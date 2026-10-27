from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from chargeops.api import app
from chargeops.config import settings
from chargeops.domain import Sample, lag, normalize_webhook, now, window_evidence, with_residuals
from chargeops.model import DEMO_PROFILE, PROFILES, physics
from chargeops.storage import window_rows
from chargeops.worker import manage_run, process_event, tick


def rows(end, residual=12):
    return [
        {"sample_id": str(i), "measured_at": end - timedelta(seconds=60 - i * 5), "residual": residual}
        for i in range(12)
    ]


def test_window_blocks_and_gap():
    end = now()
    assert window_evidence(rows(end), end)["quality"]
    assert not window_evidence(rows(end)[3:], end)["quality"]
    assert not window_evidence([], end)["quality"]
    assert window_evidence(rows(end, -2), end)["residual_mean"] == -2


def test_replayed_sessions_baseline_tracks_load_and_derating_recovers_fault():
    start = now()

    def simulate(profile, scenario, commands):
        rows, temperature, sim = [], 25.0, {}  # cold start at ambient
        for t in range(0, 1200, 5):
            sim.update(commands.get(t, {}))
            demand, current, _, _, steady = physics(t, scenario, sim, profile)
            temperature = lag(temperature, steady, 5) if t else temperature
            rows.append(
                {
                    "measured_at": start + timedelta(seconds=t),
                    "current_a": current,
                    "requested_current_a": demand,
                    "ambient_temperature_c": 25.0,
                    "connector_temperature_c": temperature,
                }
            )
        return with_residuals(rows)

    def mean(rows, end):  # 60-second window ending at t=end
        window = [r["residual"] for r in rows if 0 < end - (r["measured_at"] - start).total_seconds() <= 60]
        return sum(window) / len(window)

    # Real load curves (ramps, tapers) plus derating and restore never alarm on a healthy connector.
    for profile in PROFILES.values():
        healthy = simulate(profile, "healthy", {300: {"current_limit_a": 40}, 600: {"current_limit_a": 250}})
        assert max(abs(r["residual"]) for r in healthy) < 1e-9

    demo = PROFILES[DEMO_PROFILE]  # ~130 A; target = min(0.5 × 250, 0.5 × 129.8) ≈ 65 A
    fault = simulate(
        demo,
        "thermal_fault",
        {360: {"current_limit_a": 65}, 600: {"fault_cleared": True}, 630: {"current_limit_a": 250}},
    )
    assert mean(fault, 330) > 8  # alerting before derating
    assert mean(fault, 420) > 3  # thermal lag: one window after derating is not yet recovered
    assert mean(fault, 570) < 3  # a quarter of the 10.5 °C fault heat remains at half current
    assert all(
        r["residual"] < 3 and r["requested_current_a"] > 0.9 * 129.8 for r in fault[126:]
    )  # retest from t=630


def test_reject_nonfinite_and_naive_timestamp():
    values = dict(
        sample_id=uuid4(),
        run_id=uuid4(),
        measured_at=now(),
        current_a=60,
        voltage_v=400,
        power_kw=24,
        requested_current_a=60,
        current_limit_a=200,
        connector_temperature_c=40,
        ambient_temperature_c=25,
    )
    with pytest.raises(ValidationError):
        Sample(**{**values, "current_a": float("nan")})
    with pytest.raises(ValidationError):
        Sample(**{**values, "measured_at": now().replace(tzinfo=None)})


def payload(run_id, at, event_id="event-1", status="alert"):
    return {
        "event_id": event_id,
        "monitor_id": "123",
        "run_id": run_id,
        "connector_id": "connector-1",
        "occurred_at": at.isoformat(),
        "status": status,
    }


def setup_alert(store, quality=True):
    at = now()
    run_id = str(uuid4())
    store.runs.insert_one(
        {"_id": run_id, "mode": "datadog", "status": "active", "started_at": at - timedelta(minutes=3)}
    )
    evidence = window_evidence(rows(at), at)
    evidence.update(_id="window", run_id=run_id, connector_id="connector-1", quality=quality)
    store.windows.insert_one(evidence)
    return run_id, at


