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
    client.create_notebook.return_value = 12
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


def test_note_failure_midway_resumes_without_resending_sent_steps():
    steps = [{"at": T, "event": e} for e in ("opened", "acknowledge", "resolved_step")]
    db, client, sync = setup(timeline=steps)
    client.incident_note.side_effect = [None, DatadogError("POST", "/todos", 503), None, None]
    sync.sync_once(T)
    assert db.datadog_sync.find_one({"_id": "inc-1"})["synced_events"] == 1
    sync.sync_once(T + timedelta(minutes=2))
    events = [c.args[1].split(" · ")[1] for c in client.incident_note.call_args_list]
    assert events == ["opened", "acknowledge", "acknowledge", "resolved_step"]
    assert db.datadog_sync.find_one({"_id": "inc-1"})["synced_events"] == 3


def test_malformed_response_is_recorded_and_next_incident_still_syncs():
    db, client, sync = setup()
    db.incidents.insert_one(
        {"_id": "inc-2", "run_id": RUN, "connector_id": "connector-5", "state": "open", "opened_at": T,
         "unresolved": True, "timeline": [{"at": T, "event": "opened"}]}
    )
    client.create_incident.side_effect = [DatadogError("POST", "/api/v2/incidents", 201), {"id": "dd-2", "public_id": 8}]
    sync.sync_once(T)
    assert "201" in db.datadog_sync.find_one({"_id": "inc-1"})["last_error"]
    assert db.datadog_sync.find_one({"_id": "inc-2"})["dd_incident_id"] == "dd-2"


def test_incident_api_exposes_datadog_links(store):
    store.incidents.insert_one({"_id": "inc-9", "run_id": RUN, "connector_id": "connector-4", "state": "open"})
    http = TestClient(app)
    assert http.get("/api/v1/incidents/inc-9").json()["datadog_incident_url"] is None
    store.datadog_sync.insert_one(
        {"_id": "inc-9", "dd_incident_url": "https://app.datadoghq.com/incidents/1", "notebook_url": "https://nb"}
    )
    body = http.get("/api/v1/incidents/inc-9").json()
    assert body["datadog_incident_url"].endswith("/incidents/1") and body["datadog_notebook_url"] == "https://nb"


def test_notebook_created_and_attached_once():
    db, client, sync = setup()
    sync.sync_once(T)
    sync.sync_once(T + timedelta(seconds=5))
    client.create_notebook.assert_called_once()
    assert client.create_notebook.call_args.args[0]["name"].endswith("[inc-1]")
    client.attach_link.assert_called_once_with("dd-1", "https://app.ap1.datadoghq.com/notebook/12", "Notebook điều tra")
    doc = db.datadog_sync.find_one({"_id": "inc-1"})
    assert doc["notebook_id"] == 12 and doc["notebook_url"].endswith("/notebook/12")


def test_notebook_failure_is_retried_without_duplicating_the_incident():
    db, client, sync = setup()
    client.create_notebook.side_effect = [DatadogError("POST", "/api/v1/notebooks", 503), 12]
    sync.sync_once(T)
    assert "503" in db.datadog_sync.find_one({"_id": "inc-1"})["last_error"]
    sync.sync_once(T + timedelta(minutes=2))
    client.create_incident.assert_called_once()
    client.attach_link.assert_called_once()
    assert db.datadog_sync.find_one({"_id": "inc-1"})["notebook_id"] == 12


def test_attach_failure_retries_attach_only():
    db, client, sync = setup()
    client.attach_link.side_effect = [DatadogError("POST", "/attachments", 503), None]
    sync.sync_once(T)
    sync.sync_once(T + timedelta(minutes=2))
    client.create_notebook.assert_called_once()
    assert client.attach_link.call_count == 2
