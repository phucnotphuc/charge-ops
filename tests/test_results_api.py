"""Read-only evaluation summaries and `Z` timestamps on raw-document endpoints."""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chargeops import results
from chargeops.api import app, utc_z

COMMITTED = Path(__file__).parents[1] / "data" / "evaluation"


@pytest.fixture
def client(store):
    return TestClient(app)


def test_committed_v31_summary_is_listed_and_served(client):
    listing = client.get("/api/v1/evaluations")
    assert listing.status_code == 200
    assert listing.json()["next_cursor"] is None
    item = {i["id"]: i for i in listing.json()["items"]}["b1-eval-v3.1"]
    assert item["protocol_version"] == "b1-eval-v3.1" and item["simulation_only"] is True
    assert (item["fault_seeds"], item["healthy_seeds"], item["runs"]) == (20, 20, 120)

    detail = client.get("/api/v1/evaluations/b1-eval-v3.1").json()
    raw = json.loads((COMMITTED / "b1-eval-v3.1" / "summary.json").read_text(encoding="utf-8"))
    assert set(detail["arms"]) == {"A", "B", "C"} and set(detail["detectors"]) == {"fixed", "residual"}
    assert detail["arms"]["C"]["fault"] == raw["arms"]["C"]["fault"]
    assert detail["detectors"]["residual"] == raw["detectors"]["residual"]
    assert detail["targets"] == raw["targets"] and detail["fingerprint"] == raw["fingerprint"]
    assert detail["summary_markdown"].startswith("# Evaluation summary")
    assert "seeds" not in detail  # per-seed detail stays in the file


def test_missing_values_stay_null(client):
    detail = client.get("/api/v1/evaluations/b1-eval-v3.1").json()
    assert detail["arms"]["B"]["fault"]["effective_mitigation_s"] == {
        "n": 0,
        "median": None,
        "min": None,
        "max": None,
    }


@pytest.mark.parametrize("bad", ["nope", "..", ".hidden", "a%20b", "x" * 65])
def test_unknown_or_unsafe_id_is_404(client, bad):
    response = client.get(f"/api/v1/evaluations/{bad}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_empty_results_dir(client, monkeypatch, tmp_path):
    monkeypatch.setattr(results, "RESULTS_DIR", tmp_path / "none")
    assert client.get("/api/v1/evaluations").json() == {"items": [], "next_cursor": None}


def test_summary_without_markdown(client, monkeypatch, tmp_path):
    (tmp_path / "copy").mkdir()
    shutil.copy(COMMITTED / "b1-eval-v3.1" / "summary.json", tmp_path / "copy" / "summary.json")
    monkeypatch.setattr(results, "RESULTS_DIR", tmp_path)
    assert client.get("/api/v1/evaluations/copy").json()["summary_markdown"] is None


def test_utc_z():
    assert utc_z(datetime(2026, 10, 21, 8, 0, tzinfo=UTC)) == "2026-10-21T08:00:00Z"
    assert utc_z(datetime(2026, 10, 21, 8, 0)) == "2026-10-21T08:00:00Z"


def test_raw_document_endpoints_emit_z(client, store):
    at = datetime(2026, 10, 21, 8, 0, 0, 123000, tzinfo=UTC)
    store.incidents.insert_one(
        {
            "_id": "inc-1",
            "run_id": "r1",
            "connector_id": "connector-1",
            "state": "open",
            "opened_at": at,
            "timeline": [{"at": at, "event": "opened"}],
        }
    )
    store.commands.insert_one({"_id": "c1", "incident_id": "inc-1", "type": "acknowledge", "created_at": at})
    store.runs.insert_one({"_id": "r1", "status": "stopped", "created_at": at})
    incident = client.get("/api/v1/incidents/inc-1").json()
    assert incident["opened_at"] == "2026-10-21T08:00:00.123000Z"
    assert incident["timeline"][0]["at"] == "2026-10-21T08:00:00.123000Z"
    assert incident["commands"][0]["created_at"].endswith("Z")
    assert client.get("/api/v1/incidents").json()["items"][0]["opened_at"].endswith("Z")
    assert client.get("/api/v1/commands/c1").json()["created_at"].endswith("Z")
    assert client.get("/api/v1/demo-runs").json()["items"][0]["created_at"].endswith("Z")
    assert "+00:00" not in client.get("/api/v1/incidents/inc-1").text
