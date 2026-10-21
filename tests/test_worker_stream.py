import mongomock
from pymongo.errors import PyMongoError

from chargeops import worker
from chargeops.worker import ChangeWaker, run_loop


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
    assert not waker.available
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
    assert waker.available
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