def test_webhook_retry_single_incident_and_recovery_not_close(store):
    run_id, at = setup_alert(store)
    client = TestClient(app)
    headers = {"X-Webhook-Secret": "w" * 32}
    body = payload(run_id, at)
    assert client.post("/api/v1/webhooks/datadog", json=body).status_code == 401
    assert client.post("/api/v1/webhooks/datadog", json=body, headers=headers).json()["status"] == "accepted"
    assert client.post("/api/v1/webhooks/datadog", json=body, headers=headers).json()["status"] == "duplicate"
    item = store.inbox.find_one()
    process_event(store, item, at + timedelta(seconds=2), "123")
    process_event(store, item, at + timedelta(seconds=3), "123")  # crash before marking inbox processed
    assert store.incidents.count_documents({}) == 1
    recovery = payload(run_id, at + timedelta(seconds=5), "event-2", "recovery")
    client.post("/api/v1/webhooks/datadog", json=recovery, headers=headers)
    process_event(store, store.inbox.find_one({"_id": "event-2"}), at + timedelta(seconds=6), "123")
    assert store.incidents.find_one()["state"] == "open"


@pytest.mark.parametrize("failure", ["stopped", "quality", "monitor", "old"])
def test_invalid_events_cannot_create_incident(store, failure):
    run_id, at = setup_alert(store, quality=failure != "quality")
    if failure == "stopped":
        store.runs.update_one({"_id": run_id}, {"$set": {"status": "stopped"}})
    if failure == "old":
        store.monitor_state.insert_one(
            {"_id": f"{run_id}:connector-1:123", "occurred_at": at + timedelta(seconds=5)}
        )
    body = normalize_webhook(payload(run_id, at)).model_dump(mode="python")
    body.update(_id="event-1", run_id=run_id)
    process_event(store, body, at + timedelta(seconds=2), "wrong" if failure == "monitor" else "123")
    assert store.incidents.count_documents({}) == 0


def test_missing_identity_quarantined(store):
    client = TestClient(app)
    response = client.post(
        "/api/v1/webhooks/datadog", json={"message": "incomplete"}, headers={"X-Webhook-Secret": "w" * 32}
    )
    assert response.json()["status"] == "quarantined"
    assert store.quarantine.count_documents({}) == 1
    assert store.inbox.count_documents({}) == 0


def test_datadog_variable_mapping():
    run_id = str(uuid4())
    result = normalize_webhook(
        {
            "event_id": "dd-event",
            "monitor_id": "123",
            "scope": f"run_id:{run_id},connector_id:connector-1",
            "date_ms": "1791993600000",
            "transition": "Triggered",
        }
    )
    assert str(result.run_id) == run_id
    assert result.status == "alert"


def test_ingest_validates_whole_batch_and_aggregation_deduplicates(store):
    run_id, at = setup_alert(store)
    client = TestClient(app)
    headers = {"X-Simulator-Secret": "s" * 32}
    sample = {
        "sample_id": str(uuid4()),
        "run_id": run_id,
        "connector_id": "connector-1",
        "measured_at": at.isoformat(),
        "current_a": 60,
        "voltage_v": 400,
        "power_kw": 24,
        "requested_current_a": 60,
        "current_limit_a": 200,
        "connector_temperature_c": 51.4,
        "ambient_temperature_c": 25,
    }
    bad = {**sample, "run_id": str(uuid4())}
    assert (
        client.post("/api/v1/telemetry/batch", json={"samples": [sample, bad]}, headers=headers).status_code
        == 422
    )
    assert store.telemetry.count_documents({}) == 0
    for _ in range(2):
        assert (
            client.post("/api/v1/telemetry/batch", json={"samples": [sample]}, headers=headers).status_code
            == 202
        )
    rows = window_rows(store, run_id, at + timedelta(seconds=1), "connector-1")
    assert len(rows) == 1
    assert rows[0]["residual"] == pytest.approx(51.4 - 25)  # cold start: expected begins at ambient


