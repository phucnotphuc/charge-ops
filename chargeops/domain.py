import math
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, FiniteFloat, field_validator

CONNECTOR = "connector-1"
MANIFEST = "autosun-replay-v1"
RUN_MAX_S = 1800
# Healthy thermal model shared by simulator and detector baseline (calibrated, not ground truth).
# k: Autosun–PolyU v3 gun-temperature fit (0.0011–0.0015). tau: real ~37 min, compressed ~37x for a 15-minute demo.
AMBIENT_C = 25.0
THERMAL_K = 0.0013  # °C per A² at steady state
THERMAL_TAU_S = 60.0
BASELINE_VERSION = "ema-tau60-k0013-v2"


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


def target_current(original_limit_a, reference_current_a):
    """Absolute derating limit (CONTRACT §3): 200 A limit with 60 A actual load gives 30 A."""
    return min(0.5 * original_limit_a, 0.5 * reference_current_a)


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["acknowledge", "reduce_load", "maintenance", "verify_recovery"]


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["applied", "failed"]
    result: str = Field(default="", max_length=500)


def lag(temperature, steady, dt):
    """First-order thermal response over dt seconds."""
    return temperature + (steady - temperature) * (1 - math.exp(-dt / THERMAL_TAU_S))


def with_residuals(rows):
    """Expected temperature lags ambient + k·I² over actual sample spacing; rows sorted, deduplicated.

    Cold start: the gun is at ambient when the session begins, so history must begin at run start.
    A gun already hot at plug-in therefore shows as positive residual instead of being absorbed."""
    expected = previous = None
    for row in rows:
        steady = row["ambient_temperature_c"] + THERMAL_K * row["current_a"] ** 2
        if expected is None:
            expected = row["ambient_temperature_c"]
        else:
            expected = lag(expected, steady, (row["measured_at"] - previous).total_seconds())
        previous = row["measured_at"]
        row["expected_temperature_c"] = expected
        row["residual"] = row["connector_temperature_c"] - expected
    return rows


class SampleOut(BaseModel):
    sample_id: str
    run_id: str
    station_id: str | None = None
    charger_id: str | None = None
    connector_id: str
    measured_at: AwareDatetime
    received_at: AwareDatetime | None = None
    voltage_v: float | None = None
    current_a: float | None = None
    power_kw: float | None = None
    connector_temperature_c: float | None = None
    ambient_temperature_c: float | None = None
    requested_current_a: float | None = None
    current_limit_a: float | None = None
    provenance: dict | None = None


class TelemetryItem(SampleOut):
    expected_temperature_c: float | None = None
    residual: float | None = None


class TelemetryPage(BaseModel):
    items: list[TelemetryItem]
    next_cursor: str | None = None


class ActiveIncident(BaseModel):
    id: str
    state: str


class ConnectorOut(BaseModel):
    connector_id: str
    station_id: str | None = None
    charger_id: str | None = None
    run_id: str | None = None
    run_status: str | None = None
    mode: Literal["local", "datadog"] | None = None
    latest: SampleOut | None = None
    measured_at: AwareDatetime | None = None
    freshness_s: float | None = None
    stale: bool
    active_incident: ActiveIncident | None = None


class ConnectorList(BaseModel):
    items: list[ConnectorOut]
    next_cursor: str | None = None


class DemoRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: Literal["healthy", "thermal_fault"]
    seed: int
    mode: Literal["local", "datadog"] = "datadog"
    profile: str | None = None


class DemoRunOut(BaseModel):
    id: str
    status: str
    mode: Literal["local", "datadog"]
    scenario: str
    seed: int | None = None
    profile_id: str | None = None
    baseline_version: str | None = None
    created_at: AwareDatetime
    started_at: AwareDatetime | None = None
    stopped_at: AwareDatetime | None = None
    stop_requested: bool = False
    reason: str | None = None


class FaultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector_id: Literal["connector-1"]


class FaultEventOut(BaseModel):
    event_id: str
    run_id: str
    type: str
    connector_id: str
    requested_at: AwareDatetime


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=500)


class UserOut(BaseModel):
    username: str
    role: Literal["operator"]
