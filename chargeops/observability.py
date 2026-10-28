"""Datadog-mode incident signals (open count, time in state, one event per timeline entry) and a worker
heartbeat. The per-connector measurement gauges live in worker.evaluate_window (GAUGE_FIELDS).

Metric tags stay within run/connector/station/env (+ incident state on the age gauge); events may carry
incident_id because events are not custom-metric series."""

from chargeops.domain import CONNECTORS, LOCATION

ALERT_TYPE = {"opened": "error", "recurrence": "error", "mitigated": "warning", "resolved": "success"}


def connector_tags(run_id, connector_id):
    return [
        f"run_id:{run_id}",
        f"connector_id:{connector_id}",
        f"station_id:{LOCATION[connector_id][0]}",
        "env:hackathon",
    ]


def emit_heartbeat(stats):
    stats.increment("chargeops.worker.tick", tags=["env:hackathon"])


def emit_run_signals(db, stats, run, at):
    open_by_connector = dict.fromkeys(CONNECTORS, 0)
    for incident in db.incidents.find({"run_id": run["_id"]}):
        tags = connector_tags(run["_id"], incident["connector_id"])
        if incident.get("unresolved"):
            open_by_connector[incident["connector_id"]] += 1
            age = (at - incident["state_since"]).total_seconds()
            stats.gauge("chargeops.incident.state_age_s", age, tags=[*tags, f"state:{incident['state']}"])
        emit_timeline_events(db, stats, incident, tags)
    for connector_id, count in open_by_connector.items():
        stats.gauge("chargeops.incident.open", count, tags=connector_tags(run["_id"], connector_id))


def emit_timeline_events(db, stats, incident, tags):
    """At-least-once: the cursor advances after the events are handed to DogStatsD."""
    cursor = db.dd_event_cursor.find_one({"_id": incident["_id"]}) or {"sent": 0}
    cursor_before = cursor["sent"]
    timeline = incident.get("timeline", [])
    if len(timeline) < cursor["sent"]:  # timeline shrank (retest reset): restart rather than skip new entries
        cursor["sent"] = 0
    for entry in timeline[cursor["sent"] :]:
        stats.event(
            f"ChargeOps {entry['event']} · {incident['connector_id']}",
            f"Incident {incident['_id']} {entry['event']} at {entry['at'].isoformat()}; reason {entry.get('reason') or '-'}",
            alert_type=ALERT_TYPE.get(entry["event"], "info"),
            aggregation_key=incident["_id"],
            source_type_name="chargeops",
            tags=[*tags, f"incident_id:{incident['_id']}", f"event:{entry['event']}", "source:chargeops"],
        )
    if len(timeline) != cursor_before:
        db.dd_event_cursor.update_one({"_id": incident["_id"]}, {"$set": {"sent": len(timeline)}}, upsert=True)


def emit_charging_slo(stats, run_id, connector_id, latest):
    """One 15-second block = 0.25 charging minutes. Total: charging was requested; good: current was delivered."""
    if not latest or latest.get("requested_current_a", 0) <= 0:
        return
    tags = connector_tags(run_id, connector_id)
    stats.increment("chargeops.slo.charging_minutes.total", 0.25, tags=tags)
    if latest.get("current_a", 0) > 0:
        stats.increment("chargeops.slo.charging_minutes.good", 0.25, tags=tags)


MITIGATION_TARGET_S = 300


def emit_incident_outcome(stats, report):
    """Once per incident: it counts when derating was verified, or reached mitigation without verification (bad)."""
    seconds = report.get("seconds_to_effective_reduction")
    if seconds is None and report.get("seconds_to_mitigated") is None:
        return
    stats.increment("chargeops.slo.mitigation.total", 1)
    if seconds is not None and seconds <= MITIGATION_TARGET_S:
        stats.increment("chargeops.slo.mitigation.good", 1)
