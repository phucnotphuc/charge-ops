"""Postmortem: one $merge pipeline turns a closed incident into an incident_reports document (MongoDB 8 only;
mongomock cannot run $merge, $dateDiff or $lookup with a pipeline).

Assumptions:
- Run only for closed incidents (resolved, or run stopped while unresolved); `closed_by` is "resolved" when a
  resolved event exists and "run_stopped" otherwise, so it is meaningless for an open incident.
- Event times use `$first` per event name. After a recurrence (a later cycle repeats the same events) the report
  therefore carries the cycle-1 timestamps.
- Windows: only `quality: true` windows count toward peak_residual_c, and the span starts 60 s before opened_at so
  the evidence window that triggered the incident (it ends just before the incident opens) is included.
- Energy is simulated: telemetry between load_reduction_verified and mitigated (or close), deduplicated by
  sample_id, power_kw * 5 s / 3600.
"""

SAMPLE_PERIOD_S = 5
LEAD_MS = 60_000


def _event_at(name):
    return {
        "$first": {
            "$map": {
                "input": {"$filter": {"input": "$timeline", "as": "t", "cond": {"$eq": ["$$t.event", name]}}},
                "as": "t",
                "in": "$$t.at",
            }
        }
    }


def _seconds(field):
    return {
        "$cond": [
            {"$eq": [{"$type": f"${field}"}, "date"]},
            {"$dateDiff": {"startDate": "$opened_at", "endDate": f"${field}", "unit": "second"}},
            None,
        ]
    }


def postmortem_pipeline(incident_id):
    return [
        {"$match": {"_id": incident_id}},
        {
            "$set": {
                "ack_at": _event_at("acknowledge"),
                "verified_at": _event_at("load_reduction_verified"),
                "mitigated_at": _event_at("mitigated"),
                "resolved_at": _event_at("resolved"),
                "stopped_at": _event_at("run_stopped"),
            }
        },
        {"$set": {"closed_at": {"$ifNull": ["$resolved_at", "$stopped_at"]}}},
        {
            "$set": {
                "derate_end": {"$ifNull": ["$mitigated_at", "$closed_at"]},
                "closed_by": {"$cond": [{"$eq": [{"$type": "$resolved_at"}, "date"]}, "resolved", "run_stopped"]},
                "seconds_to_ack": _seconds("ack_at"),
                "seconds_to_effective_reduction": _seconds("verified_at"),
                "seconds_to_mitigated": _seconds("mitigated_at"),
                "seconds_to_resolved": _seconds("resolved_at"),
                "retest_resets": {
                    "$size": {
                        "$filter": {"input": "$timeline", "as": "t", "cond": {"$eq": ["$$t.event", "retest_reset"]}}
                    }
                },
            }
        },
        {
            "$lookup": {
                "from": "windows",
                "let": {"run": "$run_id", "connector": "$connector_id", "opened": "$opened_at", "closed": "$closed_at"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$run_id", "$$run"]},
                                    {"$eq": ["$connector_id", "$$connector"]},
                                    {"$gte": ["$end", {"$subtract": ["$$opened", LEAD_MS]}]},
                                    {"$lte": ["$end", {"$ifNull": ["$$closed", "$$opened"]}]},
                                ]
                            }
                        }
                    },
                    {"$match": {"quality": True}},
                    {"$project": {"residual_mean": 1, "end": 1}},
                ],
                "as": "windows",
            }
        },
        {
            "$lookup": {
                "from": "telemetry",
                "let": {
                    "run": "$run_id",
                    "connector": "$connector_id",
                    "from": "$verified_at",
                    "to": "$derate_end",
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$ne": ["$$from", None]},
                                    {"$eq": ["$meta.run_id", "$$run"]},
                                    {"$eq": ["$meta.connector_id", "$$connector"]},
                                    {"$gte": ["$measured_at", "$$from"]},
                                    {"$lte": ["$measured_at", "$$to"]},
                                ]
                            }
                        }
                    },
                    # Retried samples share a sample_id but not a document id: count each sample once.
                    {"$group": {"_id": "$sample_id", "power_kw": {"$first": "$power_kw"}}},
                ],
                "as": "derated",
            }
        },
        {
            "$project": {
                "run_id": 1,
                "connector_id": 1,
                "opened_at": 1,
                "closed_at": 1,
                "closed_by": 1,
                "seconds_to_ack": 1,
                "seconds_to_effective_reduction": 1,
                "seconds_to_mitigated": 1,
                "seconds_to_resolved": 1,
                "peak_residual_c": {"$max": "$windows.residual_mean"},
                "retest_resets": 1,
                "reference_current_a": {"$ifNull": ["$reference_current_a", None]},
                "target_current_a": {"$ifNull": ["$target_current_a", None]},
                "energy_kwh_while_derated": {
                    "$cond": [
                        {"$eq": [{"$type": "$verified_at"}, "date"]},
                        {"$divide": [{"$multiply": [{"$sum": "$derated.power_kw"}, SAMPLE_PERIOD_S]}, 3600]},
                        None,
                    ]
                },
                "simulated": {"$literal": True},
            }
        },
        {"$merge": {"into": "incident_reports", "whenMatched": "replace", "whenNotMatched": "insert"}},
    ]


def _duration(seconds):
    if seconds is None:
        return "—"
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes} phút {rest} giây"


def _num(value, unit="", digits=2):
    return "—" if value is None else f"{value:.{digits}f}{unit}"


def report_markdown(report):
    closed_by = {"resolved": "đã xử lý xong", "run_stopped": "run kết thúc khi chưa xử lý"}.get(
        report.get("closed_by"), "—"
    )
    return (
        "## Postmortem\n\n"
        f"- Đóng bởi: {closed_by}\n"
        f"- Thời gian tới xác nhận: {_duration(report.get('seconds_to_ack'))}\n"
        f"- Thời gian tới giảm tải hiệu quả: {_duration(report.get('seconds_to_effective_reduction'))}\n"
        f"- Thời gian tới giảm thiểu: {_duration(report.get('seconds_to_mitigated'))}\n"
        f"- Thời gian tới giải quyết: {_duration(report.get('seconds_to_resolved'))}\n"
        f"- Residual cao nhất: {_num(report.get('peak_residual_c'), ' °C')}\n"
        f"- Số lần retest bị đặt lại: {report.get('retest_resets', 0)}\n"
        f"- Dòng tham chiếu / dòng đích: {_num(report.get('reference_current_a'), ' A', 1)}"
        f" / {_num(report.get('target_current_a'), ' A', 1)}\n"
        f"- Năng lượng khi giảm tải: {_num(report.get('energy_kwh_while_derated'), ' kWh', 3)}\n\n"
        "_Dữ liệu mô phỏng: năng lượng và nhiệt độ không phải số đo thật._\n"
    )


def report_summary(report):
    return (
        f"Postmortem ({report.get('closed_by')}): giải quyết sau {_duration(report.get('seconds_to_resolved'))}, "
        f"residual cao nhất {_num(report.get('peak_residual_c'), ' °C')}. Dữ liệu mô phỏng."
    )
