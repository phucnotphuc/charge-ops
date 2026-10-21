"""Read-only view of committed offline evaluation summaries (data/evaluation/<id>/summary.json).

Written by `python -m chargeops.evaluation run`; simulation only, never a live result."""

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

RESULTS_DIR = Path(__file__).resolve().parents[1] / "data" / "evaluation"
ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


class Stats(BaseModel):
    n: int
    median: float | None
    min: float | None
    max: float | None


class Detection(BaseModel):
    recall_before_trip: int
    detected_any: int
    detected_before_trip_among_b_tripped: int
    latency_s: Stats
    lead_time_s: Stats
    pre_onset_false: int


class ArmFault(Detection):
    effective_mitigation_s: Stats
    mitigated_s: Stats
    maintenance_applied: int
    resolved: int
    final_states: dict[str, int]
    trips: int
    seeds_tripped: int
    interruption_min: float
    kwh: float
    kwh_delta_vs_b: float
    kwh_delta_while_derated: float


class ArmHealthy(BaseModel):
    runs_with_incident: int
    incidents: int
    false_alerts_per_connector_hour: float | None
    trips: int
    kwh: float
    kwh_delta_vs_b: float
    by_scenario: dict[str, int]


class Arm(BaseModel):
    fault: ArmFault
    healthy: ArmHealthy
    duplicate_commands: int
    rejected_commands: int
    commands: int


class Detector(Detection):
    healthy_runs_alerting: int
    healthy_alert_episodes_per_connector_hour: float | None
    healthy_by_scenario: dict[str, int]


class Locked(BaseModel):
    a_threshold_c: float
    c_alert_c: float
    c_recovered_c: float
    locked_at: str


class Targets(BaseModel):
    c_recall_before_trip_ge_18_of_20: bool
    c_healthy_runs_with_incident_le_1: bool
    duplicate_commands_zero: bool


class EvaluationItem(BaseModel):
    id: str
    protocol_version: str
    fingerprint: str
    locked: Locked
    fault_seeds: int
    healthy_seeds: int
    b_tripped_seeds: int
    runs: int
    runtime_s: float
    targets: Targets
    simulation_only: Literal[True] = True


class EvaluationOut(EvaluationItem):
    healthy_connector_hours: float
    arms: dict[Literal["A", "B", "C"], Arm]
    detectors: dict[Literal["fixed", "residual"], Detector]
    summary_markdown: str | None


class EvaluationList(BaseModel):
    items: list[EvaluationItem]
    next_cursor: None = None


def load(evaluation_id, root=None):
    """Committed summary by directory name, or None. Only names matching ID are looked up."""
    root = Path(root or RESULTS_DIR)
    if not ID.fullmatch(evaluation_id) or evaluation_id.startswith("."):
        return None
    path = root / evaluation_id / "summary.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    markdown = path.with_name("summary.md")
    data.update(
        id=evaluation_id,
        summary_markdown=markdown.read_text(encoding="utf-8") if markdown.is_file() else None,
    )
    return EvaluationOut.model_validate(data)


def list_all(root=None):
    root = Path(root or RESULTS_DIR)
    if not root.is_dir():
        return []
    found = [load(p.name, root) for p in sorted(root.iterdir()) if p.is_dir()]
    return [EvaluationItem.model_validate(e.model_dump()) for e in found if e]
