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


def test_resolved_incident_resolves_datadog_incident_once(monkeypatch):
    db, client, sync = setup(state="resolved", timeline=[{"at": T, "event": "opened"}, {"at": T, "event": "resolved"}])
    monkeypatch.setattr(db.incidents, "aggregate", lambda pipeline: iter(()))  # mongomock cannot run $merge
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
    client.find_notebook.return_value = None
    sync.sync_once(T)
    assert db.datadog_sync.find_one({"_id": "inc-1"})["notebook_error"] == "HTTP 503"
    sync.sync_once(T + timedelta(minutes=2))
    client.create_incident.assert_called_once()
    client.attach_link.assert_called_once()
    doc = db.datadog_sync.find_one({"_id": "inc-1"})
    assert doc["notebook_id"] == 12 and doc["notebook_error"] is None


def test_lost_notebook_response_adopts_existing_notebook_instead_of_creating_another():
    db, client, sync = setup()
    client.create_notebook.side_effect = DatadogError("POST", "/api/v1/notebooks", 504)
    client.find_notebook.return_value = 33
    sync.sync_once(T)  # POST attempted, response lost
    sync.sync_once(T + timedelta(minutes=2))
    client.create_notebook.assert_called_once()
    client.find_notebook.assert_called_once_with("inc-1", "Điều tra connector-4 [inc-1]")
    doc = db.datadog_sync.find_one({"_id": "inc-1"})
    assert doc["notebook_id"] == 33 and doc["notebook_url"].endswith("/notebook/33")
    client.attach_link.assert_called_once()


def test_notebook_api_down_does_not_block_timeline_or_resolve(monkeypatch):
    steps = [{"at": T, "event": "opened"}, {"at": T, "event": "resolved"}]
    db, client, sync = setup(state="resolved", timeline=steps)
    monkeypatch.setattr(db.incidents, "aggregate", lambda pipeline: iter(()))  # mongomock cannot run $merge
    client.create_notebook.side_effect = DatadogError("POST", "/api/v1/notebooks", 403)
    client.find_notebook.return_value = None
    sync.sync_once(T)
    assert client.incident_note.call_count == 2
    client.resolve_incident.assert_called_once_with("dd-1")
    doc = db.datadog_sync.find_one({"_id": "inc-1"})
    assert doc["notebook_error"] == "HTTP 403" and doc["synced_events"] == 2 and doc["dd_resolved"]
    assert not doc.get("last_error")
    sync.sync_once(T + timedelta(seconds=5))  # backoff: notebook is not hammered
    assert client.create_notebook.call_count == 1


def test_attach_is_not_repeated_after_an_attempt():
    db, client, sync = setup()
    client.attach_link.side_effect = DatadogError("POST", "/attachments", 503)
    sync.sync_once(T)
    assert db.datadog_sync.find_one({"_id": "inc-1"})["notebook_error"] == "HTTP 503"
    sync.sync_once(T + timedelta(minutes=2))
    client.create_notebook.assert_called_once()
    client.attach_link.assert_called_once()
    assert db.datadog_sync.find_one({"_id": "inc-1"})["notebook_attached"] is True


REPORT = {
    "_id": "inc-1", "run_id": RUN, "connector_id": "connector-4", "closed_at": T, "closed_by": "resolved",
    "seconds_to_ack": 30, "seconds_to_resolved": 90, "peak_residual_c": 6.5, "retest_resets": 0,
    "energy_kwh_while_derated": 0.1, "simulated": True,
}


def postmortem_setup(monkeypatch):
    steps = [{"at": T, "event": "opened"}, {"at": T, "event": "resolved"}]
    db, client, sync = setup(state="resolved", timeline=steps)
    calls = []

    def fake_aggregate(pipeline):
        calls.append(pipeline)
        db.incident_reports.replace_one({"_id": "inc-1"}, REPORT, upsert=True)
        return iter(())

    monkeypatch.setattr(db.incidents, "aggregate", fake_aggregate)
    return db, client, sync, calls


def test_postmortem_runs_once_after_resolve(monkeypatch):
    db, client, sync, calls = postmortem_setup(monkeypatch)
    sync.sync_once(T)
    sync.sync_once(T + timedelta(seconds=5))
    assert len(calls) == 1 and calls[0][-1]["$merge"]["into"] == "incident_reports"
    client.update_notebook.assert_called_once()
    assert client.update_notebook.call_args.args[0] == 12
    attrs = client.update_notebook.call_args.args[1]
    assert attrs["metadata"]["type"] == "postmortem" and "Dữ liệu mô phỏng" in str(attrs["cells"][-1])
    assert "Postmortem" in client.incident_note.call_args_list[-1].args[1]
    assert db.datadog_sync.find_one({"_id": "inc-1"})["postmortem_done"] is True


