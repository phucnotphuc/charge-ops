from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import mongomock
import pytest
from pymongo.errors import PyMongoError

from chargeops.config import settings
from chargeops.observability import connector_tags, emit_heartbeat, emit_run_signals
from chargeops.worker import run_phases

T = datetime(2026, 10, 27, 3, 0, tzinfo=UTC)
RUN = "00000000-0000-4000-8000-0000000000a1"


def db_with(*incidents):
    db = mongomock.MongoClient(tz_aware=True).chargeops
    for incident in incidents:
        db.incidents.insert_one(incident)
    return db


def incident(**extra):
    base = {
        "_id": "i1",
        "run_id": RUN,
        "connector_id": "connector-2",
        "state": "mitigating",
        "state_since": T - timedelta(seconds=90),
        "unresolved": True,
        "timeline": [],
    }
    return {**base, **extra}


def test_connector_tags():
    assert connector_tags(RUN, "connector-4") == [
        f"run_id:{RUN}",
        "connector_id:connector-4",
        "station_id:station-2",
        "env:hackathon",
    ]


def test_heartbeat_is_env_tagged_only():
    stats = Mock()
    emit_heartbeat(stats)
    stats.increment.assert_called_once_with("chargeops.worker.tick", tags=["env:hackathon"])


def test_open_count_covers_every_connector_and_state_age_is_tagged():
    stats = Mock()
    emit_run_signals(db_with(incident()), stats, {"_id": RUN}, T)
    open_by = {
        c.kwargs["tags"][1]: c.args[1] for c in stats.gauge.call_args_list if c.args[0] == "chargeops.incident.open"
    }
    assert len(open_by) == 6
    assert open_by["connector_id:connector-2"] == 1 and open_by["connector_id:connector-5"] == 0
    age = [c for c in stats.gauge.call_args_list if c.args[0] == "chargeops.incident.state_age_s"]
    assert age[0].args[1] == 90.0
    assert "state:mitigating" in age[0].kwargs["tags"]


def test_resolved_incident_has_no_state_age():
    stats = Mock()
    emit_run_signals(db_with(incident(state="resolved", unresolved=None)), stats, {"_id": RUN}, T)
    assert not [c for c in stats.gauge.call_args_list if c.args[0] == "chargeops.incident.state_age_s"]


def test_other_runs_are_ignored():
    stats = Mock()
    emit_run_signals(db_with(incident(run_id="other")), stats, {"_id": RUN}, T)
    assert all(c.args[1] == 0 for c in stats.gauge.call_args_list if c.args[0] == "chargeops.incident.open")
    stats.event.assert_not_called()


def test_timeline_events_are_sent_exactly_once():
    db = db_with(incident(timeline=[{"at": T, "event": "opened"}, {"at": T, "event": "acknowledge"}]))
    stats = Mock()
    emit_run_signals(db, stats, {"_id": RUN}, T)
    emit_run_signals(db, stats, {"_id": RUN}, T)
    assert stats.event.call_count == 2
    assert stats.event.call_args_list[0].kwargs["alert_type"] == "error"
    db.incidents.update_one({"_id": "i1"}, {"$push": {"timeline": {"at": T, "event": "resolved"}}})
    emit_run_signals(db, stats, {"_id": RUN}, T)
    assert stats.event.call_count == 3
    last = stats.event.call_args
    assert last.kwargs["alert_type"] == "success"
    assert last.kwargs["aggregation_key"] == "i1"
    assert {"incident_id:i1", "event:resolved", "source:chargeops"} <= set(last.kwargs["tags"])


def test_shrunken_timeline_resets_cursor_so_new_entries_are_sent():
    db = db_with(incident(timeline=[{"at": T, "event": "opened"}]))
    db.dd_event_cursor.insert_one({"_id": "i1", "sent": 5})
    stats = Mock()
    emit_run_signals(db, stats, {"_id": RUN}, T)
    assert stats.event.call_count == 1
    assert db.dd_event_cursor.find_one({"_id": "i1"})["sent"] == 1


def test_event_text_uses_entry_event_not_current_state():
    stats = Mock()
    resolved = incident(state="resolved", timeline=[{"at": T, "event": "opened"}])
    emit_run_signals(db_with(resolved), stats, {"_id": RUN}, T)
    text = stats.event.call_args.args[1]
    assert "opened" in text and "resolved" not in text


class BrokenCursorDb:
    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        if name == "dd_event_cursor":
            raise PyMongoError("boom")
        return getattr(self._db, name)


def test_signal_failure_does_not_starve_the_tick(store, monkeypatch):
    store.runs.insert_one(
        {"_id": RUN, "status": "active", "mode": "datadog", "stop_requested": False, "started_at": T}
    )
    store.incidents.insert_one(incident(timeline=[{"at": T, "event": "opened"}]))
    called = []
    monkeypatch.setattr("chargeops.worker.now", lambda: T)
    monkeypatch.setattr("chargeops.worker.evaluate_windows", lambda *a: None)
    monkeypatch.setattr("chargeops.worker.advance_incidents", lambda db, at: called.append("incidents"))
    run_phases(BrokenCursorDb(store), Mock(), Mock())
    assert called == ["incidents"]


@pytest.mark.parametrize("enabled", [True, False])
def test_heartbeat_only_when_enabled(store, monkeypatch, enabled):
    monkeypatch.setenv("DD_METRICS_ENABLED", "true" if enabled else "false")
    settings.cache_clear()
    monkeypatch.setattr("chargeops.worker.now", lambda: T)
    stats = Mock()
    run_phases(store, stats, Mock())
    assert stats.increment.called is enabled
    settings.cache_clear()
