import math
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, FiniteFloat, field_validator

CONNECTOR = "connector-1"
MANIFEST = "synthetic-constant-load-v1"


def now():
    return datetime.now(UTC)


class Sample(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sample_id: UUID
    run_id: UUID
    connector_id: Literal["connector-1"] = CONNECTOR
    measured_at: AwareDatetime
    current_a: FiniteFloat = Field(ge=0)
    voltage_v: FiniteFloat = Field(ge=0)
    power_kw: FiniteFloat = Field(ge=0)
    requested_current_a: FiniteFloat = Field(ge=0)
    current_limit_a: FiniteFloat = Field(ge=0)
    connector_temperature_c: FiniteFloat
    ambient_temperature_c: FiniteFloat

    @field_validator("measured_at")
    @classmethod
    def utc(cls, value):
        return value.astimezone(UTC)


class Batch(BaseModel):
    samples: list[Sample] = Field(min_length=1, max_length=1000)


class Webhook(BaseModel):
    event_id: str = Field(min_length=1, max_length=200)
    monitor_id: str = Field(min_length=1, max_length=100)
    run_id: UUID
    connector_id: Literal["connector-1"]
    occurred_at: AwareDatetime
    status: Literal["alert", "recovery"]
    message: str = Field(default="", max_length=2000)


def normalize_webhook(raw):
    """Accept canonical payload or Datadog's documented template variables."""
    if "scope" not in raw:
        return Webhook.model_validate(raw)
    tags = dict(item.strip().split(":", 1) for item in raw["scope"].split(",") if ":" in item)
    transition = {"Triggered": "alert", "Re-Triggered": "alert", "Recovered": "recovery"}
    return Webhook.model_validate(
        {
            "event_id": raw["event_id"],
            "monitor_id": raw["monitor_id"],
            "run_id": tags.get("run_id"),
            "connector_id": tags.get("connector_id"),
            "occurred_at": datetime.fromtimestamp(int(raw["date_ms"]) / 1000, UTC),
            "status": transition[raw["transition"]],
            "message": raw.get("message", ""),
        }
    )


def block_end(at):
    return datetime.fromtimestamp(int(at.timestamp()) // 15 * 15, UTC)


def window_evidence(rows, end):
    """Rows already deduplicated by Mongo aggregation; no ground truth input."""
    rows = sorted(rows, key=lambda x: x["measured_at"])
    times = [r["measured_at"] for r in rows]
    start = end - timedelta(seconds=60)
    blocks = {int((t - start).total_seconds()) // 15 for t in times}
    gaps = [(b - a).total_seconds() for a, b in pairwise(times)]
    quality = (
        (
            len(rows) >= 10
            and blocks == {0, 1, 2, 3}
            and (end - times[-1]).total_seconds() <= 30
            and (times[0] - start).total_seconds() <= 15
            and max(gaps, default=0) <= 15
        )
        if rows
        else False
    )
    return {
        "start": start,
        "end": end,
        "count": len(rows),
        "quality": quality,
        "residual_mean": sum(r["residual"] for r in rows) / len(rows) if rows else None,
        "samples": rows,
    }


def synthetic_values(elapsed, scenario):
    # Constant load already at thermal equilibrium. Fault dynamics are simulator-only.
    ambient, current = 25.0, 60.0
    temperature = ambient + 0.004 * current**2
    if scenario == "thermal_fault" and elapsed >= 120:
        temperature += 25 * (1 - math.exp(-(elapsed - 120) / 60))
    return dict(
        current_a=current,
        voltage_v=400,
        power_kw=24,
        requested_current_a=current,
        current_limit_a=200,
        connector_temperature_c=temperature,
        ambient_temperature_c=ambient,
    )
