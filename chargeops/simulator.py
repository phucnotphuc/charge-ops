import time
from datetime import datetime
from uuid import UUID, uuid5

import httpx

from chargeops.config import settings
from chargeops.domain import AMBIENT_C, CONNECTORS, LOCATION, lag, now
from chargeops.fleet import fleet_plan, physics
from chargeops.logging import event, logger
from chargeops.model import DEMO_PROFILE, PROFILES, STEP_S, replay  # noqa: F401  (re-exported)
from chargeops.realism import Imperfections, ambient_c, noisy, sample_rng
from chargeops.vehicles import SOC_PROFILES, minutes_to_full, soc_percent


class Simulator:
    """Talks to the API only (simulator secret): commands and fault events are acknowledged there, and the API
    applies them to the target connector's device state in `run.sim[connector_id]`."""

    def __init__(self, client, log=None, realism=False):
        self.client, self.log = client, log or logger("simulator")
        self.realism = realism
        self.pending = None  # unsent batch, retried unchanged (same sample_ids) until the API answers
        self.thermal = {}
        self.delivered = {}  # per run: {connector_id: (kwh, last_measured)} from the actual current
        self.last_power = {}
        self.imperfect = Imperfections() if realism else None

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
            vehicles = run.get("last_vehicles") or {}
            self.thermal = {"run_id": run["_id"]}
            self.delivered, self.last_power = {}, {}
            if self.imperfect is not None:
                self.imperfect.held = []  # a new run never inherits the previous run's late samples
            for cid in CONNECTORS:
                stored = last.get(cid)
                self.thermal[cid] = (
                    (stored["connector_temperature_c"], datetime.fromisoformat(stored["measured_at"]))
                    if stored
                    else (self.ambient(cid, measured), measured)
                )
                vehicle = vehicles.get(cid)
                self.delivered[cid] = (
                    (vehicle["energy_kwh"], datetime.fromisoformat(vehicle["measured_at"]))
                    if vehicle
                    else (0.0, measured)
                )
        step = int(elapsed // STEP_S)  # the same index the sample IDs and imperfections use
        out = []
        for cid in CONNECTORS:
            sim = dict((run.get("sim") or {}).get(cid) or {})
            for stamp, seconds in (
                ("fault_injected_at", "fault_injected_s"),
                ("fault_cleared_at", "fault_cleared_s"),
            ):
                value = sim.pop(stamp, None)
                if value:
                    sim[seconds] = (datetime.fromisoformat(value) - started).total_seconds()
            ambient = self.ambient(cid, measured)
            demand, current, limit, voltage, steady = physics(plans[cid], elapsed, sim, ambient)
            temperature, at = self.thermal[cid]
            temperature = lag(temperature, steady, (measured - at).total_seconds())
            self.thermal[cid] = (temperature, measured)
            kwh, last_at = self.delivered[cid]
            # Energy counts only after the BMS reports its first SoC (minute offset of the session's profile).
            start_s = SOC_PROFILES[plans[cid].profile_id].soc_start_minute * 60
            counted = elapsed - max(elapsed - (measured - last_at).total_seconds(), start_s)
            kwh += voltage * current / 1000 * max(counted, 0) / 3600
            self.delivered[cid] = (kwh, measured)
            self.last_power[cid] = voltage * current / 1000
            # Integration above uses the true current; only the reported values below get sensor noise.
            rng = sample_rng(run["_id"], cid, step)
            reading = noisy(temperature, 0.2, rng) if self.realism else temperature
            reported = noisy(current, 0.005 * current, rng, floor=0.0) if self.realism else current
            out.append(
                {
                    "sample_id": str(uuid5(UUID(run["_id"]), f"{cid}:{step}")),
                    "run_id": run["_id"],
                    "connector_id": cid,
                    "measured_at": measured.isoformat(),
                    "current_a": reported,
                    "voltage_v": voltage,
                    "power_kw": voltage * reported / 1000,
                    "requested_current_a": demand,
                    "current_limit_a": limit,
                    "connector_temperature_c": reading,
                    "ambient_temperature_c": ambient,
                }
            )
        return out, plans, step

    def ambient(self, connector_id, measured):
        return ambient_c(LOCATION[connector_id][0], measured) if self.realism else AMBIENT_C

    def vehicles(self, run, plans, measured):
        """Vehicle states of the step `samples()` just computed (noise-free current, so SoC is not noisy)."""
        out = []
        for cid in CONNECTORS:
            profile = SOC_PROFILES[plans[cid].profile_id]
            kwh, _ = self.delivered[cid]
            soc = soc_percent(profile, kwh)
            out.append(
                {
                    "run_id": run["_id"],
                    "connector_id": cid,
                    "measured_at": measured.isoformat(),
                    "soc_percent": round(soc, 1),
                    "energy_kwh": round(kwh, 3),
                    "minutes_to_full": minutes_to_full(profile, soc, self.last_power.get(cid, 0.0)),
                    "capacity_source": "autosun" if profile.capacity_kwh else "default",
                }
            )
        return out

    def post_vehicles(self, vehicles):
        """Best effort: a lost vehicle batch is superseded by the next step and never blocks telemetry."""
        try:
            result = self.client.post("/api/v1/simulator/vehicles", json={"vehicles": vehicles})
            result.raise_for_status()
        except httpx.HTTPError as exc:
            event(self.log, "vehicle_post_retry", error_type=type(exc).__name__)

    def step(self, measured=None):
        """One 5-second step: apply commands, consume fault events, post all connectors in one batch."""
        self.apply_commands()
        self.consume_events()
        response = self.client.get("/api/v1/simulator/run")
        response.raise_for_status()
        run = response.json()
        if run and self.thermal.get("run_id") != run["_id"]:  # (re)start: fetch each connector's last sample
            response = self.client.get("/api/v1/simulator/run", params={"resume": "true"})
            response.raise_for_status()
            run = response.json()
        if not run:
            self.pending = None
            return
        if self.pending is None or self.pending["run_id"] != run["_id"]:
            measured = measured or now()
            samples, plans, index = self.samples(run, measured)
            vehicles = self.vehicles(run, plans, measured)
            if self.imperfect is not None:  # faults hit telemetry only, never vehicle states or the integration
                samples = self.imperfect.apply(samples, run["_id"], index)
            self.pending = {"run_id": run["_id"], "samples": samples, "vehicles": vehicles}
        if not self.pending["samples"]:  # the API requires min_length=1; vehicle states still go out
            self.post_vehicles(self.pending["vehicles"])
            self.pending = None
            return
        result = self.client.post("/api/v1/telemetry/batch", json={"samples": self.pending["samples"]})
        if result.status_code not in (409, 422):  # rejected batches are dropped, not retried
            result.raise_for_status()
            self.post_vehicles(self.pending["vehicles"])
        self.pending = None


def main():
    config = settings()
    log = logger("simulator")
    headers = {"X-Simulator-Secret": config.simulator_secret.get_secret_value()}
    with httpx.Client(base_url=config.api_url, headers=headers, timeout=10) as client:
        simulator = Simulator(client, log, realism=config.sim_realism)
        while True:
            try:
                simulator.step()
            except httpx.HTTPError as exc:
                event(log, "ingest_retry", error_type=type(exc).__name__)
            # Wall-clock boundaries: Docker Desktop's VM timers run ~7% fast, its wall clock is corrected.
            time.sleep(5 - time.time() % 5)


if __name__ == "__main__":
    main()
