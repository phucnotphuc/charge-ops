"""In-memory mirror of the worker's incident rules (lifecycle.py is MongoDB-bound) for offline evaluation.

Thresholds and pure predicates are imported from the product; tests/test_evaluation.py replays the same
window/command sequences through lifecycle.py (mongomock) and this mirror and compares states and timestamps.
Only this module changes evaluation incident state, as the worker is the single writer in the product."""

from chargeops.domain import ALERT_RESIDUAL_C, target_current
from chargeops.lifecycle import (
    CURRENT_TOLERANCE,
    CYCLE_FIELDS,
    LOAD_FLOOR,
    RECOVERED_C,
    REDUCTION_CHECK_S,
    RESIDUAL_NOT_RECOVERED,
    RETEST_S,
    charging,
    maintenance_unlocked,
)

ALERT_C = ALERT_RESIDUAL_C
CYCLE = CYCLE_FIELDS


def mean(values):
    return sum(values) / len(values)


def residual_alert(threshold_c):
    return lambda evidence: evidence["quality"] and evidence["residual_mean"] > threshold_c


def fixed_alert(threshold_c):
    """Baseline A: same 60-second window and quality gate, absolute mean temperature instead of residual."""
    return lambda evidence: (
        evidence["quality"]
        and (mean([s["connector_temperature_c"] for s in evidence["samples"]]) > threshold_c)
    )


