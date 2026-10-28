"""B8: Datadog App action API. Secret-only auth, worker-mirroring gates, actor recorded."""

import pytest
from fastapi.testclient import TestClient

from chargeops.actions import allowed_commands
from chargeops.actions import router as datadog_router
from chargeops.api import app
from chargeops.config import Settings, settings
from chargeops.domain import now

SECRET = "a" * 32
ACTION = {"X-Action-Secret": SECRET}
OPERATOR = {"X-Operator-Secret": "o" * 32}
RUN = "00000000-0000-4000-8000-0000000000a1"
URL_A = "/api/v1/integrations/datadog/actions"
URL_I = "/api/v1/integrations/datadog/incidents"


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setenv("DATADOG_ACTION_SECRET", SECRET)
    settings.cache_clear()
    store.runs.insert_one({"_id": RUN, "status": "active", "mode": "datadog", "created_at": now()})
    return TestClient(app)


def add_incident(store, incident_id="inc-1", state="open", run_id=RUN, unresolved=True, **extra):
    doc = {
        "_id": incident_id,
        "run_id": run_id,
        "connector_id": "connector-1",
        "station_id": "station-1",
        "kind": "thermal_anomaly",
        "state": state,
        "opened_at": now(),
        **extra,
    }
    if unresolved:
        doc["unresolved"] = True
    store.incidents.insert_one(doc)
    return incident_id


def post(client, incident_id, command, key="k1", headers=ACTION):
    h = dict(headers)
    if key:
        h["Idempotency-Key"] = key
    return client.post(URL_A, json={"incident_id": incident_id, "command": command}, headers=h)


def test_missing_or_wrong_secret_is_401(client, store):
    add_incident(store)
    assert post(client, "inc-1", "acknowledge", headers={}).status_code == 401
    assert post(client, "inc-1", "acknowledge", headers={"X-Action-Secret": "b" * 32}).status_code == 401
    assert client.get(URL_I).status_code == 401
    assert client.get(URL_I, headers={"X-Action-Secret": ("é" * 16).encode("utf-8")}).status_code == 401


def test_empty_configured_secret_is_503_even_with_empty_header(store, monkeypatch):
    monkeypatch.delenv("DATADOG_ACTION_SECRET", raising=False)
    monkeypatch.setenv("DATADOG_ACTION_SECRET", "")
    settings.cache_clear()
    c = TestClient(app)
    assert post(c, "x", "acknowledge", headers={"X-Action-Secret": ""}).status_code == 503
    r = c.get(URL_I, headers={"X-Action-Secret": ""})
    assert r.status_code == 503 and r.json()["error"]["code"] == "actions_disabled"


def test_operator_credentials_never_authenticate_actions(client, store):
    add_incident(store)
    assert post(client, "inc-1", "acknowledge", headers=OPERATOR).status_code == 401
    assert client.get(URL_I, headers=OPERATOR).status_code == 401
    store.sessions.insert_one({"_id": "d", "username": "operator", "expires_at": now().replace(year=2099)})
    client.cookies.set("chargeops_session", "whatever")
    origin = {"Origin": "http://localhost:8080"}
    assert post(client, "inc-1", "acknowledge", headers=origin).status_code == 401
    assert client.get(URL_I, headers=origin).status_code == 401


def test_unknown_incident_404_and_validation(client, store):
    assert post(client, "nope", "acknowledge").status_code == 404
    add_incident(store)
    assert post(client, "inc-1", "acknowledge", key=None).status_code == 422
    assert post(client, "inc-1", "inject_fault").status_code == 422
    assert post(client, "inc-1", "acknowledge", key="k" * 201).status_code == 422
    extra = client.post(
        URL_A,
        json={"incident_id": "inc-1", "command": "acknowledge", "x": 1},
        headers={**ACTION, "Idempotency-Key": "z"},
    )
    assert extra.status_code == 422


def test_gate_violation_is_409(client, store):
    add_incident(store, state="open")
    for command in ("reduce_load", "maintenance", "verify_recovery"):
        r = post(client, "inc-1", command, key=command)
        assert r.status_code == 409 and r.json()["error"]["code"] == "command_not_allowed"
    assert store.commands.count_documents({}) == 0


