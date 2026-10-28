"""Pure builder for the per-incident investigation Notebook payload (Datadog Notebooks API v1).

Shapes verified against the Create a notebook reference (2026-10-28): attributes need name, cells, time;
cells are {"type": "notebook_cells", "attributes": {"definition", "graph_size"}}; markdown cells have no
graph_size; the absolute notebook time is {"start", "end"} as ISO 8601 date-time strings; metadata.type may
be "investigation" or "postmortem"; status is "published"."""

from datetime import UTC, datetime, timedelta

PAD = timedelta(minutes=5)


def notebook_url(site, notebook_id):
    return f"https://app.{site}/notebook/{notebook_id}"


def _ts(title, queries):
    return {
        "attributes": {
            "definition": {
                "type": "timeseries",
                "title": title,
                "show_legend": True,
                "requests": [{"q": q, "display_type": "line"} for q in queries],
            },
            "graph_size": "m",
        },
        "type": "notebook_cells",
    }


def _markdown(text):
    return {"attributes": {"definition": {"type": "markdown", "text": text}}, "type": "notebook_cells"}


def _logs(title, query):
    return {
        "attributes": {
            "definition": {"type": "log_stream", "title": title, "query": query, "indexes": [], "columns": ["host", "service"]},
            "graph_size": "l",
        },
        "type": "notebook_cells",
    }


def _iso(moment):
    return moment.astimezone(UTC).isoformat()


def investigation_notebook(incident, site, postmortem_md=None, now=None):
    now = now or datetime.now(UTC)
    run, connector, incident_id = incident["run_id"], incident["connector_id"], incident["_id"]
    scope = f"{{env:hackathon,run_id:{run},connector_id:{connector}}}"
    evidence = incident.get("evidence") or {}
    residual = evidence.get("residual_mean")
    header = (
        f"# Sự cố {connector}\n\n"
        f"- Incident: `{incident_id}`\n- Run: `{run}`\n- Connector: `{connector}`\n"
        f"- Mở lúc: {incident['opened_at'].isoformat()}\n"
        f"- Residual trung bình (bằng chứng): {residual if residual is not None else 'n/a'} °C\n"
        f"- Trạng thái hiện tại: {incident['state']}\n"
    )
    cells = [
        _markdown(header),
        _ts(
            "Residual nhiệt độ",
            [
                f"(sum:chargeops.residual_positive{scope}.as_count()-sum:chargeops.residual_negative{scope}.as_count())"
                f"/sum:chargeops.samples{scope}.as_count()"
            ],
        ),
        _ts(
            "Nhiệt độ đo được và kỳ vọng",
            [f"avg:chargeops.connector_temperature_c{scope}", f"avg:chargeops.expected_temperature_c{scope}"],
        ),
        _ts("Dòng sạc và giới hạn dòng", [f"avg:chargeops.current_a{scope}", f"avg:chargeops.current_limit_a{scope}"]),
        _ts("Thời gian ở trạng thái hiện tại", [f"max:chargeops.incident.state_age_s{scope}"]),
        _logs("Log của sự cố", f"service:chargeops @incident_id:{incident_id}"),
        _logs("Log của run", f"service:chargeops @run_id:{run}"),
    ]
    if postmortem_md:
        cells.append(_markdown(postmortem_md))
    end = (incident.get("resolved_at") or now) + PAD
    return {
        "data": {
            "type": "notebooks",
            "attributes": {
                "name": f"Điều tra {connector} [{incident_id}]",
                "status": "published",
                "metadata": {"type": "postmortem" if postmortem_md else "investigation"},
                "time": {"start": _iso(incident["opened_at"] - PAD), "end": _iso(end)},
                "cells": cells,
            },
        }
    }
