import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from ddtrace import tracer

from chargeops.config import settings


def logger(service):
    log = logging.getLogger(service)
    if not log.handlers:
        directory = Path(settings().log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        log.setLevel(logging.INFO)
        log.addHandler(logging.StreamHandler())
        log.addHandler(logging.FileHandler(directory / f"{service}.log", encoding="utf-8"))
    return log


def trace_fields():
    # Datadog's log-trace correlation attributes; empty outside an active span.
    span = tracer.current_span()
    if span is None:
        return {}
    fields = {"dd.trace_id": str(span.trace_id), "dd.span_id": str(span.span_id)}
    for key, name in (("dd.service", "DD_SERVICE"), ("dd.env", "DD_ENV"), ("dd.version", "DD_VERSION")):
        if os.environ.get(name):
            fields[key] = os.environ[name]
    return fields


def event(log, name, **fields):
    # Datadog uses `timestamp` as the log date; `component` avoids clashing with the Agent's service tag.
    at = datetime.now(UTC).isoformat(timespec="milliseconds")
    row = {"timestamp": at, "component": log.name, "event": name, **fields, **trace_fields()}
    log.info(json.dumps(row, default=str))
