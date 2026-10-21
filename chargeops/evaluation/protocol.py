"""Pre-registered evaluation protocol (docs/EVALUATION_PROTOCOL.md). Changing any value changes the fingerprint,
so `run` refuses a config locked under different rules."""

import hashlib
import json
from pathlib import Path

from chargeops import lifecycle
from chargeops.domain import AMBIENT_C, BASELINE_VERSION, RUN_MAX_S, THERMAL_K, THERMAL_TAU_S
from chargeops.evaluation.rules import ALERT_C
from chargeops.model import ORIGINAL_LIMIT_A, draw_exogenous

PROTOCOL_VERSION = "b1-eval-v1"
DURATION_S = RUN_MAX_S  # 30 simulated minutes per run, the product's run cap
TRIP_C = 100.0  # simulation-only protective trip on true connector temperature
RESTART_C = 60.0
LOCKOUT_S = 300
OPERATOR_DELAY_S = 30  # A and C: reaction time before each operator command
MAINTENANCE_AFTER_OPEN_S = 600  # A and C: technician available 10 minutes after the incident opens
SIM_APPLY_S = 5  # simulator applies a dispatched command at its next 5-second poll
RANGES = {
    "ambient_c": (18.0, 30.0),
    "drift_c": (0.0, 1.5),
    "k_scale": (0.95, 1.05),
    "tau_scale": (0.9, 1.1),
    "change_s": (300, 1200),
    "load_factor": (1.2, 1.6),
    "load_cap_a": 200.0,
    "drop_factor": (0.2, 0.5),
    "onset_s": (300, 900),
    "severity_c": (10.0, 80.0),
    "ramp_s": (60.0, 600.0),
    "noise_c": 0.3,
}
HEALTHY_MIX = ["healthy"] * 8 + ["load_increase"] * 6 + ["demand_drop"] * 6
SEEDS = {
    "tuning": {"fault": list(range(1000, 1020)), "healthy": list(range(1020, 1040))},
    "evaluation": {"fault": list(range(2000, 2020)), "healthy": list(range(2020, 2040))},
}
FALSE_ALERT_BUDGET = 1  # healthy runs with at least one incident, per 20 healthy runs (PLAN target)
A_GRID = [x / 2 for x in range(60, 201)]  # 30.0 to 100.0 °C in 0.5 °C steps


def scenarios(split):
    """(seed, kind, scenario) for a split; healthy scenarios are assigned by position, not by RNG."""
    seeds = SEEDS[split]
    return [(s, "fault", "thermal_fault") for s in seeds["fault"]] + [
        (s, "healthy", scenario) for s, scenario in zip(seeds["healthy"], HEALTHY_MIX, strict=True)
    ]


def exogenous(seed, scenario):
    return draw_exogenous(seed, scenario, DURATION_S, RANGES)


def definition():
    profiles = Path(__file__).resolve().parent.parent / "profiles" / "autosun_v3.csv"
    return {
        "protocol_version": PROTOCOL_VERSION,
        "duration_s": DURATION_S,
        "trip_c": TRIP_C,
        "restart_c": RESTART_C,
        "lockout_s": LOCKOUT_S,
        "operator_delay_s": OPERATOR_DELAY_S,
        "maintenance_after_open_s": MAINTENANCE_AFTER_OPEN_S,
        "sim_apply_s": SIM_APPLY_S,
        "ranges": RANGES,
        "healthy_mix": HEALTHY_MIX,
        "seeds": SEEDS,
        "false_alert_budget": FALSE_ALERT_BUDGET,
        "a_grid": [A_GRID[0], A_GRID[-1], A_GRID[1] - A_GRID[0]],
        "product": {
            "baseline_version": BASELINE_VERSION,
            "ambient_c": AMBIENT_C,
            "thermal_k": THERMAL_K,
            "thermal_tau_s": THERMAL_TAU_S,
            "original_limit_a": ORIGINAL_LIMIT_A,
            "alert_c": ALERT_C,
            "recovered_c": lifecycle.RECOVERED_C,
            "current_tolerance": lifecycle.CURRENT_TOLERANCE,
            "load_floor": lifecycle.LOAD_FLOOR,
            "retest_s": lifecycle.RETEST_S,
            "reduction_check_s": lifecycle.REDUCTION_CHECK_S,
            "command_timeout_s": lifecycle.COMMAND_TIMEOUT_S,
        },
        "profiles_sha256": hashlib.sha256(profiles.read_bytes()).hexdigest(),
    }


def fingerprint():
    return hashlib.sha256(json.dumps(definition(), sort_keys=True).encode()).hexdigest()
