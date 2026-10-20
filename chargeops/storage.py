from datetime import timedelta

from pymongo import MongoClient
from pymongo.errors import CollectionInvalid

from chargeops.config import settings
from chargeops.domain import BASELINE_VERSION, CONNECTOR, MANIFEST, with_residuals


def database():
    s = settings()
    return MongoClient(s.mongodb_uri, tz_aware=True, serverSelectionTimeoutMS=5000)[s.mongodb_database]


def initialize(db):
    try:
        db.create_collection(
            "telemetry",
            timeseries={"timeField": "measured_at", "metaField": "meta", "granularity": "seconds"},
            expireAfterSeconds=1209600,
        )
    except CollectionInvalid:
        pass
    db.telemetry.create_index([("meta.run_id", 1), ("measured_at", 1)])
    db.runs.create_index("active_slot", unique=True, partialFilterExpression={"active_slot": "single"})
    db.incidents.create_index(
        [("run_id", 1), ("connector_id", 1), ("kind", 1)],
        unique=True,
        partialFilterExpression={"unresolved": True},
    )
    db.inbox.create_index([("processed", 1), ("occurred_at", 1)])
    db.commands.create_index("idempotency_key", unique=True)
    db.commands.create_index([("incident_id", 1), ("type", 1), ("applied_at", -1)])
    db.windows.create_index([("run_id", 1), ("end", -1)])
    db.sources.update_one(
        {"_id": MANIFEST},
        {
            "$setOnInsert": {
                "composition": "synthetic",
                "version": 1,
                "fields": {
                    "electrical": "synthetic",
                    "temperature": "synthetic",
                    "ids": "synthetic",
                    "residual": "derived",
                },
                "baseline": f"{BASELINE_VERSION}: first-order lag (tau 45 s) toward ambient + 0.004 * current_a^2",
            }
        },
        upsert=True,
    )


def telemetry_pipeline(run_id, end):
    """Whole run history up to `end`, deduplicated by sample_id; the thermal baseline needs it from run start."""
    return [
        {"$match": {"meta.run_id": run_id, "meta.connector_id": CONNECTOR, "measured_at": {"$lt": end}}},
        {"$sort": {"received_at": 1}},
        {"$group": {"_id": "$sample_id", "sample": {"$first": "$$ROOT"}}},
        {"$replaceRoot": {"newRoot": "$sample"}},
        {"$project": {"_id": 0}},
        {"$sort": {"measured_at": 1}},
    ]


def window_rows(db, run_id, end):
    """Samples of the 60-second window ending at `end`, with lagged expected temperature and residual."""
    rows = with_residuals(list(db.telemetry.aggregate(telemetry_pipeline(run_id, end))))
    return [r for r in rows if r["measured_at"] >= end - timedelta(seconds=60)]
