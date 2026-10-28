import json

import httpx
import pytest

from chargeops.datadog_client import DatadogClient, DatadogError


def client(handler):
    return DatadogClient("api-key-123", "app-key-456", "ap1.datadoghq.com", transport=httpx.MockTransport(handler))


def capture(status=200, body=None):
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["query"] = dict(request.url.params)
        seen["body"] = json.loads(request.content) if request.content else None
        return httpx.Response(status, json=body if body is not None else {})

    return seen, handler


def test_create_incident_sends_keys_and_returns_ids():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"data": {"id": "abc", "attributes": {"public_id": 42}}})

    out = client(handler).create_incident("ChargeOps connector-4 quá nhiệt", "inc-1")
    assert out == {"id": "abc", "public_id": 42}
    assert seen["url"].startswith("https://api.ap1.datadoghq.com/api/v2/incidents")
    assert seen["headers"]["DD-API-KEY"] == "api-key-123"
    assert seen["headers"]["DD-APPLICATION-KEY"] == "app-key-456"
    assert "inc-1" in json.dumps(seen["body"])


def test_error_message_never_leaks_keys_or_body():
    def handler(request):
        return httpx.Response(403, json={"errors": ["Forbidden"], "echo": "api-key-123"})

    with pytest.raises(DatadogError) as info:
        client(handler).create_incident("t", "inc-1")
    assert info.value.status == 403
    assert "api-key-123" not in str(info.value) and "app-key-456" not in str(info.value)


def test_retries_on_429_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(201, json={"data": {"id": "x", "attributes": {"public_id": 1}}})

    assert client(handler).create_incident("t", "inc-1")["id"] == "x"
    assert len(calls) == 3


def test_http_date_retry_after_falls_back_to_backoff(monkeypatch):
    monkeypatch.setattr("chargeops.datadog_client.time.sleep", lambda s: None)
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 2:
            return httpx.Response(429, headers={"Retry-After": "Wed, 28 Oct 2026 07:28:00 GMT"})
        return httpx.Response(201, json={"data": {"id": "x", "attributes": {"public_id": 1}}})

    assert client(handler).create_incident("t", "inc-1")["id"] == "x"


def test_incident_url():
    assert client(lambda r: httpx.Response(200)).incident_url(42) == "https://app.ap1.datadoghq.com/incidents/42"


def test_incident_note_creates_a_completed_todo():
    seen, handler = capture(201, {"data": {"id": "t1"}})
    client(handler).incident_note("dd-1", "acknowledge step")
    assert (seen["method"], seen["path"]) == ("POST", "/api/v2/incidents/dd-1/relationships/todos")
    data = seen["body"]["data"]
    assert data["type"] == "incident_todos"
    assert data["attributes"]["content"] == "acknowledge step"
    assert data["attributes"]["completed"]


def test_resolve_incident_patches_state():
    seen, handler = capture(200, {"data": {}})
    client(handler).resolve_incident("dd-1")
    assert (seen["method"], seen["path"]) == ("PATCH", "/api/v2/incidents/dd-1")
    data = seen["body"]["data"]
    assert data["id"] == "dd-1" and data["type"] == "incidents"
    assert data["attributes"]["fields"]["state"] == {"type": "dropdown", "value": "resolved"}


def test_find_incident_searches_by_chargeops_id():
    body = {"data": {"attributes": {"incidents": [{"data": {"id": "dd-9", "attributes": {"public_id": 5, "title": "X [inc-1]"}}}]}}}
    seen, handler = capture(200, body)
    assert client(handler).find_incident("inc-1") == {"id": "dd-9", "public_id": 5}
    assert seen["path"] == "/api/v2/incidents/search" and "inc-1" in seen["query"]["query"]


def test_find_incident_returns_none_when_missing():
    seen, handler = capture(200, {"data": {"attributes": {"incidents": []}}})
    assert client(handler).find_incident("inc-1") is None


