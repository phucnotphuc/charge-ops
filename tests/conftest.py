import mongomock
import pytest

from chargeops.api import app
from chargeops.config import settings


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("SIMULATOR_SECRET", "s" * 32)
    monkeypatch.setenv("WEBHOOK_SECRET", "w" * 32)
    monkeypatch.setenv("OPERATOR_SECRET", "o" * 32)
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
    db.run_events.create_index(
        [("run_id", 1), ("connector_id", 1)],
        unique=True,
        partialFilterExpression={"pending": True},
    )
    app.state.db = db
    return db
