import os
import threading
import time
from contextlib import contextmanager
from datetime import timedelta

from datadog import DogStatsd
from pymongo.errors import PyMongoError

from chargeops.config import settings
from chargeops.domain import (
    ALERT_RESIDUAL_C,
    CONNECTORS,
    LOCATION,
    RUN_MAX_S,
    block_end,
    now,
    window_evidence,
)
from chargeops.lifecycle import advance_incidents, expire_commands, on_alert, process_commands
from chargeops.logging import event, logger
from chargeops.storage import database, fleet_window_rows, initialize

WATCHED = ("inbox", "commands", "run_events")
STATE_ID = "change_stream"


@contextmanager
def span(name, **tags):
    # Manual spans only when tracing is on (ddtrace-run sets DD_TRACE_ENABLED=true); otherwise a no-op.
    if os.environ.get("DD_TRACE_ENABLED", "").lower() != "true":
        yield None
        return
    from ddtrace import tracer

    with tracer.trace(name, service=os.environ.get("DD_SERVICE") or None) as current:
        for key, value in tags.items():
            if value is not None:
                current.set_tag(key, value)
        yield current


class ChangeWaker:
    """Wakes the worker loop early on inbox/commands/run_events changes; ticks still run on a timer."""

    def __init__(self, db, log):
        self.db, self.log = db, log
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.thread = None
        self.stream = None
        self.token = None
        self.pending = None
        self.saved = None

    def _open(self):
        pipeline = [
            {
                "$match": {
                    "ns.coll": {"$in": list(WATCHED)},
                    "operationType": {"$in": ["insert", "update", "replace"]},
                }
            }
        ]
        state = self.db.worker_state.find_one({"_id": STATE_ID})
        if state and state.get("token"):
            try:
                return self.db.watch(pipeline, resume_after=state["token"], max_await_time_ms=1000)
            except Exception as exc:  # stale or invalid token: start from now, ticks cover the gap
                event(self.log, "change_stream_resume_reset", error_type=type(exc).__name__)
        return self.db.watch(pipeline, max_await_time_ms=1000)

    def start(self):
        try:
            self.stream = self._open()
        except Exception as exc:  # standalone mongod, mongomock: polling only
            event(self.log, "change_stream_unavailable", error_type=type(exc).__name__)
            return
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()

    def _pump(self, max_delay=30):
        delay = 1
        while not self.stopping.is_set():
            try:
                if self.stream is None:
                    self.stream = self._open()
                    event(self.log, "change_stream_reopened")
                    delay = 1
                change = self.stream.try_next()
            except Exception as exc:
                # Election, mongod restart, network blip: reopen with backoff; ticks keep running on the timer.
                event(self.log, "change_stream_unavailable", error_type=type(exc).__name__, retry_s=delay)
                self._close()
                self.stopping.wait(delay)
                delay = min(delay * 2, max_delay)
                continue
            if change is None:
                self.stopping.wait(0.05)
                continue
            self.token = self.stream.resume_token
            self.wake.set()

    def wait(self, timeout):
        woke = self.wake.wait(timeout)
        self.wake.clear()
        if woke:
            self.pending = self.token  # changes arriving during the tick wake the next iteration
        return woke

    def commit(self):
        # Called after a tick consumed the batch, so a restart resumes at the last handled change.
        token = self.pending
        if token is not None and token != self.saved:
            self.db.worker_state.update_one({"_id": STATE_ID}, {"$set": {"token": token}}, upsert=True)
            self.saved = token

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=2)
        self._close()

    def _close(self):
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass


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
                "connector_id": item["connector_id"],
                "end": {"$lte": item["occurred_at"], "$gte": item["occurred_at"] - timedelta(seconds=30)},
            },
            sort=[("end", -1)],
        )
        if item["status"] == "recovery":
            reason = "recovery_recorded_not_resolved"
        elif not evidence or not evidence["quality"] or evidence["residual_mean"] <= ALERT_RESIDUAL_C:
            reason = "insufficient_alert_evidence"
        else:
            reason = on_alert(db, run, evidence, "datadog", at, item["_id"], item["occurred_at"])
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
    # Idempotent: repairs a crash between activation and the post-activation charge.
    started = [
        r["_id"] for r in db.runs.find({"mode": "datadog", "started_at": {"$exists": True}}, {"_id": 1})
    ]
    if started:
        db.budgets.update_one(
            {"_id": settings().dd_trial_id}, {"$addToSet": {"run_ids": {"$each": started}}}, upsert=True
        )
    pending = db.runs.find_one({"status": "pending"})
    if pending:
        # Every update is filtered on status=pending so an API-side cancel wins the race.
        still_pending = {"_id": pending["_id"], "status": "pending"}
        if pending["mode"] == "datadog":
            if not settings().dd_monitor_id:
                db.runs.update_one(
                    still_pending,
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
                    still_pending,
                    {
                        "$set": {"status": "rejected", "reason": "trial_run_budget_exhausted"},
                        "$unset": {"active_slot": ""},
                    },
                )
                return
        activated = db.runs.update_one(still_pending, {"$set": {"status": "active", "started_at": at}})
        if activated.matched_count == 1 and pending["mode"] == "datadog":
            db.budgets.update_one({"_id": settings().dd_trial_id}, {"$addToSet": {"run_ids": pending["_id"]}})
    run = db.runs.find_one({"status": "active"})
    if run and (run.get("stop_requested") or (at - run["started_at"]).total_seconds() >= RUN_MAX_S):
        db.runs.update_one(
            {"_id": run["_id"]},
            {"$set": {"status": "stopped", "stopped_at": at}, "$unset": {"active_slot": ""}},
        )


