"""Offline A/B/C runs on a simulated clock: no MongoDB, no Datadog, no network."""

from datetime import timedelta

from chargeops.domain import window_evidence, with_residuals
from chargeops.evaluation import protocol
from chargeops.evaluation.rules import Lifecycle, fixed_alert, maintenance_unlocked, residual_alert
from chargeops.model import EPOCH, ORIGINAL_LIMIT_A, STEP_S, Connector

# Detector and whether the operator acts on incidents.
ARMS = {"A": ("fixed", True), "B": ("residual", False), "C": ("residual", True)}


def detector(kind, config):
    if kind == "fixed":
        return fixed_alert(config["a_threshold_c"])
    return residual_alert(config["c_alert_c"])


def seconds(at):
    return None if at is None else (at - EPOCH).total_seconds()


class Operator:
    """Scripted operator shared by A and C: fixed reaction delay, maintenance scheduled from incident open."""

    def __init__(self, delay_s=protocol.OPERATOR_DELAY_S, maintenance_s=protocol.MAINTENANCE_AFTER_OPEN_S):
        self.delay, self.maintenance = timedelta(seconds=delay_s), timedelta(seconds=maintenance_s)
        self.issued, self.cycle_open = {}, {}

    def due(self, incident):
        state, since = incident["state"], incident["state_since"]
        key = (incident["_id"], incident["cycle"])
        if state == "open":
            self.cycle_open[key] = since
            return "acknowledge", since + self.delay
        if state == "acknowledged":
            return "reduce_load", since + self.delay
        if state == "mitigated" and not incident.get("maintenance_applied_at"):
            return "maintenance", max(since + self.delay, self.cycle_open[key] + self.maintenance)
        if state == "mitigated":
            return "verify_recovery", incident["maintenance_applied_at"] + self.delay
        if (
            state == "mitigating"
            and maintenance_unlocked(incident)
            and not incident.get("maintenance_applied_at")
        ):
            # CONTRACT §3: derating verified but the residual did not recover; maintenance on the same schedule.
            noted = [e["at"] for e in incident["timeline"] if e["event"] == "load_reduction_ineffective"]
            return "maintenance", max(noted[-1] + self.delay, self.cycle_open[key] + self.maintenance)
        return None, None  # otherwise mitigating/verifying: wait for telemetry evidence

    def act(self, life, at, windows):
        incident = life.unresolved()
        if incident is None:
            return
        kind, due = self.due(incident)
        if kind is None or at < due:
            return
        key = (incident["_id"], incident["cycle"], kind)
        last = self.issued.get(key)
        if last is not None and (last["status"] != "failed" or at < last["created_at"] + self.delay):
            return  # one command per step and cycle; a rejected one is retried after the delay
        self.issued[key] = life.command(incident, kind, at, windows)


