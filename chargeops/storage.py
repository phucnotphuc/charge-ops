from datetime import timedelta

from pymongo import MongoClient
from pymongo.errors import CollectionInvalid

from chargeops.config import settings
from chargeops.domain import BASELINE_VERSION, MANIFEST, with_residuals


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
    db.telemetry.create_index([("meta.run_id", 1), ("meta.connector_id", 1), ("measured_at", 1)])
    db.runs.create_index("active_slot", unique=True, partialFilterExpression={"active_slot": "single"})
    db.incidents.create_index(
        [("run_id", 1), ("connector_id", 1), ("kind", 1)],
        unique=True,
        partialFilterExpression={"unresolved": True},
    )
    db.sessions.create_index("expires_at", expireAfterSeconds=0)
    db.run_events.create_index([("run_id", 1), ("consumed_at", 1), ("requested_at", 1)])
    db.run_events.create_index(
        [("run_id", 1), ("connector_id", 1)],
        name="pending_fault",
        unique=True,
        partialFilterExpression={"pending": True},
    )
    db.inbox.create_index([("processed", 1), ("occurred_at", 1)])
    db.commands.create_index("idempotency_key", unique=True)
    db.commands.create_index([("incident_id", 1), ("type", 1), ("applied_at", -1)])
    db.windows.create_index([("run_id", 1), ("connector_id", 1), ("end", -1)])
    db.sources.update_one(
        {"_id": MANIFEST},
        {
            "$setOnInsert": {
                "composition": "hybrid",
                "version": 1,
                "fields": {
                    "requested_current_a": "public: Autosun-PolyU v3 charginga, 1-min linearly interpolated to 5 s",
                    "voltage_v": "public: Autosun-PolyU v3 chargingv, 1-min linearly interpolated to 5 s",
                    "current_a": "derived: min(requested_current_a, current_limit_a)",
                    "power_kw": "derived: voltage_v * current_a / 1000",
                    "current_limit_a": "synthetic: 250 A rating, operator commands",
                    "connector_temperature_c": "synthetic: thermal lag model with injected fault",
                    "ambient_temperature_c": "synthetic",
                    "ids": "synthetic",
                    "residual": "derived",
                },
                "source": {
                    "doi": "10.17632/c7gg94tmvz.3",
                    "license": "CC BY 4.0",
                    "authors": "haosen yang, Jinpeng Tian, Weijie Mai, Chi-yung Chung",
                    "file": "chargeops/profiles/autosun_v3.csv (see data/manifests/autosun-v3.json)",
                },
                "baseline": (
                    f"{BASELINE_VERSION}: cold start at ambient, first-order lag (tau 60 s, compressed ~37x) "
                    "toward ambient + 0.0013 * current_a^2"
                ),
            }
        },
        upsert=True,
    )


def telemetry_pipeline(run_id, end, connector_id=None):
    """Whole run history up to `end` (one connector or the whole fleet), deduplicated by connector and sample_id;
    the thermal baseline needs it from run start."""
    match = {"meta.run_id": run_id, "measured_at": {"$lt": end}}
    if connector_id is not None:
        match["meta.connector_id"] = connector_id
    return [
        {"$match": match},
        {"$sort": {"received_at": 1}},
        {
            "$group": {
                "_id": {"connector": "$meta.connector_id", "sample": "$sample_id"},
                "sample": {"$first": "$$ROOT"},
            }
        },
        {"$replaceRoot": {"newRoot": "$sample"}},
        {"$project": {"_id": 0}},
        {"$sort": {"measured_at": 1}},
    ]


def recent(rows, end):
    """Lagged expected temperature and residual over the whole history; keep the 60-second window."""
    return [r for r in with_residuals(rows) if r["measured_at"] >= end - timedelta(seconds=60)]


def window_rows(db, run_id, end, connector_id):
    """One connector's samples of the 60-second window ending at `end`, with expected temperature and residual."""
    return recent(list(db.telemetry.aggregate(telemetry_pipeline(run_id, end, connector_id))), end)


def fleet_window_rows(db, run_id, end):
    """window_rows for every connector of the run from a single aggregation: {connector_id: rows}."""
    history = {}
    for row in db.telemetry.aggregate(telemetry_pipeline(run_id, end)):
        history.setdefault(row["meta"]["connector_id"], []).append(row)
    return {connector_id: recent(rows, end) for connector_id, rows in history.items()}