def tick(db, stats, log):
    with span("worker.tick"):
        run_phases(db, stats, log)


def run_phases(db, stats, log):
    at = now()
    with span("worker.manage_run"):
        manage_run(db, at)
    run = db.runs.find_one({"status": "active"})
    if run:
        evaluate_windows(db, stats, log, run, at)
    with span("worker.inbox"):
        for item in db.inbox.find({"processed": False}).sort("occurred_at", 1).limit(100):
            with span(
                "worker.inbox_event",
                run_id=item["run_id"],
                connector_id=item["connector_id"],
                event_id=item["_id"],
            ) as current:
                outcome = process_event(db, item, at, settings().dd_monitor_id)
                incident = db.incidents.find_one(
                    {"run_id": item["run_id"], "connector_id": item["connector_id"], "unresolved": True},
                    {"_id": 1},
                )
                if current is not None and incident:
                    current.set_tag("incident_id", str(incident["_id"]))
                event(
                    log,
                    "webhook_processed",
                    run_id=item["run_id"],
                    connector_id=item["connector_id"],
                    event_id=item["_id"],
                    incident_id=incident and incident["_id"],
                    outcome=outcome,
                )
    with span("worker.commands"):
        process_commands(db, at)
        expire_commands(db, at)
    with span("worker.incidents"):
        advance_incidents(db, at)


def evaluate_windows(db, stats, log, run, at):
    # Wait two seconds for ingestion; metric window ends on a 15-second boundary.
    end = block_end(at - timedelta(seconds=2))
    todo = [c for c in CONNECTORS if not db.windows.find_one({"_id": f"{run['_id']}:{c}:{end.isoformat()}"})]
    if not todo:
        return
    rows = fleet_window_rows(db, run["_id"], end)  # one aggregation for the whole fleet
    for connector_id in todo:
        with span("worker.alert_evaluation", run_id=run["_id"], connector_id=connector_id):
            evaluate_window(db, stats, log, run, at, end, connector_id, rows.get(connector_id, []))


def evaluate_window(db, stats, log, run, at, end, connector_id, rows):
    window_id = f"{run['_id']}:{connector_id}:{end.isoformat()}"
    evidence = window_evidence(rows, end)
    evidence.update(_id=window_id, run_id=run["_id"], connector_id=connector_id, created_at=at)
    db.windows.insert_one(evidence)
    if run["mode"] == "local":
        if evidence["quality"] and evidence["residual_mean"] > ALERT_RESIDUAL_C:
            on_alert(db, run, evidence, "local", at, window_id, end)
        return
    station_id = LOCATION[connector_id][0]
    tags = [
        f"run_id:{run['_id']}",
        f"connector_id:{connector_id}",
        f"station_id:{station_id}",
        "env:hackathon",
    ]
    # Export only the newest non-overlapping block, not the rolling mean.
    block = [r for r in rows if r["measured_at"] >= end - timedelta(seconds=15)]
    stats.increment("chargeops.residual_positive", sum(max(r["residual"], 0) for r in block), tags=tags)
    stats.increment("chargeops.residual_negative", sum(max(-r["residual"], 0) for r in block), tags=tags)
    stats.increment("chargeops.samples", len(block), tags=tags)
    stats.gauge("chargeops.quality", int(evidence["quality"]), tags=tags)
    event(
        log,
        "metrics_emitted",
        run_id=run["_id"],
        connector_id=connector_id,
        window_end=end,
        quality=evidence["quality"],
        count=len(block),
    )


def run_loop(db, stats, log, waker, interval=2, min_gap=0.25, iterations=None):
    done = 0
    while iterations is None or done < iterations:
        waker.wait(interval)
        try:
            tick(db, stats, log)
            waker.commit()
        except (PyMongoError, OSError) as exc:
            event(log, "worker_retry", error_type=type(exc).__name__)
        done += 1
        time.sleep(min_gap)  # tick's own writes to watched collections re-wake the loop once


def main():
    db = database()
    initialize(db)
    stats = DogStatsd(host=settings().dd_agent_host, port=8125)
    log = logger("worker")
    waker = ChangeWaker(db, log)
    waker.start()
    try:
        run_loop(db, stats, log, waker)
    finally:
        waker.stop()


if __name__ == "__main__":
    main()
