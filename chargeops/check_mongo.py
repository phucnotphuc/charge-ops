"""Real MongoDB smoke test in a disposable, uniquely named database."""

from datetime import timedelta
from uuid import uuid4

from pymongo.errors import DuplicateKeyError

from chargeops.domain import CONNECTOR, block_end, now, window_evidence
from chargeops.storage import database, initialize, telemetry_pipeline
from chargeops.worker import open_incident


def main():
    client = database().client
    name = "chargeops_smoke_" + uuid4().hex
    db = client[name]
    try:
        initialize(db)
        info = next(db.list_collections(filter={"name": "telemetry"}))
        assert info["options"]["timeseries"]["timeField"] == "measured_at"
        assert info["options"]["expireAfterSeconds"] == 1209600
        run_id, end = str(uuid4()), block_end(now())
        db.runs.insert_one({"_id": run_id, "active_slot": "single"})
        try:
            db.runs.insert_one({"_id": str(uuid4()), "active_slot": "single"})
        except DuplicateKeyError:
            pass
        else:
            raise AssertionError("Single-run unique index not enforced")
        samples = [
            {
                "sample_id": str(i),
                "meta": {"run_id": run_id, "connector_id": CONNECTOR},
                "measured_at": end - timedelta(seconds=60 - i * 5),
                "received_at": end,
                "ambient_temperature_c": 25,
                "current_a": 60,
                "connector_temperature_c": 51.4,
            }
            for i in range(12)
        ]
        db.telemetry.insert_many(samples)
        # Retry with same sample identity, but a new MongoDB document identity.
        db.telemetry.insert_one({k: v for k, v in samples[0].items() if k != "_id"})
        rows = list(db.telemetry.aggregate(telemetry_pipeline(run_id, end)))
        evidence = window_evidence(rows, end)
        assert evidence["count"] == 12 and evidence["quality"]
        assert abs(evidence["residual_mean"] - 12) < 0.00001
        run = {"_id": run_id}
        open_incident(db, run, evidence, "local", end, "smoke")
        open_incident(db, run, evidence, "local", end, "smoke-retry")
        assert db.incidents.count_documents({}) == 1
        print("PASS: real time-series TTL, aggregation dedup/residual, unique active run and incident")
    finally:
        client.drop_database(name)
        client.close()


if __name__ == "__main__":
    main()
