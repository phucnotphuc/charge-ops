import mongomock
import pytest
from pymongo.errors import PyMongoError

from chargeops import worker
from chargeops.config import settings
from chargeops.domain import now
from chargeops.worker import ChangeWaker, run_loop


def test_trial_switch_preserves_historical_budget(store, monkeypatch):
    monkeypatch.setenv("DD_TRIAL_ID", "new-trial")
    monkeypatch.setenv("DD_MONITOR_ID", "123")
    settings.cache_clear()
    old_ids = [f"old-{i}" for i in range(20)]
    store.budgets.insert_one({"_id": "old-trial", "run_ids": old_ids})
    store.runs.insert_many([
        {"_id": rid, "mode": "datadog", "status": "stopped", "started_at": now()}
        for rid in old_ids
    ])
    store.runs.insert_one({"_id": "new", "mode": "datadog", "status": "pending"})
    worker.manage_run(store, now())
    assert store.runs.find_one({"_id": "new"})["status"] == "active"
    assert store.runs.find_one({"_id": "new"})["dd_trial_id"] == "new-trial"
    assert store.budgets.find_one({"_id": "new-trial"})["run_ids"] == ["new"]
    worker.manage_run(store, now())
    assert store.budgets.find_one({"_id": "new-trial"})["run_ids"] == ["new"]


def test_reconcile_after_trial_switch_repairs_original_trial(store, monkeypatch):
    monkeypatch.setenv("DD_TRIAL_ID", "new-trial")
    settings.cache_clear()
    store.runs.insert_one({
        "_id": "old", "mode": "datadog", "status": "stopped", "started_at": now(),
        "dd_trial_id": "old-trial",
    })
    worker.manage_run(store, now())
    assert store.budgets.find_one({"_id": "old-trial"})["run_ids"] == ["old"]
    assert store.budgets.find_one({"_id": "new-trial"}) is None


class FakeStream:
    def __init__(self, changes, token="tok-1"):
        self.changes = list(changes)
        self.resume_token = {"_data": token}

    def try_next(self):
        return self.changes.pop(0) if self.changes else None

    def close(self):
        pass


class FakeDb:
    def __init__(self, stream=None, error=None):
        self.worker_state = mongomock.MongoClient(tz_aware=True).chargeops.worker_state
        self.stream = stream
        self.error = error
        self.calls = []

    def watch(self, pipeline=None, **kwargs):
        self.calls.append((pipeline, kwargs))
        if self.error:
            raise self.error
        return self.stream


class Log:
    name = "worker"

    def __init__(self):
        self.lines = []

    def info(self, line):
        self.lines.append(line)


def test_fallback_when_watch_unsupported():
    log = Log()
    waker = ChangeWaker(FakeDb(error=NotImplementedError("no watch")), log)
    waker.start()
    assert sum("change_stream_unavailable" in line for line in log.lines) == 1
    assert waker.wait(0.01) is False
    waker.stop()


def test_watch_filters_collections_and_resume_token_roundtrip():
    db = FakeDb(FakeStream([{"operationType": "insert"}]))
    waker = ChangeWaker(db, Log())
    waker.start()
    assert waker.wait(2) is True
    waker.commit()
    assert db.worker_state.find_one({"_id": "change_stream"})["token"] == {"_data": "tok-1"}
    pipeline = db.calls[0][0]
    text = str(pipeline)
    for name in ("inbox", "commands", "run_events"):
        assert name in text
    assert "telemetry" not in text
    assert "resume_after" not in db.calls[0][1]
    waker.stop()

    db2 = FakeDb(FakeStream([]))
    db2.worker_state = db.worker_state
    again = ChangeWaker(db2, Log())
    again.start()
    assert db2.calls[0][1]["resume_after"] == {"_data": "tok-1"}
    again.stop()


def test_stale_resume_token_retries_without_it():
    db = FakeDb(FakeStream([]))
    db.worker_state.insert_one({"_id": "change_stream", "token": {"_data": "old"}})
    original = db.watch

    def watch(pipeline=None, **kwargs):
        if "resume_after" in kwargs:
            raise PyMongoError("resume token not found")
        return original(pipeline, **kwargs)

    db.watch = watch
    waker = ChangeWaker(db, Log())
    waker.start()
    assert any("change_stream_resume_reset" in line for line in waker.log.lines)
    waker.stop()


def test_commit_without_new_events_writes_nothing():
    db = FakeDb(FakeStream([]))
    waker = ChangeWaker(db, Log())
    waker.start()
    waker.commit()
    assert db.worker_state.find_one({"_id": "change_stream"}) is None
    waker.stop()


