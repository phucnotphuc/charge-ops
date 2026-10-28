from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import mongomock
import pytest
from fastapi.testclient import TestClient

from chargeops.api import app
from chargeops.config import settings
from chargeops.simulator import Simulator
from chargeops.worker import tick

SIMULATOR = {"X-Simulator-Secret": "s" * 32}
OPERATOR = {"X-Operator-Secret": "o" * 32}
RUN = "00000000-0000-4000-8000-0000000000f1"
T0 = datetime(2026, 10, 21, 3, 0, tzinfo=UTC)  # on a 15-second boundary


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("SIMULATOR_SECRET", "s" * 32)
    monkeypatch.setenv("WEBHOOK_SECRET", "w" * 32)
    monkeypatch.setenv("OPERATOR_SECRET", "o" * 32)
    # Tests must not depend on a developer's local .env (pydantic-settings reads it).
    monkeypatch.setenv("DD_METRICS_ENABLED", "false")
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    settings.cache_clear()
    db = mongomock.MongoClient(tz_aware=True).chargeops
    db.incidents.create_index(
        [("run_id", 1), ("connector_id", 1), ("kind", 1)],
        unique=True,
        partialFilterExpression={"unresolved": True},
    )
    db.commands.create_index("idempotency_key", unique=True)
    db.runs.create_index("active_slot", unique=True, partialFilterExpression={"active_slot": "single"})
    app.state.db = db
    return db


class Harness:
    """Simulator, API and worker on one simulated clock; the simulator talks to the API over HTTP only."""

    def __init__(self, store, monkeypatch):
        self.store, self.clock = store, [T0]
        monkeypatch.setattr("chargeops.api.now", lambda: self.clock[0])
        monkeypatch.setattr("chargeops.worker.now", lambda: self.clock[0])
        self.client = TestClient(app)
        self.sim_client = TestClient(app, headers=SIMULATOR)
        self.simulator = Simulator(self.sim_client)
        self.stats = Mock()
        self.t = 0

    def start(self, scenario, seed, mode="local", profile=None):
        self.store.runs.insert_one(
            {
                "_id": RUN,
                "status": "active",
                "mode": mode,
                "scenario": scenario,
                "seed": seed,
                "profile_id": profile,
                "created_at": T0,
                "started_at": T0,
                "active_slot": "single",
                "stop_requested": False,
            }
        )

    def run_until(self, seconds, worker=True):
        while self.t <= seconds:
            self.clock[0] = T0 + timedelta(seconds=self.t)
            self.simulator.step(self.clock[0])
            if worker:
                tick(self.store, self.stats, Mock())
            self.t += 5

    def run_while(self, busy, limit):
        """Advance step by step while `busy()` holds, up to `limit` seconds."""
        while busy() and self.t <= limit:
            self.run_until(self.t)

    def command(self, incident_id, kind):
        response = self.client.post(
            f"/api/v1/incidents/{incident_id}/commands",
            json={"type": kind},
            headers={**OPERATOR, "Idempotency-Key": f"{incident_id}-{kind}"},
        )
        assert response.status_code == 202
        return response.json()["command_id"]

    def incidents(self):
        return list(self.store.incidents.find())


@pytest.fixture
def fleet(store, monkeypatch):
    return Harness(store, monkeypatch)
