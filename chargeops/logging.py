import json
import logging
from datetime import UTC, datetime
from pathlib import Path

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


def event(log, name, **fields):
    # Datadog uses `timestamp` as the log date; `component` avoids clashing with the Agent's service tag.
    at = datetime.now(UTC).isoformat(timespec="milliseconds")
    log.info(json.dumps({"timestamp": at, "component": log.name, "event": name, **fields}, default=str))