def test_loop_ticks_on_timeout_without_events(monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "tick", lambda db, stats, log: calls.append(1))
    waker = ChangeWaker(FakeDb(error=NotImplementedError()), Log())
    waker.start()
    run_loop(None, None, Log(), waker, interval=0.01, min_gap=0, iterations=3)
    assert len(calls) == 3


def test_loop_wakes_early_on_event(monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "tick", lambda db, stats, log: calls.append(1))
    db = FakeDb(FakeStream([{"operationType": "insert"}]))
    waker = ChangeWaker(db, Log())
    waker.start()
    run_loop(db, None, Log(), waker, interval=30, min_gap=0, iterations=1)
    assert calls == [1]
    assert db.worker_state.find_one({"_id": "change_stream"})["token"] == {"_data": "tok-1"}
    waker.stop()


def test_loop_survives_tick_error(monkeypatch):
    calls = []

    def boom(db, stats, log):
        calls.append(1)
        raise PyMongoError("down")

    monkeypatch.setattr(worker, "tick", boom)
    waker = ChangeWaker(FakeDb(error=NotImplementedError()), Log())
    waker.start()
    log = Log()
    run_loop(None, None, log, waker, interval=0.01, min_gap=0, iterations=2)
    assert len(calls) == 2 and any("worker_retry" in line for line in log.lines)


def test_span_noop_when_tracing_disabled(monkeypatch):
    monkeypatch.delenv("DD_TRACE_ENABLED", raising=False)
    with worker.span("worker.inbox", run_id="r1") as s:
        assert s is None


def test_span_tags_when_enabled(monkeypatch):
    monkeypatch.setenv("DD_TRACE_ENABLED", "true")
    with worker.span("worker.inbox", run_id="r1", connector_id=None) as s:
        assert s.get_tag("run_id") == "r1"
        assert s.get_tag("connector_id") is None


def test_failed_tick_does_not_commit_resume_token(monkeypatch):
    def boom(db, stats, log):
        raise PyMongoError("down")

    monkeypatch.setattr(worker, "tick", boom)
    db = FakeDb(FakeStream([{"operationType": "insert"}]))
    waker = ChangeWaker(db, Log())
    waker.start()
    run_loop(db, None, Log(), waker, interval=30, min_gap=0, iterations=1)
    assert db.worker_state.find_one({"_id": "change_stream"}) is None
    waker.stop()


class RacyRuns:
    """Cancels the pending run right after the worker reads it."""

    def __init__(self, runs):
        self.runs = runs

    def find_one(self, *args, **kwargs):
        found = self.runs.find_one(*args, **kwargs)
        if found and found["status"] == "pending":
            self.runs.update_one({"_id": found["_id"]}, {"$set": {"status": "cancelled"}})
        return found

    def __getattr__(self, name):
        return getattr(self.runs, name)


class RacyDb:
    def __init__(self, db):
        self.db, self.runs = db, RacyRuns(db.runs)

    def __getattr__(self, name):
        return getattr(self.db, name)


@pytest.mark.parametrize("monitor_id", ["", "123"])
def test_cancelled_pending_run_is_not_activated_or_charged(store, monkeypatch, monitor_id):
    monkeypatch.setenv("DD_MONITOR_ID", monitor_id)
    settings.cache_clear()
    store.runs.insert_one({"_id": "r1", "status": "pending", "mode": "datadog"})
    worker.manage_run(RacyDb(store), now())
    assert store.runs.find_one({"_id": "r1"})["status"] == "cancelled"
    budget = store.budgets.find_one({"_id": settings().dd_trial_id})
    assert not budget or budget["run_ids"] == []


def test_activation_charges_budget_after_match(store, monkeypatch):
    monkeypatch.setenv("DD_MONITOR_ID", "123")
    settings.cache_clear()
    store.runs.insert_one({"_id": "r2", "status": "pending", "mode": "datadog"})
    worker.manage_run(store, now())
    assert store.runs.find_one({"_id": "r2"})["status"] == "active"
    assert store.budgets.find_one({"_id": settings().dd_trial_id})["run_ids"] == ["r2"]


def test_reconcile_charges_started_datadog_run_missing_from_budget(store, monkeypatch):
    monkeypatch.setenv("DD_MONITOR_ID", "123")
    settings.cache_clear()
    store.runs.insert_one({"_id": "r3", "status": "active", "mode": "datadog", "started_at": now()})
    store.runs.insert_one({"_id": "r4", "status": "stopped", "mode": "local", "started_at": now()})
    worker.manage_run(store, now())
    worker.manage_run(store, now())
    assert store.budgets.find_one({"_id": settings().dd_trial_id})["run_ids"] == ["r3"]
