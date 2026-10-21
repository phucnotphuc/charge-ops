"""WP-A1: connector read API, telemetry range API, demo-run control (CONTRACT §1-2)."""

from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from chargeops.api import app
from chargeops.config import settings
from chargeops.domain import now

OPERATOR = {"X-Operator-Secret": "o" * 32}
RUN = "00000000-0000-4000-8000-000000000001"
T0 = now().replace(microsecond=0)


@pytest.fixture
def client(store):
    return TestClient(app)


def sample(measured_at, run_id=RUN, sample_id=None, received_at=None, connector="connector-1", **fields):
    document = {
        "sample_id": sample_id or str(uuid4()),
        "run_id": run_id,
        "connector_id": connector,
        "measured_at": measured_at,
        "received_at": received_at or measured_at,
        "current_a": 100.0,
        "voltage_v": 400.0,
        "power_kw": 40.0,
        "requested_current_a": 100.0,
        "current_limit_a": 250.0,
        "connector_temperature_c": 30.0,
        "ambient_temperature_c": 25.0,
        "meta": {
            "run_id": run_id,
            "connector_id": connector,
            "station_id": "station-1",
            "charger_id": "charger-1",
        },
        "provenance": {"composition": "hybrid", "manifest_id": "autosun-replay-v1"},
    }
    document.update(fields)
    return document


def active_run(store, mode="local"):
    store.runs.insert_one(
        {
            "_id": RUN,
            "status": "active",
            "mode": mode,
            "scenario": "thermal_fault",
            "created_at": T0,
            "started_at": T0 - timedelta(minutes=10),
            "active_slot": "single",
        }
    )


def test_connector_latest_uses_measured_at_not_arrival(client, store):
    active_run(store)
    newest = sample(T0 - timedelta(seconds=5), received_at=T0 - timedelta(seconds=4), current_a=111.0)
    older_but_late = sample(T0 - timedelta(seconds=20), received_at=T0, current_a=99.0)
    store.telemetry.insert_many([newest, older_but_late])
    items = client.get("/api/v1/connectors").json()["items"]
    assert [c["connector_id"] for c in items] == ["connector-1"]
    connector = items[0]
    assert connector["latest"]["current_a"] == 111.0
    assert connector["mode"] == "local" and connector["run_id"] == RUN
    assert connector["stale"] is False and 4 <= connector["freshness_s"] < 15
    assert connector["measured_at"].endswith("Z")


def test_connector_stale_after_30_seconds(client, store):
    active_run(store)
    store.telemetry.insert_one(sample(now() - timedelta(seconds=45)))
    connector = client.get("/api/v1/connectors/connector-1").json()
    assert connector["stale"] is True and connector["freshness_s"] >= 45


def test_connector_without_telemetry_is_null_not_zero(client, store):
    active_run(store)
    connector = client.get("/api/v1/connectors/connector-1").json()
    assert connector["latest"] is None and connector["freshness_s"] is None and connector["stale"] is True


def test_connector_reports_active_incident(client, store):
    active_run(store)
    store.telemetry.insert_one(sample(now()))
    store.incidents.insert_one(
        {
            "_id": "inc-1",
            "run_id": RUN,
            "connector_id": "connector-1",
            "kind": "thermal_anomaly",
            "unresolved": True,
            "state": "mitigating",
            "opened_at": T0,
        }
    )
    connector = client.get("/api/v1/connectors/connector-1").json()
    assert connector["active_incident"] == {"id": "inc-1", "state": "mitigating"}


def test_connectors_without_any_run_is_empty_and_unknown_connector_404(client, store):
    assert client.get("/api/v1/connectors").json() == {"items": [], "next_cursor": None}
    response = client.get("/api/v1/connectors/connector-9")
    assert response.status_code == 404 and response.json()["error"]["code"] == "not_found"


def window(start, end):
    return {"from": start.isoformat().replace("+00:00", "Z"), "to": end.isoformat().replace("+00:00", "Z")}


def seed_series(store, count=10):
    active_run(store)
    rows = [sample(T0 - timedelta(seconds=5 * (count - i)), current_a=100.0) for i in range(count)]
    store.telemetry.insert_many(rows)
    return rows


def test_telemetry_requires_from_and_to(client, store):
    seed_series(store)
    url = "/api/v1/connectors/connector-1/telemetry"
    assert client.get(url).status_code == 422
    assert client.get(url, params={"from": T0.isoformat()}).status_code == 422
    assert client.get(url, params={"to": T0.isoformat()}).json()["error"]["code"] == "validation_error"


