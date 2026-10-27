"""Public station display: what a driver needs (status, SoC, power, current, connector temperature, energy, time
to full) and nothing operational (no residual, incident ids, commands or thresholds)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from chargeops.domain import ConnectorId

PublicStatus = Literal["offline", "idle", "charging", "limited", "checking"]


class VehicleState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    connector_id: ConnectorId
    measured_at: datetime
    soc_percent: float = Field(ge=0, le=100)
    energy_kwh: float = Field(ge=0)
    minutes_to_full: float | None = Field(default=None, ge=0)
    capacity_source: Literal["autosun", "default"]


class VehicleBatch(BaseModel):
    vehicles: list[VehicleState] = Field(min_length=1, max_length=6)


class StationConnector(BaseModel):
    connector_id: str
    status: PublicStatus
    soc_percent: float | None
    power_kw: float | None
    current_a: float | None
    connector_temperature_c: float | None
    energy_kwh: float | None
    minutes_to_full: float | None
    measured_at: datetime | None


class StationOut(BaseModel):
    station_id: str
    connectors: list[StationConnector]


def public_status(connector, vehicle):
    """`connector` is the ConnectorOut dict from GET /connectors; `vehicle` the latest vehicle state or None."""
    latest = connector.get("latest") or {}
    if connector.get("stale") or not latest:
        return "offline"
    state = (connector.get("active_incident") or {}).get("state")
    if state == "verifying":
        return "checking"
    if state in ("mitigating", "mitigated") or latest.get("current_limit_a", 0) < latest.get(
        "requested_current_a", 0
    ):
        return "limited" if latest.get("current_a", 0) >= 1 else "checking"
    if latest.get("current_a", 0) < 1 or vehicle is None:
        return "idle"
    return "charging"
