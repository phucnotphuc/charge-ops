"""Incident lifecycle (CONTRACT §3). Only the worker calls these, so it is the single writer of incident state."""

from uuid import uuid4

from chargeops.domain import CONNECTOR, RECOVERY_RESIDUAL_C, target_current

# Demo policy, locked before evaluation.
COMMAND_TIMEOUT_S = 30
REDUCTION_CHECK_S = 120
RETEST_S = 120
CURRENT_TOLERANCE = 1.10
RECOVERED_C = RECOVERY_RESIDUAL_C
LOAD_FLOOR = 0.90
RESIDUAL_NOT_RECOVERED = "residual_not_recovered"
CYCLE_FIELDS = [
    "load_reduction_verified_at",
    "reduction_check",
    "reduction_check_reason",
    "maintenance_applied_at",
    "verifying_since",
]


def note(name, at, **fields):
    return {"$push": {"timeline": {"at": at, "event": name, **fields}}}


def transition(db, incident, state, at, name, details=None, **fields):
    """Set state and incident fields; `details` go only to the timeline event."""
    db.incidents.update_one(
        {"_id": incident["_id"]},
        {"$set": {"state": state, "state_since": at, **fields}, **note(name, at, **(details or {}))},
    )


def open_incident(db, run, evidence, source, at, event_id):
    key = {"run_id": run["_id"], "connector_id": CONNECTOR, "kind": "thermal_anomaly", "unresolved": True}
    db.incidents.update_one(
        key,
        {
            "$setOnInsert": {
                **key,
                "_id": str(uuid4()),
                "state": "open",
                "state_since": at,
                "cycle": 0,
                "source": source,
                "opened_at": at,
                "evidence": evidence,
                "event_id": event_id,
                "evidence_to_incident_seconds": (at - evidence["end"]).total_seconds(),
                "timeline": [{"at": at, "event": "opened", "source": source, "event_id": event_id}],
            }
        },
        upsert=True,
    )


def on_alert(db, run, evidence, source, at, event_id, alert_at):
    """Open an incident, or reopen one under retest; earlier states are already handling the fault."""
    incident = db.incidents.find_one(
        {"run_id": run["_id"], "connector_id": CONNECTOR, "kind": "thermal_anomaly", "unresolved": True}
    )
    if incident is None:
        open_incident(db, run, evidence, source, at, event_id)
        return "incident_opened"
    if incident["state"] == "verifying" and alert_at >= incident["verifying_since"]:
        # New handling cycle; frozen reference/original/target stay immutable.
        cycle = dict.fromkeys(CYCLE_FIELDS)
        cycle["cycle"] = incident.get("cycle", 0) + 1
        transition(db, incident, "open", at, "recurrence", {"source": source, "event_id": event_id}, **cycle)
        return "incident_reopened"
    return "incident_existing"


def mean(values):
    return sum(values) / len(values)


def charging(sample):
    """A tripped or faulted connector (no current) is an interruption, never evidence of mitigation."""
    return sample["current_a"] > 0 and sample.get("connector_status") != "faulted"


def maintenance_unlocked(incident):
    """Only `residual_not_recovered`: current verified reduced, still hot REDUCTION_CHECK_S after verification.

    `load_not_reduced` and `load_reduction_unverified` never unlock maintenance."""
    return incident.get("reduction_check_reason") == RESIDUAL_NOT_RECOVERED


