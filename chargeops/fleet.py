"""Live demo fleet: 2 stations × 3 connectors, each replaying its own audited Autosun session.

The plan is ground truth (which connector is faulty, onset, severity). Only the simulator derives it, from the
run's seed; the API, worker and detector never read it."""

import random
from dataclasses import dataclass
from functools import lru_cache

from chargeops.domain import AMBIENT_C, CONNECTORS, THERMAL_K
from chargeops.evaluation.protocol import RANGES
from chargeops.model import (
    FAULT_RAMP_S,
    FAULT_RESIDUAL_C,
    ORIGINAL_LIMIT_A,
    PROFILES,
    STEP_S,
    fault_multiplier,
    replay,
    scenario_demand,
)

# Seeded live fault: severity is sized so halving the current leaves a quarter (< 3 °C), as in the
# single-connector demo calibration (FAULT_RESIDUAL_C 10.5 at 120 s).
FAULT_ONSET_S = (120, 300)
FAULT_SEVERITY_C = (9.5, 11.0)
STEADY_S = 900  # alert, derating, maintenance and the 120-second retest fit in 15 minutes after onset
STEADY_BAND = 0.08  # retest needs >= 90% of the reference current


def steady_load(profile, onset_s):
    """Demand stays within ±8% of the onset load (and >= 20 A) for STEADY_S after onset."""
    demand = [replay(profile, t)[0] for t in range(onset_s, onset_s + STEADY_S + 1, STEP_S)]
    first = demand[0]
    return first >= 20 and all(abs(d - first) <= STEADY_BAND * first for d in demand)


@dataclass(frozen=True)
class Plan:
    connector_id: str
    profile_id: str
    # This connector's own scenario: the non-faulty connectors of a thermal_fault run are healthy.
    scenario: str
    change_s: int | None
    factor: float
    drop: float
    cap_a: float
    fault_onset_s: int | None
    fault_severity_c: float | None


@lru_cache(maxsize=64)
def fleet_plan(seed, scenario, profile=None):
    """Fixed draw order for every scenario, so a seed's profiles do not depend on the scenario.

    Load-change ranges are the evaluation's (protocol RANGES); `profile` pins one Autosun session on the
    faulty connector (thermal_fault) or on connector-1."""
    rng = random.Random(seed)
    profiles = rng.sample(sorted(PROFILES), len(CONNECTORS))
    onset = rng.randrange(FAULT_ONSET_S[0], FAULT_ONSET_S[1] + 1, STEP_S)
    severity = rng.uniform(*FAULT_SEVERITY_C)
    # Heat is sized at the onset load, so a tapering session would hide the fault (or a rising one inflate it).
    stable = [c for c, p in zip(CONNECTORS, profiles, strict=True) if steady_load(PROFILES[p], onset)]
    faulty = rng.choice(stable or CONNECTORS)
    low, high = RANGES["change_s"]
    shapes = [
        (
            rng.randrange(low, high + 1, STEP_S),
            rng.uniform(*RANGES["load_factor"]),
            rng.uniform(*RANGES["drop_factor"]),
        )
        for _ in CONNECTORS
    ]
    pinned = CONNECTORS.index(faulty if scenario == "thermal_fault" else CONNECTORS[0])
    if profile is not None:
        if profile in profiles:  # keep every connector on a different session
            profiles[profiles.index(profile)] = profiles[pinned]
        profiles[pinned] = profile
    plans = {}
    for cid, profile_id, (change, factor, drop) in zip(CONNECTORS, profiles, shapes, strict=True):
        fault = scenario == "thermal_fault" and cid == faulty
        own = scenario if scenario != "thermal_fault" else ("thermal_fault" if fault else "healthy")
        plans[cid] = Plan(
            connector_id=cid,
            profile_id=profile_id,
            scenario=own,
            change_s=change if own in ("load_increase", "demand_drop") else None,
            factor=factor,
            drop=drop,
            cap_a=RANGES["load_cap_a"],
            fault_onset_s=onset if fault else None,
            fault_severity_c=severity if fault else None,
        )
    return plans


def demand_at(plan, elapsed):
    demand, voltage = replay(PROFILES[plan.profile_id], elapsed)
    if plan.change_s is not None:
        demand = scenario_demand(
            demand, elapsed, plan.scenario, plan.change_s, plan.factor, plan.drop, plan.cap_a
        )
    return demand, voltage


def physics(plan, elapsed, sim):
    """One connector: demand, current, limit, voltage and steady-state temperature.

    `sim` is this connector's device state from applied commands and consumed fault events, in elapsed
    seconds: current_limit_a, fault_cleared (any maintenance), fault_cleared_s (latest maintenance),
    fault_injected_s. Maintenance clears the seeded fault for good; an injected fault stays active unless
    maintenance was applied after it, so an injection never revives a cleared seeded fault."""
    demand, voltage = demand_at(plan, elapsed)
    limit = sim.get("current_limit_a", ORIGINAL_LIMIT_A)
    current = min(demand, limit)
    faults = []
    if plan.fault_onset_s is not None and not sim.get("fault_cleared"):
        faults.append((plan.fault_onset_s, plan.fault_severity_c))
    injected, cleared = sim.get("fault_injected_s"), sim.get("fault_cleared_s")
    if injected is not None and (cleared is None or cleared < injected):
        faults.append((injected, FAULT_RESIDUAL_C))
    heat = 0.0
    for onset, severity in faults:
        onset_a = min(demand_at(plan, onset)[0], ORIGINAL_LIMIT_A)
        heat = max(heat, fault_multiplier(elapsed, onset, severity, FAULT_RAMP_S, onset_a, THERMAL_K))
    return demand, current, limit, voltage, AMBIENT_C + THERMAL_K * current**2 * (1 + heat)
