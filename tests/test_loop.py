from datetime import timedelta
from uuid import uuid4

import mongomock
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from chargeops.api import app
from chargeops.config import settings
from chargeops.domain import Sample, normalize_webhook, now, window_evidence
from chargeops.storage import telemetry_pipeline
from chargeops.worker import manage_run, process_event, tick


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("SIMULATOR_SECRET", "s" * 32)
    monkeypatch.setenv("WEBHOOK_SECRET", "w" * 32)
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    settings.cache_clear()
    db = mongomock.MongoClient(tz_aware=True).chargeops
    db.incidents.create_index(
        [("run_id", 1), ("connector_id", 1), ("kind", 1)],
        unique=True,
        partialFilterExpression={"unresolved": True},
    )
    app.state.db = db
    return db


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
    evidence.update(_id="window", run_id=run_id, quality=quality)
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
    rows = list(store.telemetry.aggregate(telemetry_pipeline(run_id, at + timedelta(seconds=1))))
    assert len(rows) == 1
    assert rows[0]["residual"] == pytest.approx(12)


def test_datadog_mode_never_opens_incident_without_webhook(store, monkeypatch):
    from unittest.mock import Mock

    from chargeops.domain import block_end

    run_id, at = setup_alert(store)
    end = block_end(at)
    store.windows.delete_many({})
    for i in range(12):
        store.telemetry.insert_one(
            {
                "sample_id": str(i),
                "meta": {"run_id": run_id, "connector_id": "connector-1"},
                "measured_at": end - timedelta(seconds=60 - i * 5),
                "received_at": end,
                "current_a": 60,
                "ambient_temperature_c": 25,
                "connector_temperature_c": 51.4,
            }
        )
    monkeypatch.setattr("chargeops.worker.now", lambda: end + timedelta(seconds=3))
    stats = Mock()
    tick(store, stats, Mock())
    assert store.windows.find_one()["residual_mean"] == pytest.approx(12)
    assert store.incidents.count_documents({}) == 0
    assert stats.increment.call_count == 3
    # Only three newest samples, not all 12 samples in the rolling window.
    assert stats.increment.call_args_list[2].args == ("chargeops.samples", 3)
    tick(store, stats, Mock())
    assert stats.increment.call_count == 3  # restart/tick does not double export a block


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
