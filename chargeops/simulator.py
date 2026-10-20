import math
import time
from datetime import datetime
from uuid import UUID, uuid5

import httpx

from chargeops.config import settings
from chargeops.domain import AMBIENT_C, THERMAL_K, lag, now
from chargeops.logging import event, logger

# Ground truth: simulator-only, never read by the detector. Calibration knobs.
DEMAND_A = 60.0
ORIGINAL_LIMIT_A = 200.0
FAULT_START_S = 120
FAULT_RAMP_S = 60
FAULT_FACTOR = 0.7  # extra I²R heating at full fault: residual ≈10.1 °C at 60 A, ≈2.5 °C at 30 A


def physics(elapsed, scenario, sim):
    """Current, applied limit and steady-state temperature; `sim` holds applied commands."""
    limit = sim.get("current_limit_a", ORIGINAL_LIMIT_A)
    current = min(DEMAND_A, limit)
    fault = 0.0
    if scenario == "thermal_fault" and elapsed >= FAULT_START_S and not sim.get("fault_cleared"):
        fault = FAULT_FACTOR * (1 - math.exp(-(elapsed - FAULT_START_S) / FAULT_RAMP_S))
    return current, limit, AMBIENT_C + THERMAL_K * current**2 * (1 + fault)


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
                        current, limit, steady = physics(elapsed, run["scenario"], run.get("sim") or {})
                        if thermal.get("run_id") != run["_id"]:
                            last = run.get("last_sample")
                            thermal = {
                                "run_id": run["_id"],
                                "temperature": last["connector_temperature_c"] if last else steady,
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
                            "voltage_v": 400.0,
                            "power_kw": 0.4 * current,
                            "requested_current_a": DEMAND_A,
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