def test_telemetry_range_over_24h_or_inverted_is_422(client, store):
    seed_series(store)
    url = "/api/v1/connectors/connector-1/telemetry"
    too_long = window(T0 - timedelta(hours=24, seconds=1), T0)
    assert client.get(url, params=too_long).status_code == 422
    assert client.get(url, params=window(T0 - timedelta(hours=24), T0)).status_code == 200
    assert client.get(url, params=window(T0, T0 - timedelta(minutes=1))).status_code == 422


def test_telemetry_items_have_expected_temperature_and_residual(client, store):
    seed_series(store)
    body = client.get(
        "/api/v1/connectors/connector-1/telemetry",
        params={"run_id": RUN, **window(T0 - timedelta(minutes=5), T0)},
    ).json()
    assert body["next_cursor"] is None and len(body["items"]) == 10
    first = body["items"][0]
    assert first["expected_temperature_c"] == 25.0  # cold start at ambient
    assert first["residual"] == pytest.approx(5.0)
    assert first["measured_at"].endswith("Z") and first["provenance"]["composition"] == "hybrid"
    times = [item["measured_at"] for item in body["items"]]
    assert times == sorted(times)


def test_telemetry_dedupes_by_sample_id(client, store):
    active_run(store)
    first = sample(T0 - timedelta(seconds=20), sample_id="a", received_at=T0 - timedelta(seconds=19))
    duplicate = sample(T0 - timedelta(seconds=20), sample_id="a", received_at=T0 - timedelta(seconds=10))
    store.telemetry.insert_many([first, duplicate, sample(T0 - timedelta(seconds=10), sample_id="b")])
    items = client.get(
        "/api/v1/connectors/connector-1/telemetry",
        params={"run_id": RUN, **window(T0 - timedelta(minutes=1), T0)},
    ).json()["items"]
    assert [i["sample_id"] for i in items] == ["a", "b"]