def test_command_created_with_actor_and_idempotent(client, store):
    add_incident(store)
    first = post(client, "inc-1", "acknowledge")
    assert first.status_code == 202
    again = post(client, "inc-1", "acknowledge")
    assert again.status_code == 202 and again.json()["command_id"] == first.json()["command_id"]
    command = store.commands.find_one({"_id": first.json()["command_id"]})
    assert command["actor"] == "datadog-app"
    assert command["validated"] is False and command["status"] == "pending"
    assert store.commands.count_documents({}) == 1
    assert post(client, "inc-1", "reduce_load", key="k1").status_code == 409


def test_replay_after_state_changed_returns_same_command(client, store):
    add_incident(store)
    first = post(client, "inc-1", "acknowledge")
    store.incidents.update_one({"_id": "inc-1"}, {"$set": {"state": "acknowledged"}})
    again = post(client, "inc-1", "acknowledge")
    assert again.status_code == 202 and again.json()["command_id"] == first.json()["command_id"]


def test_operator_route_keeps_working_with_operator_actor(client, store):
    add_incident(store)
    r = client.post(
        "/api/v1/incidents/inc-1/commands",
        json={"type": "acknowledge"},
        headers={**OPERATOR, "Idempotency-Key": "op-1"},
    )
    assert r.status_code == 202
    assert store.commands.find_one({"_id": r.json()["command_id"]})["actor"] == "operator"


def test_incidents_list_only_unresolved_of_active_run(client, store):
    add_incident(store, "a", "acknowledged")
    add_incident(store, "done", "resolved", unresolved=False)
    store.runs.insert_one({"_id": "old", "status": "completed", "mode": "datadog", "created_at": now()})
    add_incident(store, "other", "open", run_id="old")
    items = client.get(URL_I, headers=ACTION).json()["items"]
    assert [i["incident_id"] for i in items] == ["a"]
    assert items[0]["allowed_commands"] == ["reduce_load"]
    assert items[0]["connector_id"] == "connector-1" and items[0]["station_id"] == "station-1"
    assert items[0]["ui_url"] == "/incidents/a"
    assert set(items[0]) == {"incident_id", "connector_id", "station_id", "state", "allowed_commands", "ui_url"}


def test_incidents_empty_without_active_run(store, monkeypatch):
    monkeypatch.setenv("DATADOG_ACTION_SECRET", SECRET)
    settings.cache_clear()
    assert TestClient(app).get(URL_I, headers=ACTION).json() == {"items": []}


@pytest.mark.parametrize(
    ("incident", "expected"),
    [
        ({"state": "open"}, ["acknowledge"]),
        ({"state": "acknowledged"}, ["reduce_load"]),
        ({"state": "mitigating"}, ["reduce_load"]),
        ({"state": "mitigating", "load_reduction_verified_at": 1}, []),
        (
            {"state": "mitigating", "load_reduction_verified_at": 1, "reduction_check_reason": "residual_not_recovered"},
            ["maintenance"],
        ),
        ({"state": "mitigating", "reduction_check_reason": "load_not_reduced"}, ["reduce_load"]),
        ({"state": "mitigated"}, ["maintenance"]),
        ({"state": "mitigated", "maintenance_applied_at": 1}, ["maintenance", "verify_recovery"]),
        ({"state": "resolved"}, []),
    ],
)
def test_allowed_commands_mirror_worker_gates(incident, expected):
    assert allowed_commands(incident) == expected


def test_only_two_action_routes_and_secret_length_validated():
    paths = {r.path for r in datadog_router.routes}
    assert paths == {URL_A, URL_I}
    base = {"simulator_secret": "s" * 24, "webhook_secret": "w" * 24, "operator_secret": "o" * 24}
    with pytest.raises(ValueError):
        Settings(_env_file=None, datadog_action_secret="short", **base)
    assert Settings(_env_file=None, datadog_action_secret="", **base).datadog_action_secret.get_secret_value() == ""
