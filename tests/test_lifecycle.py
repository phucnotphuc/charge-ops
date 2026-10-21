"""CONTRACT §3 incident lifecycle: commands, derating evidence, retest, timeouts, recurrence."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from chargeops.api import app
from chargeops.domain import now, target_current
from chargeops.lifecycle import advance_incidents, expire_commands, on_alert, open_incident, process_commands

OPERATOR = {"X-Operator-Secret": "o" * 32}
SIMULATOR = {"X-Simulator-Secret": "s" * 32}
RUN = "00000000-0000-4000-8000-000000000001"


def window(store, end, current=30.0, limit=30.0, residual=1.0, requested=60.0, quality=True):
    samples = [
        {
            "measured_at": end - timedelta(seconds=60 - 5 * i),
            "current_a": current,
            "current_limit_a": limit,
            "requested_current_a": requested,
            "residual": residual,
        }
        for i in range(12)
    ]
    store.windows.insert_one(
        {
            "_id": f"{RUN}:{end.isoformat()}",
            "run_id": RUN,
            "start": end - timedelta(seconds=60),
            "end": end,
            "quality": quality,
            "residual_mean": residual,
            "samples": samples,
        }
    )


@pytest.fixture
def flow(store, monkeypatch):
    """Active run with an open incident, a clock shared by API and worker, and command helpers."""
    t = now().replace(microsecond=0)  # MongoDB stores milliseconds
    clock = [t]
    monkeypatch.setattr("chargeops.api.now", lambda: clock[0])
    store.runs.insert_one(
        {"_id": RUN, "status": "active", "mode": "local", "started_at": t - timedelta(minutes=7)}
    )
    window(store, t - timedelta(seconds=10), current=60, limit=200, residual=10)  # pre-derating reference
    open_incident(
        store, {"_id": RUN}, {"end": t - timedelta(seconds=10)}, "local", t - timedelta(seconds=5), "w"
    )
    client = TestClient(app)

    def at(seconds):
        clock[0] = t + timedelta(seconds=seconds)
        return clock[0]

    def command(kind, seconds, key=None):
        response = client.post(
            f"/api/v1/incidents/{incident()['_id']}/commands",
            json={"type": kind},
            headers={**OPERATOR, "Idempotency-Key": key or f"{kind}-{seconds}"},
        )
        assert response.status_code == 202
        process_commands(store, at(seconds))
        return response.json()["command_id"]

    def simulate(seconds):  # simulator claims and applies dispatched commands
        at(seconds)
        for item in client.get("/api/v1/simulator/commands", headers=SIMULATOR).json():
            client.post(
                f"/api/v1/simulator/commands/{item['_id']}/result",
                json={"status": "applied"},
                headers=SIMULATOR,
            )

    def incident():
        return store.incidents.find_one()

    return t, at, command, simulate, incident, client


def events(incident):
    return [e["event"] for e in incident["timeline"]]


def test_target_is_absolute_half_of_reference_or_limit():
    assert target_current(200, 60) == 30  # halving a 200 A limit would not reduce a 60 A load
    assert target_current(80, 200) == 40


def test_full_loop_open_to_resolved(store, flow):
    t, at, command, simulate, incident, _ = flow
    command("acknowledge", 0)
    assert incident()["state"] == "acknowledged"
    command("reduce_load", 1)
    assert incident()["state"] == "mitigating"
    assert (
        incident()["reference_current_a"],
        incident()["original_limit_a"],
        incident()["target_current_a"],
    ) == (
        60,
        200,
        30,
    )
    simulate(5)  # ACK at t+5
    assert store.runs.find_one()["sim"] == {"current_limit_a": 30}

    window(store, t + timedelta(seconds=70), residual=5)  # load reduced, still hot
    advance_incidents(store, at(70))
    assert incident()["state"] == "mitigating" and incident()["load_reduction_verified_at"]
    window(store, t + timedelta(seconds=85), residual=2)
    advance_incidents(store, at(85))
    assert incident()["state"] == "mitigated"

    command("maintenance", 90)
    simulate(92)
    advance_incidents(store, at(93))
    assert incident()["maintenance_applied_at"]
    command("verify_recovery", 95)
    simulate(97)
    advance_incidents(store, at(98))
    assert incident()["state"] == "verifying"
    assert store.runs.find_one()["sim"] == {"current_limit_a": 200, "fault_cleared": True}

    plan = {160: {}, 175: {"current": 40}, 190: {}, 205: {"quality": False}, 220: {}, 235: {"residual": 4}}
    for end in range(160, 311, 15):
        window(store, t + timedelta(seconds=end), **{"current": 60, "limit": 200, **plan.get(end, {})})
    advance_incidents(store, at(311))
    final = incident()
    assert final["state"] == "resolved" and "unresolved" not in final
    assert final["resolved_at"] == t + timedelta(seconds=310)  # 120 s of passing windows from t+190
    resets = [e["reason"] for e in final["timeline"] if e["event"] == "retest_reset"]
    assert resets == ["low_load", "insufficient_data", "hot"]


@pytest.mark.parametrize(
    "quality, expected", [(True, "load_reduction_ineffective"), (False, "load_reduction_unverified")]
)
def test_ack_without_lower_current_is_not_mitigated(store, flow, quality, expected):
    t, at, command, simulate, incident, client = flow
    command("acknowledge", 0)
    first = command("reduce_load", 1)
    expire_commands(store, at(31))
    assert store.commands.find_one({"_id": first})["status"] == "timed_out"
    late = client.post(
        f"/api/v1/simulator/commands/{first}/result", json={"status": "applied"}, headers=SIMULATOR
    )
    assert late.json() == {"status": "late"}
    expire_commands(store, at(36))
    assert "late_command_result" in events(incident())

    retry = command("reduce_load", 40)  # retry reuses the frozen target, never halves again
    assert store.commands.find_one({"_id": retry})["sim"] == {"current_limit_a": 30}
    assert incident()["target_current_a"] == 30
    simulate(42)  # ACK at t+42, but the vehicle keeps drawing 60 A under the old limit
    window(store, t + timedelta(seconds=110), current=60, limit=200, residual=2, quality=quality)
    advance_incidents(store, at(161))
    assert incident()["state"] == "mitigating"
    advance_incidents(store, at(162))  # 120 s after ACK
    assert incident()["reduction_check"] == expected
    assert events(incident()).count(expected) == 1


def test_idempotency_auth_and_invalid_state(store, flow):
    _, at, command, _, incident, client = flow
    url = f"/api/v1/incidents/{incident()['_id']}/commands"
    assert client.post(url, json={"type": "acknowledge"}, headers={"Idempotency-Key": "k"}).status_code == 401
    first = client.post(
        url, json={"type": "acknowledge"}, headers={**OPERATOR, "Idempotency-Key": "k"}
    ).json()
    again = client.post(
        url, json={"type": "acknowledge"}, headers={**OPERATOR, "Idempotency-Key": "k"}
    ).json()
    assert first["command_id"] == again["command_id"]
    conflict = client.post(url, json={"type": "reduce_load"}, headers={**OPERATOR, "Idempotency-Key": "k"})
    assert conflict.status_code == 409
    maintenance = command("maintenance", 1)  # the pending acknowledge is processed first
    assert store.commands.find_one({"_id": maintenance})["error"] == "invalid_state:acknowledged"
    assert incident()["state"] == "acknowledged"


def test_alert_during_retest_reopens_but_stale_alert_does_not(store, flow):
    t, *_ = flow
    store.incidents.update_one({}, {"$set": {"state": "verifying", "verifying_since": t}})
    run = {"_id": RUN}
    assert on_alert(store, run, {}, "datadog", t, "old", t - timedelta(seconds=1)) == "incident_existing"
    assert on_alert(store, run, {}, "datadog", t, "new", t + timedelta(seconds=10)) == "incident_reopened"
    found = store.incidents.find_one()
    assert found["state"] == "open" and found["timeline"][-1]["event"] == "recurrence"
    assert found["source"] == "local"  # recurrence does not rewrite the original source


def reduce_and_ack(command, simulate):
    command("acknowledge", 0)
    command("reduce_load", 1)
    simulate(5)  # ACK at t+5; target 30 A


def test_maintenance_unlocked_after_ineffective_derating(store, flow):
    """CONTRACT §3: derating verified but residual not recovered after 120 s lets maintenance run."""
    t, at, command, simulate, incident, _ = flow
    reduce_and_ack(command, simulate)
    window(store, t + timedelta(seconds=70), residual=5)  # 30 A, still hot
    advance_incidents(store, at(70))
    assert incident()["load_reduction_verified_at"] == t + timedelta(seconds=70)
    early = command("maintenance", 80)
    assert store.commands.find_one({"_id": early})["error"] == "invalid_state:mitigating"
    window(store, t + timedelta(seconds=115), residual=5)
    advance_incidents(store, at(125))  # 120 s after ACK
    found = incident()
    assert found["state"] == "mitigating" and found["reduction_check"] == "load_reduction_ineffective"
    assert found["timeline"][-1]["reason"] == "residual_not_recovered"
    maintenance = command("maintenance", 130)
    assert store.commands.find_one({"_id": maintenance})["sim"] == {"fault_cleared": True}
    simulate(135)
    advance_incidents(store, at(136))
    assert incident()["state"] == "mitigating" and incident()["maintenance_applied_at"]
    rejected = command("verify_recovery", 137)  # the retest gate is unchanged: mitigated first
    assert store.commands.find_one({"_id": rejected})["error"] == "invalid_state:mitigating"
    window(store, t + timedelta(seconds=205), residual=1)
    advance_incidents(store, at(206))
    assert incident()["state"] == "mitigated" and incident()["maintenance_applied_at"]
    command("verify_recovery", 210)
    simulate(212)
    advance_incidents(store, at(213))
    assert incident()["state"] == "verifying"


def test_unverified_derating_does_not_unlock_maintenance(store, flow):
    t, at, command, simulate, incident, _ = flow
    reduce_and_ack(command, simulate)
    window(store, t + timedelta(seconds=70), residual=5, quality=False)
    advance_incidents(store, at(125))
    assert incident()["reduction_check"] == "load_reduction_unverified"
    rejected = command("maintenance", 130)
    assert store.commands.find_one({"_id": rejected})["error"] == "invalid_state:mitigating"


def test_tripped_window_never_verifies_mitigation(store, flow):
    """A trip (0 A) is below the target and cool, but it is an interruption, not effective derating."""
    t, at, command, simulate, incident, _ = flow
    reduce_and_ack(command, simulate)
    window(store, t + timedelta(seconds=70), current=0, residual=1)
    advance_incidents(store, at(70))
    assert incident()["state"] == "mitigating" and not incident().get("load_reduction_verified_at")
    window(store, t + timedelta(seconds=85), residual=1)
    store.windows.update_one(
        {"end": t + timedelta(seconds=85)}, {"$set": {"samples.0.connector_status": "faulted"}}
    )
    advance_incidents(store, at(85))
    assert incident()["state"] == "mitigating" and not incident().get("load_reduction_verified_at")
    window(store, t + timedelta(seconds=100), residual=1)
    advance_incidents(store, at(100))
    assert incident()["state"] == "mitigated"
    assert incident()["load_reduction_verified_at"] == t + timedelta(seconds=100)