def test_datadog_mode_never_opens_incident_without_webhook(store, monkeypatch):
    from unittest.mock import Mock

    from chargeops.domain import block_end

    run_id, at = setup_alert(store)
    end = block_end(at)
    store.windows.delete_many({})
    samples = [
        {
            "sample_id": str(i),
            "meta": {"run_id": run_id, "connector_id": "connector-1"},
            "measured_at": end - timedelta(seconds=60 - i * 5),
            "received_at": end,
            "current_a": 60,
            "current_limit_a": 250,
            "ambient_temperature_c": 25,
            "connector_temperature_c": 51.4,
        }
        for i in range(12)
    ]
    expected = with_residuals([dict(s) for s in samples])
    store.telemetry.insert_many(samples)
    monkeypatch.setattr("chargeops.worker.now", lambda: end + timedelta(seconds=3))
    stats = Mock()
    tick(store, stats, Mock())
    assert store.windows.find_one({"connector_id": "connector-1"})["residual_mean"] == pytest.approx(
        sum(r["residual"] for r in expected) / 12
    )
    assert store.incidents.count_documents({}) == 0
    assert stats.increment.call_count == 3 * 6  # three counters per connector of the fleet
    # Only three newest samples, not all 12 samples in the rolling window; connector-1 is exported first.
    assert stats.increment.call_args_list[2].args == ("chargeops.samples", 3)
    assert "connector_id:connector-1" in stats.increment.call_args_list[2].kwargs["tags"]
    gauges = {c.args[0]: c.args[1] for c in stats.gauge.call_args_list[:6]}  # connector-1 first
    assert gauges["chargeops.current_limit_a"] == 250
    assert gauges["chargeops.expected_temperature_c"] == pytest.approx(
        sum(r["expected_temperature_c"] for r in expected[-3:]) / 3
    )
    tick(store, stats, Mock())
    assert stats.increment.call_count == 3 * 6  # restart/tick does not double export a block


def test_run_budget_survives_pending_restart_and_rejects_twenty_first(store, monkeypatch):
    monkeypatch.setenv("DD_MONITOR_ID", "123")
    settings.cache_clear()
    run_id = str(uuid4())
    previous = [str(uuid4()) for _ in range(19)]
    store.budgets.insert_one({"_id": "hackathon-2026", "run_ids": previous + [run_id]})
    store.runs.insert_one({"_id": run_id, "mode": "datadog", "status": "pending"})
    manage_run(store, now())
    assert store.runs.find_one()["status"] == "active"
    assert len(store.budgets.find_one()["run_ids"]) == 20
    store.runs.update_one({"_id": run_id}, {"$set": {"status": "stopped"}})
    another = str(uuid4())
    store.runs.insert_one({"_id": another, "mode": "datadog", "status": "pending"})
    manage_run(store, now())
    assert store.runs.find_one({"_id": another})["reason"] == "trial_run_budget_exhausted"


def test_missing_monitor_rejects_start_without_consuming_budget(store, monkeypatch):
    monkeypatch.setenv("DD_MONITOR_ID", "")  # override a configured local .env
    store.runs.insert_one({"_id": str(uuid4()), "mode": "datadog", "status": "pending"})
    manage_run(store, now())
    assert store.runs.find_one()["reason"] == "configure_dd_monitor_id_first"
    assert store.budgets.count_documents({}) == 0


def test_manifest_documents_current_and_power_noise(store, monkeypatch):
    from chargeops.storage import initialize

    monkeypatch.setattr(type(store), "create_collection", lambda *a, **k: None)  # no time-series in mongomock
    initialize(store)
    fields = store.sources.find_one({})["fields"]
    for name in ("current_a", "power_kw"):
        assert "σ 0.5 % of current, floored at 0" in fields[name] and "SIM_REALISM=true" in fields[name]
    assert "σ 0.2" in fields["connector_temperature_c"]
