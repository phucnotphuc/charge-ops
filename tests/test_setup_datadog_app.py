"""B8: Datadog App provisioning (App Builder + HTTP connection) through httpx.MockTransport."""

import importlib.util
import json
from pathlib import Path

import httpx

spec = importlib.util.spec_from_file_location("setup_datadog", Path(__file__).parents[1] / "scripts" / "setup_datadog.py")
sd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sd)

TUNNEL = "https://abc.trycloudflare.com"
ACTION_SECRET = "z" * 32


def app_client(handler):
    return httpx.Client(base_url="https://api.datadoghq.com", transport=httpx.MockTransport(handler))


def components(app):
    """Children of the root grid: every one must be a gridCell, whose own children are the real widgets."""
    cells = app["components"][0]["properties"]["children"]
    assert cells and all(c["type"] == "gridCell" for c in cells)
    return [child for cell in cells for child in cell["properties"]["children"]]


# Documented App Builder event names/types (Create App API reference).
EVENT_NAMES = {
    "pageChange", "tableRowClick", "_tableRowButtonClick", "change", "submit", "click",
    "toggleOpen", "close", "open", "executionFinished",
}
EVENT_TYPES = {
    "custom", "setComponentState", "triggerQuery", "openModal", "closeModal", "openUrl", "downloadFile",
    "setStateVariableValue",
}


def test_app_json_follows_documented_schema():
    import uuid

    app = sd.app_definition(TUNNEL, "conn-1")
    names = {q["name"] for q in app["queries"]}
    ids = [q["id"] for q in app["queries"]]
    assert len(set(ids)) == len(ids)
    for query_id in ids:
        assert str(uuid.UUID(query_id)) == query_id
    events = [e for q in app["queries"] for e in q["events"]] + [e for c in components(app) for e in c.get("events", [])]
    assert events
    for e in events:
        assert e["name"] in EVENT_NAMES and e["type"] in EVENT_TYPES
        assert e["queryName"] in names
    assert {e["name"] for q in app["queries"] for e in q["events"]} == {"executionFinished"}
    assert all(c["name"] == "click" for comp in components(app) if comp["type"] == "button" for c in comp["events"])


def provision(handler, state):
    return sd.provision_app(app_client(handler), state, TUNNEL, ACTION_SECRET, lambda: None, "datadoghq.com")


def test_app_definition_substitutes_and_has_four_buttons_and_no_secret():
    app = sd.app_definition(TUNNEL, "conn-1")
    text = json.dumps(app)
    assert "__" not in text and ACTION_SECRET not in text
    assert TUNNEL + "/api/v1/integrations/datadog/incidents" in text
    posts = [q for q in app["queries"] if q["properties"]["spec"]["inputs"]["verb"] == "POST"]
    commands = sorted(q["properties"]["spec"]["inputs"]["body"]["command"] for q in posts)
    assert commands == ["acknowledge", "maintenance", "reduce_load", "verify_recovery"]
    for q in posts:
        assert q["properties"]["onlyTriggerManually"] is True
        assert q["properties"]["spec"]["inputs"]["url"] == TUNNEL + "/api/v1/integrations/datadog/actions"
    buttons = [c for c in components(app) if c["type"] == "button"]
    assert len(buttons) == 4 and all("allowed_commands" in b["properties"]["isDisabled"] for b in buttons)


def test_provision_happy_path_creates_then_updates_without_leaking(capsys):
    seen, state = [], {}

    def handler(request):
        seen.append((request.method, request.url.path, request.content.decode()))
        if request.method == "POST" and request.url.path == "/api/v2/actions/connections":
            return httpx.Response(201, json={"data": {"id": "conn-1"}})
        if request.method == "POST" and request.url.path == "/api/v2/app-builder/apps":
            return httpx.Response(201, json={"data": {"id": "app-1", "type": "appDefinitions"}})
        return httpx.Response(200, json={"data": {"id": "x"}})

    assert provision(handler, state)
    assert state["app_connection"] == "conn-1" and state["app"] == "app-1"
    conn = json.loads(seen[0][2])["data"]["attributes"]["integration"]
    assert conn["base_url"] == TUNNEL and conn["type"] == "HTTP"
    assert any(t["value"] == ACTION_SECRET for t in conn["credentials"]["tokens"])
    assert any(h["name"] == "X-Action-Secret" for h in conn["credentials"]["headers"])
    assert ACTION_SECRET not in capsys.readouterr().out
    seen.clear()
    assert provision(handler, state)
    assert [m for m, _, _ in seen[:2]] == ["PATCH", "PATCH"]
    assert seen[0][1].endswith("/actions/connections/conn-1") and seen[1][1].endswith("/apps/app-1")
    assert seen[-1][0] == "POST" and seen[-1][1].endswith("/apps/app-1/deployment")


def test_provision_falls_back_to_manual_import_on_404(capsys):
    def handler(request):
        return httpx.Response(404, text=f"nope {ACTION_SECRET}")

    state = {}
    assert provision(handler, state) is False
    out = capsys.readouterr().out
    assert "Import JSON" in out and "datadog_app.json" in out and "X-Action-Secret" in out
    assert ACTION_SECRET not in out and "app" not in state


def test_any_4xx_falls_back_and_continues(capsys):
    def handler(request):
        return httpx.Response(400, text=f"bad {ACTION_SECRET}")

    state = {}
    assert provision(handler, state) is False
    out = capsys.readouterr().out
    assert "HTTP 400" in out and "Import JSON" in out and ACTION_SECRET not in out


def test_stale_stored_ids_are_cleared_and_recreated_once():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        if request.method == "PATCH":
            return httpx.Response(404, json={})
        if request.url.path == "/api/v2/actions/connections":
            return httpx.Response(201, json={"data": {"id": "conn-2"}})
        if request.url.path == "/api/v2/app-builder/apps":
            return httpx.Response(201, json={"data": {"id": "app-2"}})
        return httpx.Response(200, json={})

    state = {"app_connection": "conn-old", "app": "app-old"}
    assert provision(handler, state)
    assert state["app_connection"] == "conn-2" and state["app"] == "app-2"
    assert seen[:4] == [
        ("PATCH", "/api/v2/actions/connections/conn-old"),
        ("POST", "/api/v2/actions/connections"),
        ("PATCH", "/api/v2/app-builder/apps/app-old"),
        ("POST", "/api/v2/app-builder/apps"),
    ]


def test_persistent_404_does_not_loop():
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(404, json={})

    state = {"app_connection": "conn-old"}
    assert provision(handler, state) is False
    assert calls == ["PATCH", "POST"] and "app_connection" not in state
