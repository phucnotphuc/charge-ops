"""Read-only MongoDB analytics for operators: window-function residual trend per connector and an incident
summary (counts by state/connector, MTTA/MTTM/MTTR) computed in one $facet aggregation."""

from datetime import datetime

from pydantic import BaseModel


def trend_pipeline(run_id, connector_id, limit=240):
    """Newest `limit` quality windows (15 s apart → 60 min), oldest first, with moving average, slope and max."""
    return [
        {"$match": {"run_id": run_id, "connector_id": connector_id, "quality": True}},
        {"$sort": {"end": -1}},
        {"$limit": limit},
        {
            "$setWindowFields": {
                "partitionBy": "$connector_id",
                "sortBy": {"end": 1},
                "output": {
                    "residual_avg_5m": {
                        "$avg": "$residual_mean",
                        "window": {"range": [-300, 0], "unit": "second"},
                    },
                    "residual_slope_c_per_min": {
                        "$derivative": {"input": "$residual_mean", "unit": "minute"},
                        "window": {"range": [-120, 0], "unit": "second"},
                    },
                    "residual_max_15m": {
                        "$max": "$residual_mean",
                        "window": {"range": [-900, 0], "unit": "second"},
                    },
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "end": 1,
                "residual_mean": 1,
                "residual_avg_5m": 1,
                "residual_slope_c_per_min": 1,
                "residual_max_15m": 1,
            }
        },
        {"$sort": {"end": 1}},
    ]


def fleet_pipeline(run_id):
    """Latest 60-second window of every connector in the run (quality and residual mean), one row per connector."""
    return [
        {"$match": {"run_id": run_id}},
        {"$project": {"connector_id": 1, "end": 1, "quality": 1, "residual_mean": 1}},  # drop `samples` pre-sort
        {"$sort": {"end": -1}},
        {
            "$group": {
                "_id": "$connector_id",
                "end": {"$first": "$end"},
                "quality": {"$first": "$quality"},
                "residual_mean": {"$first": "$residual_mean"},
            }
        },
        {"$project": {"_id": 0, "connector_id": "$_id", "end": 1, "quality": 1, "residual_mean": 1}},
        {"$sort": {"connector_id": 1}},
    ]


def first_event_at(name):
    return {
        "$first": {
            "$map": {
                "input": {"$filter": {"input": "$timeline", "cond": {"$eq": ["$$this.event", name]}}},
                "in": "$$this.at",
            }
        }
    }


def seconds_between(start, end):
    return {"$dateDiff": {"startDate": start, "endDate": end, "unit": "second"}}


def incident_summary_pipeline(run_id, connector_ids=None):
    match = {"run_id": run_id} if run_id else {}
    if connector_ids:
        match["connector_id"] = {"$in": list(connector_ids)}
    return [
        {"$match": match},
        {
            "$facet": {
                "by_state": [{"$group": {"_id": "$state", "count": {"$sum": 1}}}, {"$sort": {"_id": 1}}],
                "by_connector": [
                    {
                        "$group": {
                            "_id": "$connector_id",
                            "count": {"$sum": 1},
                            "open": {"$sum": {"$cond": [{"$eq": ["$unresolved", True]}, 1, 0]}},
                        }
                    },
                    {"$sort": {"_id": 1}},
                ],
                "durations": [
                    {
                        "$project": {
                            "opened_at": 1,
                            "ack_at": first_event_at("acknowledge"),
                            "verified_at": first_event_at("load_reduction_verified"),
                            "resolved_at": first_event_at("resolved"),
                        }
                    },
                    {
                        "$group": {
                            "_id": None,
                            "mtta_s": {"$avg": seconds_between("$opened_at", "$ack_at")},
                            # MTTM = time to verified load reduction, like the postmortem and the 5-minute SLO.
                            "mttm_s": {"$avg": seconds_between("$opened_at", "$verified_at")},
                            "mttr_s": {"$avg": seconds_between("$opened_at", "$resolved_at")},
                        }
                    },
                ],
            }
        },
    ]


class TrendPoint(BaseModel):
    end: datetime
    residual_mean: float | None
    residual_avg_5m: float | None
    residual_slope_c_per_min: float | None
    residual_max_15m: float | None


class TrendOut(BaseModel):
    run_id: str
    connector_id: str
    points: list[TrendPoint]


class FleetWindow(BaseModel):
    connector_id: str
    end: datetime
    quality: bool
    residual_mean: float | None


class FleetOut(BaseModel):
    run_id: str
    windows: list[FleetWindow]


class StateCount(BaseModel):
    state: str
    count: int


class ConnectorCount(BaseModel):
    connector_id: str
    count: int
    open: int


class IncidentSummary(BaseModel):
    total: int
    by_state: list[StateCount]
    by_connector: list[ConnectorCount]
    mtta_s: float | None
    mttm_s: float | None
    mttr_s: float | None


def shape_summary(doc):
    durations = (doc.get("durations") or [{}])[0]
    by_state = [StateCount(state=r["_id"], count=r["count"]) for r in doc.get("by_state", [])]
    return IncidentSummary(
        total=sum(s.count for s in by_state),
        by_state=by_state,
        by_connector=[ConnectorCount(connector_id=r["_id"], count=r["count"], open=r["open"]) for r in doc.get("by_connector", [])],
        mtta_s=durations.get("mtta_s"),
        mttm_s=durations.get("mttm_s"),
        mttr_s=durations.get("mttr_s"),
    )