def test_attach_link_posts_link_attachment():
    seen, handler = capture(201, {"data": {}})
    client(handler).attach_link("dd-1", "https://example.test/nb/1", "Notebook")
    assert (seen["method"], seen["path"]) == ("POST", "/api/v2/incidents/dd-1/attachments")
    attributes = seen["body"]["data"]["attributes"]
    assert attributes["attachment_type"] == "link"
    assert attributes["attachment"] == {"documentUrl": "https://example.test/nb/1", "title": "Notebook"}


def test_create_notebook_returns_id():
    seen, handler = capture(200, {"data": {"id": 77}})
    assert client(handler).create_notebook({"name": "PM inc-1", "cells": []}) == 77
    assert (seen["method"], seen["path"]) == ("POST", "/api/v1/notebooks")
    assert "PM inc-1" in json.dumps(seen["body"])


def test_update_notebook_puts_payload():
    seen, handler = capture(200, {"data": {"id": 77}})
    client(handler).update_notebook(77, {"name": "PM inc-1 v2", "cells": []})
    assert (seen["method"], seen["path"]) == ("PUT", "/api/v1/notebooks/77")
    assert "PM inc-1 v2" in json.dumps(seen["body"])


def test_find_incident_ignores_non_matching_titles():
    body = {"data": {"attributes": {"incidents": [{"data": {"id": "o", "attributes": {"public_id": 1, "title": "X [inc-10]"}}}]}}}
    seen, handler = capture(200, body)
    assert client(handler).find_incident("inc-1") is None


def test_post_is_not_retried_on_5xx_or_read_timeout():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503)

    with pytest.raises(DatadogError) as info:
        client(handler).create_incident("t", "inc-1")
    assert info.value.status == 503 and len(calls) == 1

    def timeout(request):
        calls.append(1)
        raise httpx.ReadTimeout("slow")

    with pytest.raises(DatadogError):
        client(timeout).create_incident("t", "inc-1")
    assert len(calls) == 2


def test_post_retries_connect_errors_and_get_retries_5xx():
    calls = []

    def handler(request):
        calls.append(request.method)
        if len(calls) < 2:
            raise httpx.ConnectError("down")
        return httpx.Response(201, json={"data": {"id": "x", "attributes": {"public_id": 1}}})

    assert client(handler).create_incident("t", "inc-1")["id"] == "x"
    assert len(calls) == 2
    gets = []

    def flaky(request):
        gets.append(1)
        if len(gets) < 2:
            return httpx.Response(503, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"data": {"attributes": {"incidents": []}}})

    assert client(flaky).find_incident("inc-1") is None
    assert len(gets) == 2


@pytest.mark.parametrize("response", [httpx.Response(201, content=b"<html>"), httpx.Response(201, json={"oops": 1})])
def test_malformed_success_response_becomes_datadog_error(response):
    with pytest.raises(DatadogError) as info:
        client(lambda r: response).create_incident("t", "inc-1")
    assert info.value.status == 201


def test_retries_must_be_positive():
    with pytest.raises(ValueError):
        DatadogClient("a", "b", "datadoghq.com", retries=0)


def test_incident_note_uses_step_time():
    from datetime import UTC, datetime

    seen, handler = capture(201, {"data": {}})
    client(handler).incident_note("dd-1", "x", at=datetime(2026, 10, 28, 3, 0, tzinfo=UTC))
    assert seen["body"]["data"]["attributes"]["completed"].startswith("2026-10-28T03:00:00")


def test_find_notebook_matches_name_exactly():
    seen, handler = capture(body={"data": [
        {"id": 5, "attributes": {"name": "Điều tra connector-4 [inc-10]"}},
        {"id": 6, "attributes": {"name": "Điều tra connector-4 [inc-1]"}},
    ]})
    assert client(handler).find_notebook("inc-1", "Điều tra connector-4 [inc-1]") == 6
    assert seen["method"] == "GET" and seen["path"] == "/api/v1/notebooks"
    assert seen["query"]["query"] == "[inc-1]" and seen["query"]["include_cells"] == "false"
    seen2, handler2 = capture(body={"data": []})
    assert client(handler2).find_notebook("inc-1", "x") is None
