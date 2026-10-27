"""Pure thermal/load model shared by the live simulator and offline evaluation.

Ground truth (fault, true temperature, connector parameters) lives here only; detectors never read it."""

import csv
import math
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from chargeops.domain import AMBIENT_C, THERMAL_K, THERMAL_TAU_S

# Calibration knobs of the live demo simulator.
ORIGINAL_LIMIT_A = 250.0  # highest current in the Autosun–PolyU v3 data
FAULT_START_S = 120
FAULT_RAMP_S = 60
FAULT_RESIDUAL_C = 10.5  # extra heat at the onset load; halving current leaves a quarter (≈2.6 °C)
DEMO_PROFILE = "44020100400000052012112300218810"  # ~130 A, stable for 92 of 97 minutes
STEP_S = 5
EPOCH = datetime(2026, 10, 21, tzinfo=UTC)  # simulated clock origin, on a 15-second boundary


def load_profiles():
    """Per-minute (current A, voltage V) of the 50 audited sessions (data/manifests/autosun-v3.json)."""
    profiles = defaultdict(list)
    with (Path(__file__).parent / "profiles" / "autosun_v3.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):  # rows are ordered by minute within each session
            profiles[row["transaction_id"]].append((float(row["current_a"]), float(row["voltage_v"])))
    return dict(profiles)


PROFILES = load_profiles()


def replay(profile, elapsed):
    """Vehicle demand and voltage, linearly interpolated between published minutes; 0 A after the session."""
    position = elapsed / 60
    minute = int(position)
    if minute >= len(profile):
        return 0.0, profile[-1][1]
    if minute == len(profile) - 1:
        return profile[minute]
    weight = position - minute
    (a0, v0), (a1, v1) = profile[minute], profile[minute + 1]
    return a0 + (a1 - a0) * weight, v0 + (v1 - v0) * weight


def scenario_demand(base, t, scenario, change_s, factor, drop, cap_a):
    """Healthy demand changes: load_increase towards min(base × factor, cap), demand_drop to base × drop."""
    weight = min(1.0, max(0.0, (t - change_s) / 60))  # 60-second ramp into the new demand
    if scenario == "load_increase":
        base += (max(base, min(base * factor, cap_a)) - base) * weight
    elif scenario == "demand_drop":
        base *= 1 - (1 - drop) * weight
    return base


def fault_multiplier(elapsed, onset_s, severity_c, ramp_s, onset_a, k):
    """Extra I²R heating sized in °C at the onset load, so severity does not depend on the session's current."""
    if elapsed < onset_s or onset_a <= 0:
        return 0.0
    full = severity_c / (k * onset_a**2)
    return full * (1 - math.exp(-(elapsed - onset_s) / ramp_s))


def physics(elapsed, scenario, sim, profile):
    """Single-connector demo calibration (fixed onset/severity); the live fleet uses chargeops.fleet.physics."""
    demand, voltage = replay(profile, elapsed)
    limit = sim.get("current_limit_a", ORIGINAL_LIMIT_A)
    current = min(demand, limit)
    fault = 0.0
    onset_a = min(replay(profile, FAULT_START_S)[0], ORIGINAL_LIMIT_A)
    if scenario == "thermal_fault" and not sim.get("fault_cleared"):
        fault = fault_multiplier(elapsed, FAULT_START_S, FAULT_RESIDUAL_C, FAULT_RAMP_S, onset_a, THERMAL_K)
    return demand, current, limit, voltage, AMBIENT_C + THERMAL_K * current**2 * (1 + fault)


def response(temperature, steady, dt, tau):
    """First-order thermal response with the connector's own tau (domain.lag uses the nominal one)."""
    return temperature + (steady - temperature) * (1 - math.exp(-dt / tau))


@dataclass(frozen=True)
class Fault:
    onset_s: int
    severity_c: float
    ramp_s: float
    onset_current_a: float


@dataclass(frozen=True)
class Exogenous:
    """Everything outside the operator's control, drawn once per seed before any run."""

    seed: int
    scenario: str
    profile_id: str
    k: float  # true connector heating, differs from the detector's nominal THERMAL_K
    tau_s: float
    change_s: int | None
    fault: Fault | None
    demand_a: tuple
    voltage_v: tuple
    ambient_c: tuple
    noise_c: tuple


def draw_exogenous(seed, scenario, duration_s, ranges):
    """Fixed draw order for every scenario, so a seed's profile/ambient/noise do not depend on the scenario."""
    rng = random.Random(seed)
    profile_id = rng.choice(sorted(PROFILES))
    ambient = rng.uniform(*ranges["ambient_c"])
    drift = rng.uniform(*ranges["drift_c"])
    phase = rng.uniform(0, 2 * math.pi)
    k = THERMAL_K * rng.uniform(*ranges["k_scale"])
    tau = THERMAL_TAU_S * rng.uniform(*ranges["tau_scale"])
    change = rng.randrange(ranges["change_s"][0], ranges["change_s"][1] + 1, STEP_S)
    factor = rng.uniform(*ranges["load_factor"])
    drop = rng.uniform(*ranges["drop_factor"])
    onset = rng.randrange(ranges["onset_s"][0], ranges["onset_s"][1] + 1, STEP_S)
    severity = rng.uniform(*ranges["severity_c"])
    ramp = rng.uniform(*ranges["ramp_s"])
    times = range(0, duration_s, STEP_S)
    noise = tuple(rng.gauss(0, ranges["noise_c"]) for _ in times)
    profile = PROFILES[profile_id]
    demand, voltage = [], []
    for t in times:
        base, volts = replay(profile, t)
        demand.append(scenario_demand(base, t, scenario, change, factor, drop, ranges["load_cap_a"]))
        voltage.append(volts)
    fault = None
    if scenario == "thermal_fault":
        fault = Fault(onset, severity, ramp, min(demand[onset // STEP_S], ORIGINAL_LIMIT_A))
    return Exogenous(
        seed=seed,
        scenario=scenario,
        profile_id=profile_id,
        k=k,
        tau_s=tau,
        change_s=change if scenario in ("load_increase", "demand_drop") else None,
        fault=fault,
        demand_a=tuple(demand),
        voltage_v=tuple(voltage),
        ambient_c=tuple(ambient + drift * math.sin(2 * math.pi * t / 3600 + phase) for t in times),
        noise_c=noise,
    )


class Connector:
    """One simulated connector: applies commands, heats, trips above `trip_c`, restarts after lockout."""

    def __init__(self, exo, trip_c, restart_c, lockout_s):
        self.exo, self.trip_c, self.restart_c, self.lockout_s = exo, trip_c, restart_c, lockout_s
        self.limit, self.fault_cleared = ORIGINAL_LIMIT_A, False
        self.temperature = self.tripped_at = None
        self.trips = []

    def apply(self, payload):
        """Absolute values, so re-applying a command is harmless (same as the live simulator)."""
        self.limit = payload.get("current_limit_a", self.limit)
        self.fault_cleared = self.fault_cleared or bool(payload.get("fault_cleared"))

    def step(self, i):
        """Telemetry sample (detector input) and ground truth (evaluation only) at step i."""
        exo, t = self.exo, i * STEP_S
        demand, ambient = exo.demand_a[i], exo.ambient_c[i]
        if (
            self.tripped_at is not None
            and t - self.tripped_at >= self.lockout_s
            and self.temperature <= self.restart_c
        ):
            self.tripped_at = None  # automatic restart with the limit in force
        tripped = self.tripped_at is not None
        current = 0.0 if tripped else min(demand, self.limit)
        fault = 0.0
        if exo.fault and not self.fault_cleared:
            f = exo.fault
            fault = fault_multiplier(t, f.onset_s, f.severity_c, f.ramp_s, f.onset_current_a, exo.k)
        steady = ambient + exo.k * current**2 * (1 + fault)
        # Cold start at ambient, like the live simulator.
        self.temperature = ambient if i == 0 else response(self.temperature, steady, STEP_S, exo.tau_s)
        if not tripped and self.temperature > self.trip_c:
            self.tripped_at = t
            self.trips.append(t)
        sample = {
            "measured_at": EPOCH + timedelta(seconds=t),
            "current_a": current,
            "voltage_v": exo.voltage_v[i],
            "power_kw": exo.voltage_v[i] * current / 1000,
            "requested_current_a": demand,
            "current_limit_a": self.limit,
            "connector_temperature_c": self.temperature + exo.noise_c[i],
            "ambient_temperature_c": ambient,
        }
        truth = {"t": t, "temperature_c": self.temperature, "fault_heat_c": exo.k * current**2 * fault}
        return sample, {**truth, "tripped": tripped}
