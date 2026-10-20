import time
from datetime import timedelta
from uuid import uuid4

from datadog import DogStatsd
from pymongo.errors import PyMongoError

from chargeops.config import settings
from chargeops.domain import CONNECTOR, block_end, now, window_evidence
from chargeops.logging import event, logger
from chargeops.storage import database, initialize, telemetry_pipeline


def open_incident(db, run, evidence, source, at, event_id):
    key = {"run_id": run["_id"], "connector_id": CONNECTOR, "kind": "thermal_anomaly", "unresolved": True}
    db.incidents.update_one(
        key,
        {
            "$setOnInsert": {
                **key,
                "_id": str(uuid4()),
                "state": "open",
                "source": source,
                "opened_at": at,
                "evidence": evidence,
                "event_id": event_id,
                "evidence_to_incident_seconds": (at - evidence["end"]).total_seconds(),
            }
        },
        upsert=True,
    )


def process_event(db, item, at, monitor_id):
    run = db.runs.find_one({"_id": item["run_id"], "status": "active", "mode": "datadog"})
    reason = None
    cursor_id = f"{item['run_id']}:{item['connector_id']}:{item['monitor_id']}"
    cursor = db.monitor_state.find_one({"_id": cursor_id})
    if not run:
        reason = "inactive_or_wrong_mode"
    elif not monitor_id or item["monitor_id"] != monitor_id:
        reason = "unknown_monitor"
    elif item["occurred_at"] < run["started_at"] or item["occurred_at"] > at + timedelta(seconds=5):
        reason = "timestamp_outside_run"
    elif cursor and item["occurred_at"] < cursor["occurred_at"]:
        reason = "out_of_order"
    else:
        # Select durable evidence at alert time, rather than silently attaching current data.
        evidence = db.windows.find_one(
            {
                "run_id": run["_id"],
                "end": {"$lte": item["occurred_at"], "$gte": item["occurred_at"] - timedelta(seconds=30)},
            },
            sort=[("end", -1)],
        )
        if item["status"] == "recovery":
            reason = "recovery_recorded_not_resolved"
        elif not evidence or not evidence["quality"] or evidence["residual_mean"] <= 8:
            reason = "insufficient_alert_evidence"
        else:
            open_incident(db, run, evidence, "datadog", at, item["_id"])
            db.incidents.update_one(
                {"run_id": run["_id"], "event_id": item["_id"]},
                {
                    "$set": {
                        "datadog_occurred_at": item["occurred_at"],
                        "webhook_received_at": item.get("received_at", at),
                        "webhook_delivery_seconds": (
                            item.get("received_at", at) - item["occurred_at"]
                        ).total_seconds(),
                    }
                },
            )
            reason = "incident_opened_or_existing"
        db.monitor_state.update_one(
            {"_id": cursor_id},
            {"$set": {"occurred_at": item["occurred_at"], "status": item["status"]}},
            upsert=True,
        )
    db.inbox.update_one(
        {"_id": item["_id"]}, {"$set": {"processed": True, "processed_at": at, "outcome": reason}}
    )
    return reason


def manage_run(db, at):
    pending = db.runs.find_one({"status": "pending"})
    if pending:
        if pending["mode"] == "datadog":
            if not settings().dd_monitor_id:
                db.runs.update_one(
                    {"_id": pending["_id"]},
                    {
                        "$set": {"status": "rejected", "reason": "configure_dd_monitor_id_first"},
                        "$unset": {"active_slot": ""},
                    },
                )
                return
            # Single worker; addToSet makes crash/restart charging idempotent.
            trial_id = settings().dd_trial_id
            db.budgets.update_one({"_id": trial_id}, {"$setOnInsert": {"run_ids": []}}, upsert=True)
            budget = db.budgets.find_one({"_id": trial_id})
            if pending["_id"] not in budget["run_ids"] and len(budget["run_ids"]) >= 20:
                db.runs.update_one(
                    {"_id": pending["_id"]},
                    {
                        "$set": {"status": "rejected", "reason": "trial_run_budget_exhausted"},
                        "$unset": {"active_slot": ""},
                    },
                )
                return
            db.budgets.update_one({"_id": trial_id}, {"$addToSet": {"run_ids": pending["_id"]}})
        db.runs.update_one({"_id": pending["_id"]}, {"$set": {"status": "active", "started_at": at}})
    run = db.runs.find_one({"status": "active"})
    if run and (run.get("stop_requested") or (at - run["started_at"]).total_seconds() >= 900):
        db.runs.update_one(
            {"_id": run["_id"]},
            {"$set": {"status": "stopped", "stopped_at": at}, "$unset": {"active_slot": ""}},
        )


def tick(db, stats, log):
    at = now()
    manage_run(db, at)
    run = db.runs.find_one({"status": "active"})
    if run:
        # Wait two seconds for ingestion; metric window ends on a 15-second boundary.
        end = block_end(at - timedelta(seconds=2))
        window_id = f"{run['_id']}:{end.isoformat()}"
        if not db.windows.find_one({"_id": window_id}):
            rows = list(db.telemetry.aggregate(telemetry_pipeline(run["_id"], end)))
            evidence = window_evidence(rows, end)
            evidence.update(_id=window_id, run_id=run["_id"], created_at=at)
            db.windows.insert_one(evidence)
            if run["mode"] == "local":
                if evidence["quality"] and evidence["residual_mean"] > 8:
                    open_incident(db, run, evidence, "local", at, window_id)
            else:
                tags = [f"run_id:{run['_id']}", f"connector_id:{CONNECTOR}", "env:hackathon"]
                # Export only the newest non-overlapping block, not the rolling mean.
                block = [r for r in rows if r["measured_at"] >= end - timedelta(seconds=15)]
                stats.increment(
                    "chargeops.residual_positive", sum(max(r["residual"], 0) for r in block), tags=tags
                )
                stats.increment(
                    "chargeops.residual_negative", sum(max(-r["residual"], 0) for r in block), tags=tags
                )
                stats.increment("chargeops.samples", len(block), tags=tags)
                stats.gauge("chargeops.quality", int(evidence["quality"]), tags=tags)
                event(
                    log,
                    "metrics_emitted",
                    run_id=run["_id"],
                    window_end=end,
                    quality=evidence["quality"],
                    count=len(block),
                )
    for item in db.inbox.find({"processed": False}).sort("occurred_at", 1).limit(100):
        outcome = process_event(db, item, at, settings().dd_monitor_id)
        incident = db.incidents.find_one(
            {"run_id": item["run_id"], "connector_id": item["connector_id"], "unresolved": True}, {"_id": 1}
        )
        event(
            log,
            "webhook_processed",
            run_id=item["run_id"],
            connector_id=item["connector_id"],
            event_id=item["_id"],
            incident_id=incident and incident["_id"],
            outcome=outcome,
        )


def main():
    db = database()
    initialize(db)
    stats = DogStatsd(host=settings().dd_agent_host, port=8125)
    log = logger("worker")
    while True:
        try:
            tick(db, stats, log)
        except (PyMongoError, OSError) as exc:
            event(log, "worker_retry", error_type=type(exc).__name__)
        time.sleep(2)


if __name__ == "__main__":
    main()
