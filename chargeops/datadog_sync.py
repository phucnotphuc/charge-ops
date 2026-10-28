"""datadog-sync: mirror ChargeOps incidents of datadog-mode runs into Datadog (Incident, timeline steps, resolve;
notebook and postmortem in later tasks). Never writes incidents; only datadog_sync / incident_reports."""

import time
from datetime import timedelta

from datadog import DogStatsd
from pymongo.errors import PyMongoError

from chargeops.config import settings
from chargeops.datadog_client import DatadogClient, DatadogError
from chargeops.domain import now
from chargeops.logging import event, logger
from chargeops.notebooks import investigation_notebook, notebook_url
from chargeops.observability import emit_incident_outcome
from chargeops.postmortem import postmortem_pipeline, report_markdown, report_summary
from chargeops.storage import database

BACKOFF_S = (30, 60, 120, 300)


def note_text(incident, entry):
    reason = entry.get("reason") or entry.get("command_id") or ""
    head = f"{entry['at'].isoformat()} · {entry['event']} · {incident['connector_id']}"
    return head + (f" · {reason}" if reason else "")


class DatadogSync:
    def __init__(self, db, client, stats, log, site):
        self.db, self.client, self.stats, self.log, self.site = db, client, stats, log, site

    def put(self, state, **fields):
        """Persist only the changed fields ($set), so a concurrent writer of other fields is never overwritten."""
        state.update(fields)
        self.db.datadog_sync.update_one(
            {"_id": state["_id"]}, {"$set": fields, "$setOnInsert": {"run_id": state["run_id"]}}, upsert=True
        )

    def sync_once(self, at):
        touched = 0
        runs = [r["_id"] for r in self.db.runs.find({"mode": "datadog"}, {"_id": 1})]
        incidents = list(self.db.incidents.find({"run_id": {"$in": runs}}))
        states = {d["_id"]: d for d in self.db.datadog_sync.find({"_id": {"$in": [i["_id"] for i in incidents]}})}
        for incident in incidents:
            state = states.get(incident["_id"]) or {"_id": incident["_id"], "run_id": incident["run_id"]}
            next_at = state.get("next_attempt_at")
            if next_at and next_at.replace(tzinfo=at.tzinfo) > at:
                continue
            if self.is_done(incident, state):
                continue
            try:
                self.sync_incident(incident, state, at)
                self.put(state, attempts=0, last_error=None, next_attempt_at=None)
            except DatadogError as error:
                attempts = state.get("attempts", 0) + 1
                delay = BACKOFF_S[min(attempts, len(BACKOFF_S)) - 1]
                self.put(state, attempts=attempts, last_error=str(error), next_attempt_at=at + timedelta(seconds=delay))
                event(self.log, "datadog_sync_failed", incident_id=incident["_id"], status=error.status)
            touched += 1
        return touched

    @staticmethod
    def is_done(incident, state):
        if not state.get("dd_incident_id") or state.get("last_error") or not state.get("notebook_attached"):
            return False
        if DatadogSync.is_closed(incident) and not (state.get("postmortem_done") and state.get("postmortem_notebook_updated")):
            return False
        caught_up = state.get("synced_events", 0) >= len(incident.get("timeline", []))
        return caught_up and (incident["state"] != "resolved" or state.get("dd_resolved", False))

    @staticmethod
    def is_closed(incident):
        return incident["state"] == "resolved" or bool(incident.get("run_stopped_at"))

    def sync_incident(self, incident, state, at):
        if not state.get("dd_incident_id"):
            # A crash between "create attempted" and "id saved" must not duplicate the Datadog incident.
            found = self.client.find_incident(incident["_id"]) if state.get("create_attempted_at") else None
            if not found:
                self.put(state, create_attempted_at=at)
                title = f"ChargeOps {incident['connector_id']} quá nhiệt"
                found = self.client.create_incident(title, incident["_id"])
            self.put(
                state,
                dd_incident_id=found["id"],
                dd_public_id=found["public_id"],
                dd_incident_url=self.client.incident_url(found["public_id"]),
            )
        self.sync_notebook(incident, state, at)
        for entry in incident.get("timeline", [])[state.get("synced_events", 0):]:
            self.client.incident_note(state["dd_incident_id"], note_text(incident, entry), at=entry["at"])
            self.put(state, synced_events=state.get("synced_events", 0) + 1)
        if incident["state"] == "resolved" and not state.get("dd_resolved"):
            self.client.resolve_incident(state["dd_incident_id"])
            self.put(state, dd_resolved=True)
        self.sync_postmortem(incident, state, at)

    def sync_notebook(self, incident, state, at):
        """Notebook create + attach. A Datadog failure here is recorded and retried later; it never blocks the
        timeline or resolve mirroring."""
        retry_at = state.get("notebook_next_attempt_at")
        if retry_at and retry_at.replace(tzinfo=at.tzinfo) > at:
            return
        try:
            if not state.get("notebook_id"):
                payload = investigation_notebook(incident, self.site, now=at)
                attrs = payload["data"]["attributes"]
                # A crash or lost response after the POST must not create a second notebook.
                found = (
                    self.client.find_notebook(incident["_id"], attrs["name"])
                    if state.get("notebook_create_attempted_at")
                    else None
                )
                if found is None:
                    self.put(state, notebook_create_attempted_at=at)
                    found = self.client.create_notebook(attrs)
                self.put(state, notebook_id=found, notebook_url=notebook_url(self.site, found))
            if not state.get("notebook_attached"):
                if state.get("notebook_attach_attempted_at"):
                    # attach_link is a POST that may have landed; prefer a missing link over a duplicate one.
                    event(self.log, "datadog_notebook_attach_skipped", incident_id=incident["_id"])
                else:
                    self.put(state, notebook_attach_attempted_at=at)
                    self.client.attach_link(state["dd_incident_id"], state["notebook_url"], "Notebook điều tra")
                self.put(state, notebook_attached=True)
            self.put(state, notebook_error=None, notebook_attempts=0, notebook_next_attempt_at=None)
        except DatadogError as error:
            attempts = state.get("notebook_attempts", 0) + 1
            delay = BACKOFF_S[min(attempts, len(BACKOFF_S)) - 1]
            self.put(
                state,
                notebook_error=f"HTTP {error.status}",
                notebook_attempts=attempts,
                notebook_next_attempt_at=at + timedelta(seconds=delay),
            )
            event(self.log, "datadog_notebook_failed", incident_id=incident["_id"], status=error.status)

    @staticmethod
    def definitely_rejected(error):
        """4xx and 503 mean Datadog did not act on the POST; status 0 / other 5xx may have landed."""
        return 400 <= error.status < 500 or error.status == 503

    def sync_postmortem(self, incident, state, at):
        """Once per closed incident, in three independently flagged parts: the $merge report, the Incident summary
        note and the notebook PUT (idempotent; waits for the notebook). A failure is recorded and retried with
        backoff; it never blocks the timeline or resolve mirroring, nor the other parts."""
        if not self.is_closed(incident) or (state.get("postmortem_done") and state.get("postmortem_notebook_updated")):
            return
        retry_at = state.get("postmortem_next_attempt_at")
        if retry_at and retry_at.replace(tzinfo=at.tzinfo) > at:
            return
        errors = []
        report = None
        try:
            if not state.get("postmortem_report_done"):
                self.db.incidents.aggregate(postmortem_pipeline(incident["_id"]))
            report = self.db.incident_reports.find_one({"_id": incident["_id"]})
            if report is None:
                raise LookupError("incident report missing")
            self.put(state, postmortem_report_done=True)
            if not state.get("outcome_emitted"):
                # Flag first: a missed SLO sample is preferable to counting one incident twice.
                self.put(state, outcome_emitted=True)
                emit_incident_outcome(self.stats, report)
        except (PyMongoError, LookupError) as error:
            errors.append(type(error).__name__)
        if report is not None and not state.get("postmortem_note_done") and state.get("dd_incident_id"):
            if state.get("postmortem_note_attempted_at"):
                # incident_note is a POST that may have landed; prefer a missing note over a duplicate one.
                event(self.log, "datadog_postmortem_note_skipped", incident_id=incident["_id"])
                self.put(state, postmortem_note_done=True)
            else:
                try:
                    self.put(state, postmortem_note_attempted_at=at)
                    self.client.incident_note(state["dd_incident_id"], report_summary(report), at=at)
                    self.put(state, postmortem_note_done=True)
                except DatadogError as error:
                    if self.definitely_rejected(error):
                        self.put(state, postmortem_note_attempted_at=None)
                    errors.append(f"HTTP {error.status}")
        if report is not None and not state.get("postmortem_notebook_updated") and state.get("notebook_id"):
            try:
                closed = {**incident, "resolved_at": incident.get("resolved_at") or report.get("closed_at")}
                payload = investigation_notebook(closed, self.site, postmortem_md=report_markdown(report), now=at)
                self.client.update_notebook(state["notebook_id"], payload["data"]["attributes"])
                self.put(state, postmortem_notebook_updated=True)
            except DatadogError as error:
                errors.append(f"HTTP {error.status}")
        if state.get("postmortem_report_done") and state.get("postmortem_note_done"):
            self.put(state, postmortem_done=True)
        if errors or not state.get("postmortem_notebook_updated"):
            attempts = state.get("postmortem_attempts", 0) + 1
            delay = BACKOFF_S[min(attempts, len(BACKOFF_S)) - 1]
            self.put(
                state,
                postmortem_error=",".join(errors) or None,
                postmortem_attempts=attempts,
                postmortem_next_attempt_at=at + timedelta(seconds=delay),
            )
            if errors:
                event(self.log, "datadog_postmortem_failed", incident_id=incident["_id"], error=",".join(errors))
        else:
            self.put(state, postmortem_error=None, postmortem_attempts=0, postmortem_next_attempt_at=None)

    def run_forever(self, interval=5):
        while True:
            try:
                self.sync_once(now())
            except PyMongoError as error:
                event(self.log, "datadog_sync_retry", error_type=type(error).__name__)
            time.sleep(interval)


def main():
    config = settings()
    client = DatadogClient(config.dd_api_key.get_secret_value(), config.dd_app_key.get_secret_value(), config.dd_site)
    stats = DogStatsd(host=config.dd_agent_host, port=8125)
    DatadogSync(database(), client, stats, logger("datadog-sync"), config.dd_site).run_forever()


if __name__ == "__main__":
    main()
