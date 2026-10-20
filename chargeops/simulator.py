import csv
import math
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid5

import httpx

from chargeops.config import settings
from chargeops.domain import AMBIENT_C, THERMAL_K, lag, now
from chargeops.logging import event, logger

# Ground truth: simulator-only, never read by the detector. Calibration knobs.
ORIGINAL_LIMIT_A = 250.0  # highest current in the Autosun–PolyU v3 data
FAULT_START_S = 120
FAULT_RAMP_S = 60
FAULT_RESIDUAL_C = 10.5  # extra heat at the onset load; halving current leaves a quarter (≈2.6 °C)
DEMO_PROFILE = "44020100400000052012112300218810"  # ~130 A, stable for 92 of 97 minutes


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


def physics(elapsed, scenario, sim, profile):
    """Demand, current, limit, voltage and steady-state temperature; `sim` holds applied commands."""
    demand, voltage = replay(profile, elapsed)
    limit = sim.get("current_limit_a", ORIGINAL_LIMIT_A)
    current = min(demand, limit)
    fault = 0.0
    onset_a = min(replay(profile, FAULT_START_S)[0], ORIGINAL_LIMIT_A)
    if (
        scenario == "thermal_fault"
        and elapsed >= FAULT_START_S
        and onset_a > 0
        and not sim.get("fault_cleared")
    ):
        # Extra I²R heating sized in °C at the onset load, so severity does not depend on the session's current.
        full = FAULT_RESIDUAL_C / (THERMAL_K * onset_a**2)
        fault = full * (1 - math.exp(-(elapsed - FAULT_START_S) / FAULT_RAMP_S))
    return demand, current, limit, voltage, AMBIENT_C + THERMAL_K * current**2 * (1 + fault)


def main():
    config = settings()
    log = logger("simulator")
    headers = {"X-Simulator-Secret": config.simulator_secret.get_secret_value()}
    pending = None
    thermal = {}
    with httpx.Client(base_url=config.api_url, headers=headers, timeout=10) as client:
        while True:
            try:
                # Applying is the server-side state change; absolute values make re-applying harmless.
                commands = client.get("/api/v1/simulator/commands")
                commands.raise_for_status()
                for command in commands.json():
                    result = client.post(
                        f"/api/v1/simulator/commands/{command['_id']}/result", json={"status": "applied"}
                    )
                    result.raise_for_status()
                    event(log, "command_applied", command_id=command["_id"], type=command["type"])
                response = client.get("/api/v1/simulator/run")
                response.raise_for_status()
                run = response.json()
                if run:
                    measured = now()
                    elapsed = (measured - datetime.fromisoformat(run["started_at"])).total_seconds()
                    if pending is None or pending["run_id"] != run["_id"]:
                        profile = PROFILES[run.get("profile_id", DEMO_PROFILE)]
                        demand, current, limit, voltage, steady = physics(
                            elapsed, run["scenario"], run.get("sim") or {}, profile
                        )
                        if thermal.get("run_id") != run["_id"]:
                            # Cold start at plug-in; a restarted simulator resumes from the last stored sample.
                            last = run.get("last_sample")
                            thermal = {
                                "run_id": run["_id"],
                                "temperature": last["connector_temperature_c"] if last else AMBIENT_C,
                                "at": datetime.fromisoformat(last["measured_at"]) if last else measured,
                            }
                        dt = (measured - thermal["at"]).total_seconds()
                        thermal.update(temperature=lag(thermal["temperature"], steady, dt), at=measured)
                        pending = {
                            "sample_id": str(uuid5(UUID(run["_id"]), str(int(elapsed // 5)))),
                            "run_id": run["_id"],
                            "connector_id": "connector-1",
                            "measured_at": measured.isoformat(),
                            "current_a": current,
                            "voltage_v": voltage,
                            "power_kw": voltage * current / 1000,
                            "requested_current_a": demand,
                            "current_limit_a": limit,
                            "connector_temperature_c": thermal["temperature"],
                            "ambient_temperature_c": AMBIENT_C,
                        }
                    result = client.post("/api/v1/telemetry/batch", json={"samples": [pending]})
                    if result.status_code in (409, 422):
                        pending = None
                    else:
                        result.raise_for_status()
                        pending = None
                else:
                    pending = None
            except httpx.HTTPError as exc:
                event(log, "ingest_retry", error_type=type(exc).__name__)
            # Wall-clock boundaries: Docker Desktop's VM timers run ~7% fast, its wall clock is corrected.
            time.sleep(5 - time.time() % 5)


if __name__ == "__main__":
    main()
