"""Real MongoDB smoke test in a disposable, uniquely named database."""

from datetime import timedelta
from uuid import uuid4

from pymongo.errors import DuplicateKeyError

from chargeops.domain import block_end, now, window_evidence, with_residuals
from chargeops.insights import trend_pipeline
from chargeops.lifecycle import open_incident
from chargeops.storage import database, initialize, window_rows


def main():
    client = database().client
    name = "chargeops_smoke_" + uuid4().hex
    db = client[name]
    try:
        initialize(db)
        info = next(db.list_collections(filter={"name": "telemetry"}))
        assert info["options"]["timeseries"]["timeField"] == "measured_at"
        assert info["options"]["expireAfterSeconds"] == 1209600
        check_loop(db)
        check_trend(db)
        print("PASS: real time-series TTL, aggregation dedup/residual, unique active run and incident")
    finally:
        client.drop_database(name)
        client.close()


def check_loop(db, connector_id="connector-1"):
    """Unique active run, deduplicated window aggregation with residuals, one unresolved incident per connector."""
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
            "meta": {"run_id": run_id, "connector_id": connector_id},
            "measured_at": end - timedelta(seconds=60 - i * 5),
            "received_at": end,
            "ambient_temperature_c": 25,
            "current_a": 60,
            "connector_temperature_c": 51.4,
        }
        for i in range(12)
    ]
    expected = with_residuals([dict(s) for s in samples])
    db.telemetry.insert_many(samples)
    # Retry with same sample identity, but a new MongoDB document identity.
    db.telemetry.insert_one({k: v for k, v in samples[0].items() if k != "_id"})
    evidence = window_evidence(window_rows(db, run_id, end, connector_id), end)
    assert evidence["count"] == 12 and evidence["quality"]
    # Deduplicated pipeline + baseline on real MongoDB equals the baseline on the 12 distinct samples.
    assert abs(evidence["residual_mean"] - sum(r["residual"] for r in expected) / 12) < 0.00001
    evidence["connector_id"] = connector_id
    run = {"_id": run_id}
    open_incident(db, run, evidence, "local", end, "smoke")
    open_incident(db, run, evidence, "local", end, "smoke-retry")
    assert db.incidents.count_documents({"run_id": run_id, "connector_id": connector_id}) == 1


def check_trend(db, connector_id="connector-1"):
    """$setWindowFields trend on real MongoDB (mongomock has no window functions)."""
    run_id, end = str(uuid4()), block_end(now())
    db.windows.insert_many(
        [
            {
                "run_id": run_id,
                "connector_id": connector_id,
                "end": end - timedelta(seconds=15 * (11 - i)),
                "quality": True,
                "residual_mean": float(i),
            }
            for i in range(12)
        ]
    )
    trend = list(db.windows.aggregate(trend_pipeline(run_id, connector_id)))
    assert len(trend) == 12 and abs(trend[-1]["residual_slope_c_per_min"] - 4.0) < 0.001
    print(f"trend points: {len(trend)}; $setWindowFields ok")


if __name__ == "__main__":
    main()