def handle_command(db, incident, command, at):
    """Validate an operator command against incident state: (error, incident fields, simulator payload)."""
    state, kind = incident["state"], command["type"]
    if kind == "acknowledge":
        return (
            (None, {"state": "acknowledged"}, None)
            if state == "open"
            else (f"invalid_state:{state}", None, None)
        )
    if not db.runs.find_one({"_id": incident["run_id"], "status": "active"}):
        return "run_not_active", None, None
    if kind == "reduce_load":
        if state not in ("acknowledged", "mitigating") or incident.get("load_reduction_verified_at"):
            return f"invalid_state:{state}", None, None
        frozen = {}
        if incident.get("target_current_a") is None:
            # Frozen once from the newest valid pre-derating window; retries reuse it, never halve again.
            window = db.windows.find_one(
                {"run_id": incident["run_id"], "quality": True, "end": {"$lte": at}}, sort=[("end", -1)]
            )
            reference = window and mean([s["current_a"] for s in window["samples"]])
            if not reference:
                return "no_valid_reference_window", None, None
            original = window["samples"][-1]["current_limit_a"]
            frozen = {
                "reference_current_a": reference,
                "original_limit_a": original,
                "target_current_a": target_current(original, reference),
                "evidence_before": {"window_id": window["_id"], "end": window["end"]},
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


def process_commands(db, at):
    for command in db.commands.find({"status": "pending", "validated": False}).sort("created_at", 1):
        incident = db.incidents.find_one({"_id": command["incident_id"]})
        error, fields, sim = handle_command(db, incident, command, at)
        if error:
            db.commands.update_one(
                {"_id": command["_id"]}, {"$set": {"status": "failed", "error": error, "validated": True}}
            )
            db.incidents.update_one(
                {"_id": incident["_id"]}, note("command_rejected", at, command_id=command["_id"], error=error)
            )
            continue
        if sim is None:  # acknowledge needs no simulator
            db.commands.update_one(
                {"_id": command["_id"]}, {"$set": {"status": "applied", "applied_at": at, "validated": True}}
            )
        else:  # the handling cycle at dispatch decides which cycle a late result belongs to
            db.commands.update_one(
                {"_id": command["_id"]},
                {
                    "$set": {
                        "sim": sim,
                        "dispatched_at": at,
                        "validated": True,
                        "cycle": incident.get("cycle", 0),
                    }
                },
            )
        state = fields.pop("state", incident["state"])
        if state != incident["state"]:
            transition(db, incident, state, at, command["type"], {"command_id": command["_id"]}, **fields)
        else:  # retry or same-state command: keep state_since
            update = {"$set": fields} if fields else {}
            event = note(f"{command['type']}_dispatched", at, command_id=command["_id"])
            db.incidents.update_one({"_id": incident["_id"]}, {**update, **event})


def expire_commands(db, at):
    for command in db.commands.find(
        {"status": {"$in": ["pending", "claimed"]}, "dispatched_at": {"$ne": None}}
    ):
        if (at - command["dispatched_at"]).total_seconds() >= COMMAND_TIMEOUT_S:
            db.commands.update_one(
                {"_id": command["_id"], "status": command["status"]},
                {"$set": {"status": "timed_out", "timed_out_at": at}},
            )
            db.incidents.update_one(
                {"_id": command["incident_id"]}, note("command_timed_out", at, command_id=command["_id"])
            )
    # Late results are kept as events; operators must re-issue, and telemetry decides effect.
    for command in db.commands.find(
        {"status": "timed_out", "late_result": {"$ne": None}, "late_noted": None}
    ):
        db.commands.update_one({"_id": command["_id"]}, {"$set": {"late_noted": True}})
        db.incidents.update_one(
            {"_id": command["incident_id"]},
            note(
                "late_command_result", at, command_id=command["_id"], result=command["late_result"]["status"]
            ),
        )


def applied(db, incident, kind):
    """Newest applied command of this kind issued in the incident's current state."""
    return db.commands.find_one(
        {
            "incident_id": incident["_id"],
            "type": kind,
            "status": "applied",
            "applied_at": {"$gte": incident["state_since"]},
        },
        sort=[("applied_at", -1)],
    )


def record_maintenance(db, incident, at):
    """Match maintenance by handling cycle, not state_since: a result landing around a same-tick
    mitigating -> mitigated transition still counts."""
    if not incident.get("maintenance_applied_at"):
        command = db.commands.find_one(
            {
                "incident_id": incident["_id"],
                "type": "maintenance",
                "status": "applied",
                "cycle": incident.get("cycle", 0),
            },
            sort=[("applied_at", -1)],
        )
        if command:
            incident["maintenance_applied_at"] = command["applied_at"]
            db.incidents.update_one(
                {"_id": incident["_id"]},
                {
                    "$set": {"maintenance_applied_at": command["applied_at"]},
                    **note("maintenance_applied", at),
                },
            )


def check_mitigation(db, incident, at):
    record_maintenance(db, incident, at)  # maintenance unlocked by an ineffective derating
    command = applied(db, incident, "reduce_load")
    if not command:
        return
    ack, target = command["applied_at"], incident["target_current_a"]
    windows = list(
        db.windows.find({"run_id": incident["run_id"], "quality": True, "start": {"$gte": ack}}).sort(
            "end", 1
        )
    )
    for window in windows:
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
            db.incidents.update_one(
                {"_id": incident["_id"]},
                {
                    "$set": {"load_reduction_verified_at": window["end"]},
                    **note("load_reduction_verified", at, window_id=window["_id"]),
                },
            )
        if window["residual_mean"] < RECOVERED_C:
            evidence = {
                "window_id": window["_id"],
                "end": window["end"],
                "residual_mean": window["residual_mean"],
            }
            transition(
                db,
                incident,
                "mitigated",
                at,
                "mitigated",
                mitigated_at=window["end"],
                evidence_after=evidence,
            )
            return
    verified = incident.get("load_reduction_verified_at")
    if not verified:  # once per cycle, REDUCTION_CHECK_S after ACK
        if incident.get("reduction_check") or (at - ack).total_seconds() < REDUCTION_CHECK_S:
            return
        name, reason = (
            ("load_reduction_ineffective", "load_not_reduced")
            if windows
            else ("load_reduction_unverified", "insufficient_data")
        )
    elif (
        incident.get("reduction_check_reason") == RESIDUAL_NOT_RECOVERED
        or (at - verified).total_seconds() < REDUCTION_CHECK_S
    ):
        return
    else:  # once per cycle, REDUCTION_CHECK_S after verification, even after an earlier check event
        name, reason = "load_reduction_ineffective", RESIDUAL_NOT_RECOVERED
    incident.update(reduction_check=name, reduction_check_reason=reason)
    db.incidents.update_one(
        {"_id": incident["_id"]},
        {
            "$set": {"reduction_check": name, "reduction_check_reason": reason},
            **note(name, at, reason=reason),
        },
    )


def check_mitigated(db, incident, at):
    record_maintenance(db, incident, at)
    command = incident.get("maintenance_applied_at") and applied(db, incident, "verify_recovery")
    if command:
        transition(
            db,
            incident,
            "verifying",
            at,
            "verifying",
            verifying_since=command["applied_at"],
            retest_started_at=None,
            retest_checked_until=command["applied_at"],
        )


def check_retest(db, incident, at):
    """120 s of consecutive passing windows; a hot, low-load or low-quality window resets the clock."""
    floor = LOAD_FLOOR * incident["reference_current_a"]
    started, checked = incident["retest_started_at"], incident["retest_checked_until"]
    windows = db.windows.find(
        {
            "run_id": incident["run_id"],
            "start": {"$gte": incident["verifying_since"]},
            "end": {"$gt": checked},
        }
    ).sort("end", 1)
    for window in windows:
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
                db.incidents.update_one({"_id": incident["_id"]}, note("retest_reset", at, reason=reason))
            started = None
            continue
        started = started or window["start"]
        if (window["end"] - started).total_seconds() >= RETEST_S:
            db.incidents.update_one(
                {"_id": incident["_id"]},
                {
                    "$set": {"state": "resolved", "state_since": at, "resolved_at": window["end"]},
                    "$unset": {"unresolved": ""},
                    **note("resolved", at, retest_started_at=started, window_id=window["_id"]),
                },
            )
            return
    db.incidents.update_one(
        {"_id": incident["_id"]}, {"$set": {"retest_started_at": started, "retest_checked_until": checked}}
    )


def advance_incidents(db, at):
    for incident in db.incidents.find({"unresolved": True}):
        run = db.runs.find_one({"_id": incident["run_id"]})
        if run["status"] != "active":
            if not incident.get("run_stopped_at"):
                db.incidents.update_one(
                    {"_id": incident["_id"]},
                    {
                        "$set": {"run_stopped_at": at},
                        **note("run_stopped", at, reason="demo_ended_unresolved"),
                    },
                )
            continue
        {"mitigating": check_mitigation, "mitigated": check_mitigated, "verifying": check_retest}.get(
            incident["state"], lambda *_: None
        )(db, incident, at)
