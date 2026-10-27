import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from chargeops.api import LOCATION, app
from chargeops.insights import fleet_pipeline, incident_summary_pipeline, shape_summary, trend_pipeline

RUN = "00000000-0000-4000-8000-0000000000b1"
T0 = datetime(2026, 10, 25, 3, 0, tzinfo=UTC)


def test_trend_pipeline_uses_window_functions_on_quality_windows():
    pipeline = trend_pipeline(RUN, "connector-3")
    assert pipeline[0] == {"$match": {"run_id": RUN, "connector_id": "connector-3", "quality": True}}
    stage = next(s["$setWindowFields"] for s in pipeline if "$setWindowFields" in s)
    assert stage["sortBy"] == {"end": 1}
    out = stage["output"]
    assert out["residual_avg_5m"]["window"] == {"range": [-300, 0], "unit": "second"}
    assert out["residual_slope_c_per_min"]["$derivative"] == {"input": "$residual_mean", "unit": "minute"}
    assert pipeline[-1] == {"$sort": {"end": 1}}


def test_fleet_pipeline_takes_latest_window_per_connector(store):
    for i, residual in enumerate([1.0, 2.0, 9.5]):
        store.windows.insert_one(
            {"_id": f"w{i}", "run_id": RUN, "connector_id": "connector-4", "end": T0 + timedelta(seconds=15 * i), "quality": True, "residual_mean": residual}
        )
    store.windows.insert_one(
        {"_id": "w9", "run_id": RUN, "connector_id": "connector-1", "end": T0, "quality": False, "residual_mean": None}
    )
    rows = {r["connector_id"]: r for r in store.windows.aggregate(fleet_pipeline(RUN))}
    assert rows["connector-4"]["residual_mean"] == 9.5
    assert rows["connector-1"]["quality"] is False and rows["connector-1"]["residual_mean"] is None


def test_fleet_route(store):
    store.windows.insert_one(
        {"_id": "w1", "run_id": RUN, "connector_id": "connector-2", "end": T0, "quality": True, "residual_mean": 4.0}
    )
    body = TestClient(app).get(f"/api/v1/insights/fleet?run_id={RUN}").json()
    assert body["windows"] == [{"connector_id": "connector-2", "end": "2026-10-25T03:00:00Z", "quality": True, "residual_mean": 4.0}]


def test_summary_pipeline_facets():
    facet = incident_summary_pipeline(RUN)[1]["$facet"]
    assert set(facet) == {"by_state", "by_connector", "durations"}
    assert incident_summary_pipeline(None)[0] == {"$match": {}}


def test_summary_pipeline_filters_by_connectors():
    assert incident_summary_pipeline("r", ["connector-4"])[0] == {
        "$match": {"run_id": "r", "connector_id": {"$in": ["connector-4"]}}
    }


def test_incident_insights_station_filter_passes_connectors(monkeypatch):
    from chargeops import insights

    seen = []
    real = insights.incident_summary_pipeline
    monkeypatch.setattr(insights, "incident_summary_pipeline", lambda r, c=None: seen.append((r, c)) or real(r, c))
    assert TestClient(app).get("/api/v1/insights/incidents?run_id=r&station_id=station-2").status_code == 200
    assert seen[0][0] == "r" and seen[0][1]
    assert all(LOCATION[c][0] == "station-2" for c in seen[0][1])
    assert TestClient(app).get("/api/v1/insights/incidents?station_id=bogus").status_code == 422


def test_shape_summary_fills_missing_values_with_none():
    doc = {"by_state": [{"_id": "open", "count": 2}], "by_connector": [], "durations": []}
    summary = shape_summary(doc)
    assert summary.by_state[0].state == "open"
    assert summary.mtta_s is None and summary.mttr_s is None and summary.total == 2


def test_trend_route_shapes_rows(store, monkeypatch):
    rows = [{"end": T0, "residual_mean": 1.0, "residual_avg_5m": 1.0, "residual_slope_c_per_min": None, "residual_max_15m": 1.0}]
    monkeypatch.setattr(type(store.windows), "aggregate", lambda self, pipeline: iter(rows))
    response = TestClient(app).get(f"/api/v1/connectors/connector-3/trend?run_id={RUN}")
    assert response.status_code == 200
    body = response.json()
    assert body["connector_id"] == "connector-3"
    assert body["points"][0]["end"].endswith("Z")
    assert body["points"][0]["residual_slope_c_per_min"] is None


def test_trend_route_rejects_unknown_connector(store):
    response = TestClient(app).get(f"/api/v1/connectors/connector-9/trend?run_id={RUN}")
    assert response.status_code == 422


def test_incident_summary_route(store, monkeypatch):
    doc = {
        "by_state": [{"_id": "resolved", "count": 1}],
        "by_connector": [{"_id": "connector-4", "count": 1, "open": 0}],
        "durations": [{"_id": None, "mtta_s": 30.0, "mttm_s": 160.0, "mttr_s": 840.0}],
    }
    monkeypatch.setattr(type(store.incidents), "aggregate", lambda self, pipeline: iter([doc]))
    body = TestClient(app).get(f"/api/v1/insights/incidents?run_id={RUN}").json()
    assert body["total"] == 1 and body["mttr_s"] == 840.0
    assert body["by_connector"] == [{"connector_id": "connector-4", "count": 1, "open": 0}]


@pytest.mark.mongo
@pytest.mark.skipif(not os.getenv("MONGODB_TEST_URI"), reason="needs a real MongoDB (MONGODB_TEST_URI)")
def test_trend_pipeline_on_real_mongo():
    from pymongo import MongoClient

    db = MongoClient(os.environ["MONGODB_TEST_URI"], tz_aware=True).chargeops_insights_test
    db.windows.drop()
    db.windows.insert_many(
        [
            {"run_id": RUN, "connector_id": "connector-3", "end": T0 + timedelta(seconds=15 * i), "quality": True, "residual_mean": float(i)}
            for i in range(12)
        ]
    )
    rows = list(db.windows.aggregate(trend_pipeline(RUN, "connector-3")))
    assert len(rows) == 12
    assert rows[-1]["residual_slope_c_per_min"] == pytest.approx(4.0)  # +1 °C per 15 s
    assert rows[-1]["residual_max_15m"] == 11.0
    db.windows.drop()
