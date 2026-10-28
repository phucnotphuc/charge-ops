from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import mongomock
from fastapi.testclient import TestClient

from chargeops.api import app
from chargeops.datadog_client import DatadogError
from chargeops.datadog_sync import DatadogSync

T = datetime(2026, 10, 28, 3, 0, tzinfo=UTC)
RUN = "00000000-0000-4000-8000-0000000000f9"


def setup(mode="datadog", timeline=None, state="open"):
    db = mongomock.MongoClient(tz_aware=True).chargeops
    db.runs.insert_one({"_id": RUN, "mode": mode, "status": "active"})
    db.incidents.insert_one(
        {"_id": "inc-1", "run_id": RUN, "connector_id": "connector-4", "state": state, "state_since": T,
         "opened_at": T, "unresolved": state != "resolved",
         "timeline": timeline or [{"at": T, "event": "opened", "source": "datadog"}]}
    )
    client = Mock()
    client.create_incident.return_value = {"id": "dd-1", "public_id": 7}
    client.incident_url.return_value = "https://app.ap1.datadoghq.com/incidents/7"
    client.find_incident.return_value = None
    return db, client, DatadogSync(db, client, Mock(), Mock(), "ap1.datadoghq.com")


def test_creates_one_datadog_incident_and_saves_the_id():
    db, client, sync = setup()
    sync.sync_once(T)
    sync.sync_once(T + timedelta(seconds=5))
    client.create_incident.assert_called_once()
    doc = db.datadog_sync.find_one({"_id": "inc-1"})
    assert doc["dd_incident_id"] == "dd-1" and doc["dd_incident_url"].endswith("/incidents/7")


def test_local_mode_runs_are_never_synced():
    db, client, sync = setup(mode="local")
    sync.sync_once(T)
    client.create_incident.assert_not_called()


def test_each_timeline_step_is_mirrored_once():
    db, client, sync = setup()
    sync.sync_once(T)
    db.incidents.update_one({"_id": "inc-1"}, {"$push": {"timeline": {"at": T, "event": "acknowledge"}}})
    sync.sync_once(T)
    sync.sync_once(T)
    texts = [c.args[1] for c in client.incident_note.call_args_list]
    assert len(texts) == 2 and "acknowledge" in texts[1]


def test_resolved_incident_resolves_datadog_incident_once():
    db, client, sync = setup(state="resolved", timeline=[{"at": T, "event": "opened"}, {"at": T, "event": "resolved"}])
    sync.sync_once(T)
    sync.sync_once(T)
    client.resolve_incident.assert_called_once_with("dd-1")


def test_api_error_is_recorded_and_retried_later_without_touching_the_incident():
    db, client, sync = setup()
    client.create_incident.side_effect = [DatadogError("POST", "/api/v2/incidents", 503), {"id": "dd-1", "public_id": 7}]
    before = db.incidents.find_one({"_id": "inc-1"})
    sync.sync_once(T)
    doc = db.datadog_sync.find_one({"_id": "inc-1"})
    assert doc["attempts"] == 1 and "503" in doc["last_error"]
    assert db.incidents.find_one({"_id": "inc-1"}) == before
    sync.sync_once(T + timedelta(minutes=2))  # past next_attempt_at
    assert db.datadog_sync.find_one({"_id": "inc-1"})["dd_incident_id"] == "dd-1"


def test_restart_after_create_without_saved_id_reuses_existing_incident():
    db, client, sync = setup()
    db.datadog_sync.insert_one({"_id": "inc-1", "run_id": RUN, "create_attempted_at": T, "synced_events": 0, "attempts": 0})
    client.find_incident.return_value = {"id": "dd-old", "public_id": 3}
    sync.sync_once(T)
    client.create_incident.assert_not_called()
    assert db.datadog_sync.find_one({"_id": "inc-1"})["dd_incident_id"] == "dd-old"


def test_incident_api_exposes_datadog_links(store):
    store.incidents.insert_one({"_id": "inc-9", "run_id": RUN, "connector_id": "connector-4", "state": "open"})
    http = TestClient(app)
    assert http.get("/api/v1/incidents/inc-9").json()["datadog_incident_url"] is None
    store.datadog_sync.insert_one(
        {"_id": "inc-9", "dd_incident_url": "https://app.datadoghq.com/incidents/1", "notebook_url": "https://nb"}
    )
    body = http.get("/api/v1/incidents/inc-9").json()
    assert body["datadog_incident_url"].endswith("/incidents/1") and body["datadog_notebook_url"] == "https://nb"