def run_arm(exo, arm, config):
    kind, intervene = ARMS[arm]
    alert = detector(kind, config)
    plant = Connector(exo, protocol.TRIP_C, protocol.RESTART_C, protocol.LOCKOUT_S)
    life, operator = Lifecycle(), Operator() if intervene else None
    rows, truths, windows = [], [], []
    for i in range(protocol.DURATION_S // STEP_S):
        t = i * STEP_S
        at = EPOCH + timedelta(seconds=t)
        for command in life.dispatched(at - timedelta(seconds=protocol.SIM_APPLY_S)):
            plant.apply(command["sim"])  # simulator applies, then reports the result
            command.update(status="applied", applied_at=at)
        sample, truth = plant.step(i)
        if t and t % 15 == 0:
            # Worker window: samples measured in [end - 60 s, end), baseline from run start (storage.window_rows).
            history = with_residuals(rows)
            evidence = window_evidence(
                [r for r in history if r["measured_at"] >= at - timedelta(seconds=60)], at
            )
            windows.append(evidence)
            if alert(evidence):
                life.alert(evidence, at, at, kind)
        rows.append(sample)
        truths.append(truth)
        life.advance(at, windows)
        if operator:
            operator.act(life, at, windows)
    life.advance(EPOCH + timedelta(seconds=protocol.DURATION_S), windows, run_active=False)
    return {
        "exo": exo,
        "arm": arm,
        "rows": rows,
        "truths": truths,
        "windows": windows,
        "life": life,
        "plant": plant,
    }


def run_seed(exo, config):
    return {arm: run_arm(exo, arm, config) for arm in ARMS}


def record(result):
    """JSON-serializable per-run record; times are seconds on the simulated clock from run start."""
    exo, life, rows, truths = result["exo"], result["life"], result["rows"], result["truths"]
    fault = exo.fault
    incidents = [
        {
            "opened_s": seconds(i["opened_at"]),
            "source": i["source"],
            "final_state": i["state"],
            "cycles": i["cycle"] + 1,
            "reference_current_a": i.get("reference_current_a"),
            "original_limit_a": i.get("original_limit_a"),
            "target_current_a": i.get("target_current_a"),
            "load_reduction_verified_s": seconds(i.get("load_reduction_verified_at")),
            "mitigated_s": seconds(i.get("mitigated_at")),
            "maintenance_applied_s": seconds(i.get("maintenance_applied_at")),
            "resolved_s": seconds(i.get("resolved_at")),
            "timeline": [{"t": seconds(e["at"]), "event": e["event"]} for e in i["timeline"]],
        }
        for i in life.incidents
    ]
    commands = [
        {
            "incident": c["incident_id"],
            "cycle": c["cycle"],
            "type": c["type"],
            "status": c["status"],
            "error": c.get("error"),
            "created_s": seconds(c["created_at"]),
            "applied_s": seconds(c.get("applied_at")),
        }
        for c in life.commands
    ]
    interrupted = sum(
        STEP_S for r, x in zip(rows, truths, strict=True) if x["tripped"] and r["requested_current_a"] > 0
    )
    return {
        "protocol_version": protocol.PROTOCOL_VERSION,
        "seed": exo.seed,
        "scenario": exo.scenario,
        "arm": result["arm"],
        "truth": {
            "profile_id": exo.profile_id,
            "k": exo.k,
            "tau_s": exo.tau_s,
            "change_s": exo.change_s,
            "fault": fault
            and {
                "onset_s": fault.onset_s,
                "severity_c": fault.severity_c,
                "ramp_s": fault.ramp_s,
                "onset_current_a": fault.onset_current_a,
            },
        },
        "incidents": incidents,
        "commands": commands,
        "trips_s": list(result["plant"].trips),
        "interruption_s": interrupted,
        "kwh": sum(r["power_kw"] for r in rows) * STEP_S / 3600,
        "max_true_temperature_c": max(x["temperature_c"] for x in truths),
        "series": {
            "current_a": [r["current_a"] for r in rows],
            "current_limit_a": [r["current_limit_a"] for r in rows],
            "power_kw": [r["power_kw"] for r in rows],
            "connector_temperature_c": [r["connector_temperature_c"] for r in rows],
            "true_temperature_c": [x["temperature_c"] for x in truths],
        },
        "derated_steps": sum(1 for r in rows if r["current_limit_a"] < ORIGINAL_LIMIT_A),
    }


def detector_windows(result, config):
    """Detector-only view: both detectors evaluated on the same (un-intervened B) windows."""
    fixed, residual = fixed_alert(config["a_threshold_c"]), residual_alert(config["c_alert_c"])
    return [
        {
            "end_s": seconds(w["end"]),
            "quality": w["quality"],
            "fixed": bool(fixed(w)),
            "residual": bool(residual(w)),
            "mean_temperature_c": (
                sum(s["connector_temperature_c"] for s in w["samples"]) / len(w["samples"])
                if w["samples"]
                else None
            ),
            "residual_mean": w["residual_mean"],
        }
        for w in result["windows"]
    ]
