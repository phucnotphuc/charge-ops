import os
from datetime import UTC, datetime, timedelta

import pytest

from chargeops.postmortem import postmortem_pipeline, report_markdown, report_summary

T = datetime(2026, 10, 28, 3, 0, tzinfo=UTC)


def test_pipeline_shape():
    stages = postmortem_pipeline("inc-1")
    assert stages[0] == {"$match": {"_id": "inc-1"}}
    lookups = [s["$lookup"] for s in stages if "$lookup" in s]
    windows = next(item for item in lookups if item["from"] == "windows")
    text = str(windows["pipeline"])
    assert "$run_id" in text and "$connector_id" in text and "$end" in text and "$$opened" in text and "$$closed" in text
    assert any(item["from"] == "telemetry" for item in lookups)
    assert "$dateDiff" in str(stages)
    assert stages[-1] == {"$merge": {"into": "incident_reports", "whenMatched": "replace", "whenNotMatched": "insert"}}


def test_report_markdown_formats_durations_nulls_and_simulated_label():
    md = report_markdown(
        {"closed_by": "resolved", "seconds_to_ack": 125, "seconds_to_mitigated": None, "seconds_to_resolved": 60,
         "peak_residual_c": 7.5, "retest_resets": 2, "energy_kwh_while_derated": 0.25}
    )
    assert "2 phút 5 giây" in md and "1 phút 0 giây" in md
    assert "giảm thiểu: —" in md
    assert "Dữ liệu mô phỏng" in md
    assert "Dữ liệu mô phỏng" in report_summary({"closed_by": "resolved"})


@pytest.mark.mongo
def test_pipeline_on_real_mongo():
    from pymongo import MongoClient

    uri = os.environ.get("MONGODB_TEST_URI")
    if not uri:
        pytest.skip("MONGODB_TEST_URI not set")
    client = MongoClient(uri, tz_aware=True)
    db = client[f"chargeops_pm_{os.getpid()}"]
    try:
        db.incidents.insert_one(
            {"_id": "inc-1", "run_id": "r", "connector_id": "c", "opened_at": T, "reference_current_a": 60,
             "target_current_a": 40,
             "timeline": [
                 {"at": T, "event": "opened"},
                 {"at": T + timedelta(seconds=30), "event": "acknowledge"},
                 {"at": T + timedelta(seconds=90), "event": "resolved"},
             ]}
        )
        db.windows.insert_many(
            [{"run_id": "r", "connector_id": "c", "end": T + timedelta(seconds=15 * i), "residual_mean": v}
             for i, v in enumerate([1.0, 6.5, 3.0])]
        )
        list(db.incidents.aggregate(postmortem_pipeline("inc-1")))
        report = db.incident_reports.find_one({"_id": "inc-1"})
        assert report["peak_residual_c"] == 6.5
        assert report["seconds_to_resolved"] == 90 and report["seconds_to_ack"] == 30
        assert report["closed_by"] == "resolved" and report["simulated"] is True
    finally:
        client.drop_database(db.name)
        client.close()