def test_telemetry_pagination_cursor(client, store):
    rows = seed_series(store, 10)
    params = {"run_id": RUN, **window(T0 - timedelta(minutes=5), T0), "limit": 4}
    url = "/api/v1/connectors/connector-1/telemetry"
    seen, cursor, pages = [], None, 0
    while True:
        page = client.get(url, params={**params, **({"cursor": cursor} if cursor else {})}).json()
        seen += [i["sample_id"] for i in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert pages == 3 and seen == [r["sample_id"] for r in rows]
    assert client.get(url, params={**params, "limit": 1001}).status_code == 422
    assert client.get(url, params={**params, "cursor": "garbage"}).status_code == 422


def test_telemetry_residual_spans_history_before_from(client, store):
    """Expected temperature must lag from run start even when `from` is later."""
    active_run(store)
    store.telemetry.insert_many(
        [
            sample(T0 - timedelta(seconds=50 - 5 * i), connector_temperature_c=60.0, current_a=150.0)
            for i in range(10)
        ]
    )
    body = client.get(
        "/api/v1/connectors/connector-1/telemetry",
        params={"run_id": RUN, **window(T0 - timedelta(seconds=10), T0)},
    ).json()
    assert len(body["items"]) == 2 and body["items"][0]["expected_temperature_c"] > 25.0


def create(client, **overrides):
    body = {"scenario": "thermal_fault", "seed": 7, "mode": "local", **overrides}
    return client.post("/api/v1/demo-runs", json=body, headers=OPERATOR)


def test_demo_run_lifecycle(client, store):
    created = create(client)
    assert created.status_code == 201
    run = created.json()
    assert run["status"] == "created" and run["seed"] == 7 and run["created_at"].endswith("Z")
    run_id = run["id"]
    assert store.runs.find_one({"_id": run_id}).get("active_slot") is None

    started = client.post(f"/api/v1/demo-runs/{run_id}/start", headers=OPERATOR)
    assert started.status_code == 202 and started.json()["status"] == "pending"
    assert store.runs.find_one({"_id": run_id})["active_slot"] == "single"  # same record shape as cli.py
    assert client.get(f"/api/v1/demo-runs/{run_id}").json()["status"] == "pending"

    stopped = client.post(f"/api/v1/demo-runs/{run_id}/stop", headers=OPERATOR)
    assert stopped.status_code == 202 and stopped.json()["stop_requested"] is True
    assert store.runs.find_one({"_id": run_id})["stop_requested"] is True


def test_start_second_run_conflicts_with_single_active_slot(client):
    first, second = create(client).json()["id"], create(client).json()["id"]
    assert client.post(f"/api/v1/demo-runs/{first}/start", headers=OPERATOR).status_code == 202
    response = client.post(f"/api/v1/demo-runs/{second}/start", headers=OPERATOR)
    assert response.status_code == 409 and response.json()["error"]["code"] == "conflict"
    assert client.get(f"/api/v1/demo-runs/{second}").json()["status"] == "created"
    assert (
        client.post(f"/api/v1/demo-runs/{first}/start", headers=OPERATOR).status_code == 409
    )  # not startable twice


def test_stop_unstarted_run_cancels_it(client):
    run_id = create(client).json()["id"]
    response = client.post(f"/api/v1/demo-runs/{run_id}/stop", headers=OPERATOR)
    assert response.status_code == 202 and response.json()["status"] == "cancelled"
    assert client.post(f"/api/v1/demo-runs/{run_id}/stop", headers=OPERATOR).status_code == 409


def test_demo_run_validation_and_unknown_id(client):
    assert create(client, scenario="nonsense").status_code == 422
    assert create(client, profile="not-a-profile").json()["error"]["code"] == "validation_error"
    assert create(client, mode="cloud").status_code == 422
    assert client.get("/api/v1/demo-runs/missing").status_code == 404
    assert client.post("/api/v1/demo-runs/missing/start", headers=OPERATOR).status_code == 404


def test_demo_run_control_requires_operator(client):
    run_id = create(client).json()["id"]
    for path in ("start", "stop", "inject-fault"):
        assert (
            client.post(
                f"/api/v1/demo-runs/{run_id}/{path}", json={"connector_id": "connector-1"}
            ).status_code
            == 401
        )


def test_datadog_budget_refuses_start(client, store, monkeypatch):
    monkeypatch.setenv("DD_MONITOR_ID", "123")
    monkeypatch.setenv("DD_TRIAL_ID", "trial-x")
    settings.cache_clear()
    store.budgets.insert_one({"_id": "trial-x", "run_ids": [str(uuid4()) for _ in range(20)]})
    run_id = create(client, mode="datadog").json()["id"]
    response = client.post(f"/api/v1/demo-runs/{run_id}/start", headers=OPERATOR)
    assert response.status_code == 409 and response.json()["error"]["code"] == "trial_run_budget_exhausted"
    assert store.runs.find_one({"_id": run_id})["status"] == "created"
    # local runs are not charged against the Datadog budget
    assert (
        client.post(f"/api/v1/demo-runs/{create(client).json()['id']}/start", headers=OPERATOR).status_code
        == 202
    )


def test_datadog_start_needs_monitor_id(client, monkeypatch):
    monkeypatch.setenv("DD_MONITOR_ID", "")
    settings.cache_clear()
    run_id = create(client, mode="datadog").json()["id"]
    response = client.post(f"/api/v1/demo-runs/{run_id}/start", headers=OPERATOR)
    assert response.status_code == 409 and response.json()["error"]["code"] == "configure_dd_monitor_id_first"


def test_inject_fault_records_run_event(client, store):
    active_run(store)
    response = client.post(
        f"/api/v1/demo-runs/{RUN}/inject-fault", json={"connector_id": "connector-1"}, headers=OPERATOR
    )
    assert response.status_code == 202
    stored = store.run_events.find_one({"run_id": RUN})
    assert stored["type"] == "inject_fault" and stored["connector_id"] == "connector-1"
    assert stored["requested_at"] and stored["consumed_at"] is None
    assert response.json()["event_id"] == stored["_id"]


def test_inject_fault_needs_active_run_and_valid_connector(client, store):
    run_id = create(client).json()["id"]
    body = {"connector_id": "connector-1"}
    assert (
        client.post(f"/api/v1/demo-runs/{run_id}/inject-fault", json=body, headers=OPERATOR).status_code
        == 409
    )
    active_run(store)
    bad = client.post(f"/api/v1/demo-runs/{RUN}/inject-fault", json={"connector_id": "x"}, headers=OPERATOR)
    assert bad.status_code == 422


def test_routes_in_openapi_with_response_models():
    spec = app.openapi()["paths"]
    expected = {
        ("/api/v1/connectors", "get"),
        ("/api/v1/connectors/{connector_id}", "get"),
        ("/api/v1/connectors/{connector_id}/telemetry", "get"),
        ("/api/v1/demo-runs", "post"),
        ("/api/v1/demo-runs/{run_id}", "get"),
        ("/api/v1/demo-runs/{run_id}/start", "post"),
        ("/api/v1/demo-runs/{run_id}/stop", "post"),
        ("/api/v1/demo-runs/{run_id}/inject-fault", "post"),
        ("/api/v1/auth/login", "post"),
        ("/api/v1/auth/logout", "post"),
        ("/api/v1/auth/me", "get"),
    }
    for path, method in expected:
        operation = spec[path][method]
        ok = [c for c in operation["responses"] if c.startswith("2")]
        assert ok, (path, method)
        if method != "post" or "logout" not in path:
            content = operation["responses"][ok[0]].get("content", {})
            assert "$ref" in content["application/json"]["schema"], (path, method)
