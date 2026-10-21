import json
import logging

import pytest
from ddtrace import tracer

from chargeops.config import settings
from chargeops.logging import event, logger


@pytest.fixture
def captured(monkeypatch, tmp_path):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.setenv("SIMULATOR_SECRET", "s" * 32)
    monkeypatch.setenv("WEBHOOK_SECRET", "w" * 32)
    monkeypatch.setenv("OPERATOR_SECRET", "o" * 32)
    settings.cache_clear()
    log = logger("logtest")
    records = []

    class Grab(logging.Handler):
        def emit(self, record):
            records.append(json.loads(record.getMessage()))

    log.addHandler(Grab())
    yield log, records
    for handler in list(log.handlers):
        handler.close()
    log.handlers.clear()


def test_no_trace_fields_without_span(captured):
    log, records = captured
    event(log, "plain", run_id="r1")
    row = records[-1]
    assert row["run_id"] == "r1" and row["component"] == "logtest" and "timestamp" in row
    assert not any(key.startswith("dd.") for key in row)


def test_trace_fields_inside_span(captured, monkeypatch):
    monkeypatch.setenv("DD_SERVICE", "chargeops-worker")
    monkeypatch.setenv("DD_ENV", "hackathon")
    monkeypatch.setenv("DD_VERSION", "0.1.0")
    log, records = captured
    with tracer.trace("unit.test") as span:
        event(log, "traced", incident_id="i1")
        expected = (str(span.trace_id), str(span.span_id))
    row = records[-1]
    assert row["incident_id"] == "i1"
    assert (row["dd.trace_id"], row["dd.span_id"]) == expected
    assert row["dd.service"] == "chargeops-worker"
    assert row["dd.env"] == "hackathon" and row["dd.version"] == "0.1.0"
    event(log, "after")
    assert "dd.trace_id" not in records[-1]
