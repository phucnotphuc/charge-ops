import json
import logging
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
    # tracer.get_log_correlation_context() renders the trace id in Datadog's log format (32-char hex).
    if tracer.current_span() is None:
        return {}
    return {key: value for key, value in tracer.get_log_correlation_context().items() if value}


def event(log, name, **fields):
    # Datadog uses `timestamp` as the log date; `component` avoids clashing with the Agent's service tag.
    at = datetime.now(UTC).isoformat(timespec="milliseconds")
    row = {"timestamp": at, "component": log.name, "event": name, **fields, **trace_fields()}
    log.info(json.dumps(row, default=str))
