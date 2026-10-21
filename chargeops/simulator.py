import time
from datetime import datetime
from uuid import UUID, uuid5

import httpx

from chargeops.config import settings
from chargeops.domain import AMBIENT_C, CONNECTORS, lag, now
from chargeops.fleet import fleet_plan, physics
from chargeops.logging import event, logger
from chargeops.model import DEMO_PROFILE, PROFILES, STEP_S, replay  # noqa: F401  (re-exported)


class Simulator:
    """Talks to the API only (simulator secret): commands and fault events are acknowledged there, and the API
    applies them to the target connector's device state in `run.sim[connector_id]`."""

    def __init__(self, client, log=None):
        self.client, self.log = client, log or logger("simulator")
        self.pending = None  # unsent batch, retried unchanged (same sample_ids) until the API answers
        self.thermal = {}

    def apply_commands(self):
        commands = self.client.get("/api/v1/simulator/commands")
        commands.raise_for_status()
        for command in commands.json():
            # Absolute values make re-applying harmless.
            result = self.client.post(
                f"/api/v1/simulator/commands/{command['_id']}/result", json={"status": "applied"}
            )
            result.raise_for_status()
            event(
                self.log,
                "command_applied",
                command_id=command["_id"],
                type=command["type"],
                connector_id=command.get("connector_id"),
            )

    def consume_events(self):
        events = self.client.get("/api/v1/simulator/events")
        events.raise_for_status()
        for item in events.json():
            result = self.client.post(f"/api/v1/simulator/events/{item['_id']}/consume")
            result.raise_for_status()
            event(self.log, "fault_injected", event_id=item["_id"], connector_id=item["connector_id"])

    def samples(self, run, measured):
        started = datetime.fromisoformat(run["started_at"])
        elapsed = (measured - started).total_seconds()
        plans = fleet_plan(run.get("seed") or 0, run["scenario"], run.get("profile_id"))
        if self.thermal.get("run_id") != run["_id"]:
            # Cold start at plug-in; a restarted simulator resumes each connector from its last stored sample.
            last = run.get("last_samples") or {}
            self.thermal = {"run_id": run["_id"]}
            for cid in CONNECTORS:
                stored = last.get(cid)
                self.thermal[cid] = (
                    (stored["connector_temperature_c"], datetime.fromisoformat(stored["measured_at"]))
                    if stored
                    else (AMBIENT_C, measured)
                )
        step = int(elapsed // STEP_S)
        out = []
        for cid in CONNECTORS:
            sim = dict((run.get("sim") or {}).get(cid) or {})
            injected = sim.pop("fault_injected_at", None)
            if injected:
                sim["fault_injected_s"] = (datetime.fromisoformat(injected) - started).total_seconds()
            demand, current, limit, voltage, steady = physics(plans[cid], elapsed, sim)
            temperature, at = self.thermal[cid]
            temperature = lag(temperature, steady, (measured - at).total_seconds())
            self.thermal[cid] = (temperature, measured)
            out.append(
                {
                    "sample_id": str(uuid5(UUID(run["_id"]), f"{cid}:{step}")),
                    "run_id": run["_id"],
                    "connector_id": cid,
                    "measured_at": measured.isoformat(),
                    "current_a": current,
                    "voltage_v": voltage,
                    "power_kw": voltage * current / 1000,
                    "requested_current_a": demand,
                    "current_limit_a": limit,
                    "connector_temperature_c": temperature,
                    "ambient_temperature_c": AMBIENT_C,
                }
            )
        return out

    def step(self, measured=None):
        """One 5-second step: apply commands, consume fault events, post all connectors in one batch."""
        self.apply_commands()
        self.consume_events()
        response = self.client.get("/api/v1/simulator/run")
        response.raise_for_status()
        run = response.json()
        if not run:
            self.pending = None
            return
        if self.pending is None or self.pending["run_id"] != run["_id"]:
            self.pending = {"run_id": run["_id"], "samples": self.samples(run, measured or now())}
        result = self.client.post("/api/v1/telemetry/batch", json={"samples": self.pending["samples"]})
        if result.status_code not in (409, 422):  # rejected batches are dropped, not retried
            result.raise_for_status()
        self.pending = None


def main():
    config = settings()
    log = logger("simulator")
    headers = {"X-Simulator-Secret": config.simulator_secret.get_secret_value()}
    with httpx.Client(base_url=config.api_url, headers=headers, timeout=10) as client:
        simulator = Simulator(client, log)
        while True:
            try:
                simulator.step()
            except httpx.HTTPError as exc:
                event(log, "ingest_retry", error_type=type(exc).__name__)
            # Wall-clock boundaries: Docker Desktop's VM timers run ~7% fast, its wall clock is corrected.
            time.sleep(5 - time.time() % 5)


if __name__ == "__main__":
    main()