def test_postmortem_for_run_stopped_unresolved_incident(monkeypatch):
    db, client, sync, calls = postmortem_setup(monkeypatch)
    db.incidents.update_one({"_id": "inc-1"}, {"$set": {"state": "open", "unresolved": True, "run_stopped_at": T}})
    sync.sync_once(T)
    assert len(calls) == 1
    client.resolve_incident.assert_not_called()
    assert db.datadog_sync.find_one({"_id": "inc-1"})["postmortem_done"] is True


def test_no_postmortem_while_incident_open(monkeypatch):
    db, client, sync, calls = postmortem_setup(monkeypatch)
    db.incidents.update_one({"_id": "inc-1"}, {"$set": {"state": "open", "unresolved": True}})
    sync.sync_once(T)
    assert calls == [] and not db.datadog_sync.find_one({"_id": "inc-1"}).get("postmortem_done")


def test_postmortem_failure_is_retried_and_never_blocks_resolve(monkeypatch):
    db, client, sync, calls = postmortem_setup(monkeypatch)
    client.update_notebook.side_effect = [DatadogError("PUT", "/api/v1/notebooks/12", 503), None]
    sync.sync_once(T)
    doc = db.datadog_sync.find_one({"_id": "inc-1"})
    assert doc["postmortem_error"] == "HTTP 503" and doc["postmortem_done"] and not doc.get("postmortem_notebook_updated")
    assert doc["dd_resolved"] and doc["synced_events"] == 2 and not doc.get("last_error")
    sync.sync_once(T + timedelta(seconds=5))  # backoff
    assert len(calls) == 1
    sync.sync_once(T + timedelta(minutes=2))
    doc = db.datadog_sync.find_one({"_id": "inc-1"})
    assert doc["postmortem_notebook_updated"] is True and doc["postmortem_error"] is None
    assert client.update_notebook.call_count == 2
    assert len(postmortem_notes(client)) == 1


def postmortem_notes(client):
    return [c for c in client.incident_note.call_args_list if "Postmortem" in c.args[1]]


def test_notebook_403_does_not_block_report_note_or_done_and_put_happens_once_when_notebook_exists(monkeypatch):
    db, client, sync, calls = postmortem_setup(monkeypatch)
    client.create_notebook.side_effect = DatadogError("POST", "/api/v1/notebooks", 403)
    client.find_notebook.return_value = None
    sync.sync_once(T)
    doc = db.datadog_sync.find_one({"_id": "inc-1"})
    assert doc["postmortem_report_done"] and doc["postmortem_note_done"] and doc["postmortem_done"]
    assert not doc.get("postmortem_notebook_updated") and len(postmortem_notes(client)) == 1
    client.update_notebook.assert_not_called()
    client.create_notebook.side_effect = None
    client.create_notebook.return_value = 12
    sync.sync_once(T + timedelta(minutes=10))
    sync.sync_once(T + timedelta(minutes=20))
    client.update_notebook.assert_called_once()
    assert len(calls) == 1 and len(postmortem_notes(client)) == 1
    assert db.datadog_sync.find_one({"_id": "inc-1"})["postmortem_notebook_updated"] is True


def test_note_rejected_with_503_is_retried(monkeypatch):
    db, client, sync, calls = postmortem_setup(monkeypatch)
    real = client.incident_note
    outcomes = iter([None, None, DatadogError("POST", "/todos", 503)])  # two mirrored steps, then postmortem
    real.side_effect = lambda *a, **k: (lambda o: (_ for _ in ()).throw(o) if o else None)(next(outcomes, None))
    sync.sync_once(T)
    assert not db.datadog_sync.find_one({"_id": "inc-1"}).get("postmortem_note_done")
    sync.sync_once(T + timedelta(minutes=2))
    doc = db.datadog_sync.find_one({"_id": "inc-1"})
    assert doc["postmortem_note_done"] and doc["postmortem_done"]
    assert len(postmortem_notes(client)) == 2  # failed attempt + successful retry


def test_note_with_ambiguous_failure_is_not_repeated(monkeypatch):
    db, client, sync, calls = postmortem_setup(monkeypatch)
    outcomes = iter([None, None, DatadogError("POST", "/todos", 0)])
    client.incident_note.side_effect = lambda *a, **k: (lambda o: (_ for _ in ()).throw(o) if o else None)(
        next(outcomes, None)
    )
    sync.sync_once(T)
    sync.sync_once(T + timedelta(minutes=2))
    assert len(postmortem_notes(client)) == 1
    assert db.datadog_sync.find_one({"_id": "inc-1"})["postmortem_note_done"] is True