class Lifecycle:
    def __init__(self):
        self.incidents, self.commands = [], []

    def unresolved(self):
        return next((i for i in self.incidents if i["unresolved"]), None)

    def note(self, incident, name, at, **fields):
        incident["timeline"].append({"at": at, "event": name, **fields})

    def transition(self, incident, state, at, name, **fields):
        incident.update(state=state, state_since=at, **fields)
        self.note(incident, name, at)

    def alert(self, evidence, at, alert_at, source):
        """lifecycle.on_alert: open, or reopen under retest; other states are already handling the fault."""
        incident = self.unresolved()
        if incident is None:
            self.incidents.append(
                {
                    "_id": len(self.incidents),
                    "state": "open",
                    "state_since": at,
                    "opened_at": at,
                    "source": source,
                    "evidence": {"end": evidence["end"], "residual_mean": evidence["residual_mean"]},
                    "unresolved": True,
                    "cycle": 0,  # evaluation bookkeeping: recurrences start a new handling cycle
                    "timeline": [{"at": at, "event": "opened", "source": source}],
                }
            )
            return "incident_opened"
        if incident["state"] == "verifying" and alert_at >= incident["verifying_since"]:
            incident["cycle"] += 1
            self.transition(incident, "open", at, "recurrence", **dict.fromkeys(CYCLE))
            return "incident_reopened"
        return "incident_existing"

    def handle(self, incident, kind, at, windows, run_active):
        """lifecycle.handle_command: (error, incident fields, simulator payload)."""
        state = incident["state"]
        if kind == "acknowledge":
            if state != "open":
                return f"invalid_state:{state}", None, None
            return None, {"state": "acknowledged"}, None
        if not run_active:
            return "run_not_active", None, None
        if kind == "reduce_load":
            if state not in ("acknowledged", "mitigating") or incident.get("load_reduction_verified_at"):
                return f"invalid_state:{state}", None, None
            frozen = {}
            if incident.get("target_current_a") is None:
                window = next((w for w in reversed(windows) if w["quality"] and w["end"] <= at), None)
                reference = window and mean([s["current_a"] for s in window["samples"]])
                if not reference:
                    return "no_valid_reference_window", None, None
                original = window["samples"][-1]["current_limit_a"]
                frozen = {
                    "reference_current_a": reference,
                    "original_limit_a": original,
                    "target_current_a": target_current(original, reference),
                    "evidence_before": {"end": window["end"]},
                }
            target = frozen.get("target_current_a", incident.get("target_current_a"))
            return None, {"state": "mitigating", **frozen}, {"current_limit_a": target}
        if kind == "maintenance":
            if state == "mitigated" or (state == "mitigating" and maintenance_unlocked(incident)):
                return None, {}, {"fault_cleared": True}
            return f"invalid_state:{state}", None, None
        if state != "mitigated":
            return f"invalid_state:{state}", None, None
        if not incident.get("maintenance_applied_at"):
            return "maintenance_not_applied", None, None
        return None, {}, {"current_limit_a": incident["original_limit_a"]}  # verify_recovery

    def command(self, incident, kind, at, windows, run_active=True):
        """API insert + lifecycle.process_commands in one step (the worker validates within one tick)."""
        command = {
            "_id": len(self.commands),
            "incident_id": incident["_id"],
            "cycle": incident["cycle"],
            "type": kind,
            "created_at": at,
            "status": "pending",
        }
        self.commands.append(command)
        error, fields, sim = self.handle(incident, kind, at, windows, run_active)
        if error:
            command.update(status="failed", error=error)
            self.note(incident, "command_rejected", at, command_id=command["_id"], error=error)
            return command
        if sim is None:
            command.update(status="applied", applied_at=at)
        else:
            command.update(sim=sim, dispatched_at=at)
        state = fields.pop("state", incident["state"])
        if state != incident["state"]:
            self.transition(incident, state, at, kind, **fields)
        else:
            incident.update(fields)
            self.note(incident, f"{kind}_dispatched", at, command_id=command["_id"])
        return command

    def dispatched(self, before):
        return [c for c in self.commands if c["status"] == "pending" and c["dispatched_at"] <= before]

    def applied(self, incident, kind):
        matches = [
            c
            for c in self.commands
            if c["incident_id"] == incident["_id"]
            and c["type"] == kind
            and c["status"] == "applied"
            and c["applied_at"] >= incident["state_since"]
        ]
        return max(matches, key=lambda c: c["applied_at"], default=None)

    def record_maintenance(self, incident, at):
        """lifecycle.record_maintenance: matched by handling cycle, not state_since."""
        if not incident.get("maintenance_applied_at"):
            matches = [
                c
                for c in self.commands
                if c["incident_id"] == incident["_id"]
                and c["type"] == "maintenance"
                and c["status"] == "applied"
                and c["cycle"] == incident["cycle"]
            ]
            command = max(matches, key=lambda c: c["applied_at"], default=None)
            if command:
                incident["maintenance_applied_at"] = command["applied_at"]
                self.note(incident, "maintenance_applied", at)

    def check_mitigation(self, incident, at, windows):
        self.record_maintenance(incident, at)
        command = self.applied(incident, "reduce_load")
        if not command:
            return
        ack, target = command["applied_at"], incident["target_current_a"]
        eligible = [w for w in windows if w["quality"] and w["start"] >= ack]
        for window in eligible:
            samples = window["samples"]
            load_ok = (
                all(charging(s) for s in samples)
                and mean([s["current_a"] for s in samples]) <= CURRENT_TOLERANCE * target
                and all(s["current_limit_a"] <= target for s in samples)
            )
            if not load_ok:
                continue
            if not incident.get("load_reduction_verified_at"):
                incident["load_reduction_verified_at"] = window["end"]
                self.note(incident, "load_reduction_verified", at)
            if window["residual_mean"] < RECOVERED_C:
                after = {"end": window["end"], "residual_mean": window["residual_mean"]}
                self.transition(
                    incident, "mitigated", at, "mitigated", mitigated_at=window["end"], evidence_after=after
                )
                return
        verified = incident.get("load_reduction_verified_at")
        if not verified:
            if incident.get("reduction_check") or (at - ack).total_seconds() < REDUCTION_CHECK_S:
                return
            name, reason = (
                ("load_reduction_ineffective", "load_not_reduced")
                if eligible
                else ("load_reduction_unverified", "insufficient_data")
            )
        elif (
            incident.get("reduction_check_reason") == RESIDUAL_NOT_RECOVERED
            or (at - verified).total_seconds() < REDUCTION_CHECK_S
        ):
            return
        else:
            name, reason = "load_reduction_ineffective", RESIDUAL_NOT_RECOVERED
        incident.update(reduction_check=name, reduction_check_reason=reason)
        self.note(incident, name, at, reason=reason)

    def check_mitigated(self, incident, at):
        self.record_maintenance(incident, at)
        command = incident.get("maintenance_applied_at") and self.applied(incident, "verify_recovery")
        if command:
            self.transition(
                incident,
                "verifying",
                at,
                "verifying",
                verifying_since=command["applied_at"],
                retest_started_at=None,
                retest_checked_until=command["applied_at"],
            )

    def check_retest(self, incident, at, windows):
        floor = LOAD_FLOOR * incident["reference_current_a"]
        started, checked = incident["retest_started_at"], incident["retest_checked_until"]
        for window in windows:
            if window["start"] < incident["verifying_since"] or window["end"] <= checked:
                continue
            samples, checked = window["samples"], window["end"]
            if not window["quality"]:
                reason = "insufficient_data"
            elif any(s["residual"] >= RECOVERED_C for s in samples):
                reason = "hot"
            elif any(min(s["current_a"], s["requested_current_a"]) < floor for s in samples):
                reason = "low_load"
            else:
                reason = None
            if reason:
                if started:
                    self.note(incident, "retest_reset", at, reason=reason)
                started = None
                continue
            started = started or window["start"]
            if (window["end"] - started).total_seconds() >= RETEST_S:
                incident.update(state="resolved", state_since=at, resolved_at=window["end"], unresolved=False)
                self.note(incident, "resolved", at, retest_started_at=started)
                return
        incident.update(retest_started_at=started, retest_checked_until=checked)

    def advance(self, at, windows, run_active=True):
        for incident in [i for i in self.incidents if i["unresolved"]]:
            if not run_active:
                if not incident.get("run_stopped_at"):
                    incident["run_stopped_at"] = at
                    self.note(incident, "run_stopped", at, reason="demo_ended_unresolved")
                continue
            if incident["state"] == "mitigating":
                self.check_mitigation(incident, at, windows)
            elif incident["state"] == "mitigated":
                self.check_mitigated(incident, at)
            elif incident["state"] == "verifying":
                self.check_retest(incident, at, windows)
