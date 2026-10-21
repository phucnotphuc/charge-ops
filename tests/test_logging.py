import json
import logging
import re

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


def test_trace_fields_inside_span(captured):
    log, records = captured
    with tracer.trace("unit.test") as span:
        event(log, "traced", incident_id="i1")
        span_id = str(span.span_id)
    row = records[-1]
    assert row["incident_id"] == "i1"
    # Datadog links logs to traces only with a 32-char lowercase hex id or a decimal below 2**64.
    trace_id = row["dd.trace_id"]
    assert re.fullmatch(r"[0-9a-f]{32}", trace_id) or (trace_id.isdecimal() and int(trace_id) < 2**64)
    assert row["dd.span_id"] == span_id
    assert all(value for key, value in row.items() if key.startswith("dd."))
    event(log, "after")
    assert not any(key.startswith("dd.") for key in records[-1])
