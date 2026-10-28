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
        if not state.get("dd_incident_id") or state.get("last_error"):
            return False
        caught_up = state.get("synced_events", 0) >= len(incident.get("timeline", []))
        return caught_up and (incident["state"] != "resolved" or state.get("dd_resolved", False))

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
        if not state.get("notebook_id"):
            payload = investigation_notebook(incident, self.site, now=at)
            notebook_id = self.client.create_notebook(payload["data"]["attributes"])
            self.put(state, notebook_id=notebook_id, notebook_url=notebook_url(self.site, notebook_id))
        if not state.get("notebook_attached"):
            self.client.attach_link(state["dd_incident_id"], state["notebook_url"], "Notebook điều tra")
            self.put(state, notebook_attached=True)
        for entry in incident.get("timeline", [])[state.get("synced_events", 0):]:
            self.client.incident_note(state["dd_incident_id"], note_text(incident, entry), at=entry["at"])
            self.put(state, synced_events=state.get("synced_events", 0) + 1)
        if incident["state"] == "resolved" and not state.get("dd_resolved"):
            self.client.resolve_incident(state["dd_incident_id"])
            self.put(state, dd_resolved=True)

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
