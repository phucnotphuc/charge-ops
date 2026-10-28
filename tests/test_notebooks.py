from datetime import UTC, datetime

from chargeops.notebooks import investigation_notebook, notebook_url

T = datetime(2026, 10, 28, 3, 0, tzinfo=UTC)
INCIDENT = {"_id": "inc-1", "run_id": "r1", "connector_id": "connector-4", "state": "mitigating", "opened_at": T,
            "evidence": {"residual_mean": 9.4, "end": T}, "timeline": [{"at": T, "event": "opened"}]}


def cells(payload):
    return [c["attributes"]["definition"] for c in payload["data"]["attributes"]["cells"]]


def test_notebook_is_scoped_to_run_and_connector():
    defs = cells(investigation_notebook(INCIDENT, "ap1.datadoghq.com", now=T))
    queries = [r.get("q", "") for d in defs if d["type"] == "timeseries" for r in d["requests"]]
    assert queries and all("run_id:r1" in q and "connector_id:connector-4" in q for q in queries)


def test_notebook_has_evidence_markdown_and_log_cell():
    defs = cells(investigation_notebook(INCIDENT, "ap1.datadoghq.com", now=T))
    text = "\n".join(d.get("text", "") for d in defs if d["type"] == "markdown")
    assert "9.4" in text and "connector-4" in text
    assert any(d["type"] == "log_stream" and "@incident_id:inc-1" in d["query"] for d in defs)
    assert any(d["type"] == "log_stream" and "@run_id:r1" in d["query"] for d in defs)


def test_postmortem_cell_added_when_given():
    defs = cells(investigation_notebook(INCIDENT, "ap1.datadoghq.com", postmortem_md="## Postmortem\nMTTR 14 phút", now=T))
    assert any("MTTR 14 phút" in d.get("text", "") for d in defs)


def test_cell_shape_matches_datadog_api():
    payload = investigation_notebook(INCIDENT, "ap1.datadoghq.com", now=T)
    attrs = payload["data"]["attributes"]
    assert payload["data"]["type"] == "notebooks" and attrs["status"] == "published" and "inc-1" in attrs["name"]
    assert attrs["metadata"]["type"] == "investigation"
    for cell in attrs["cells"]:
        assert cell["type"] == "notebook_cells"
        assert ("graph_size" in cell["attributes"]) == (cell["attributes"]["definition"]["type"] != "markdown")


def test_time_span_covers_incident():
    # Datadog documents the absolute notebook time as ISO 8601 date-time strings.
    attrs = investigation_notebook(INCIDENT, "ap1.datadoghq.com", now=T)["data"]["attributes"]
    start = datetime.fromisoformat(attrs["time"]["start"])
    end = datetime.fromisoformat(attrs["time"]["end"])
    assert (T - start).total_seconds() == 300 and (end - T).total_seconds() == 300


def test_resolved_incident_time_ends_at_resolution():
    resolved = {**INCIDENT, "state": "resolved", "resolved_at": datetime(2026, 10, 28, 3, 20, tzinfo=UTC)}
    end = datetime.fromisoformat(investigation_notebook(resolved, "x", now=T)["data"]["attributes"]["time"]["end"])
    assert end == datetime(2026, 10, 28, 3, 25, tzinfo=UTC)


def test_exact_queries_and_log_columns():
    defs = cells(investigation_notebook(INCIDENT, "ap1.datadoghq.com", now=T))
    scope = "{env:hackathon,run_id:r1,connector_id:connector-4}"
    queries = [r["q"] for d in defs if d["type"] == "timeseries" for r in d["requests"]]
    assert queries == [
        f"(sum:chargeops.residual_positive{scope}.as_count()-sum:chargeops.residual_negative{scope}.as_count())"
        f"/sum:chargeops.samples{scope}.as_count()",
        f"avg:chargeops.connector_temperature_c{scope}",
        f"avg:chargeops.expected_temperature_c{scope}",
        f"avg:chargeops.current_a{scope}",
        f"avg:chargeops.current_limit_a{scope}",
        f"max:chargeops.incident.state_age_s{scope}",
    ]
    assert all(d["columns"] == ["host", "service"] for d in defs if d["type"] == "log_stream")


def test_url():
    assert notebook_url("ap1.datadoghq.com", 12) == "https://app.ap1.datadoghq.com/notebook/12"
