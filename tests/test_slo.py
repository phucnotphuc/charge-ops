import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import httpx

from chargeops.observability import connector_tags, emit_charging_slo, emit_incident_outcome

spec = importlib.util.spec_from_file_location("setup_datadog", Path(__file__).parents[1] / "scripts" / "setup_datadog.py")
sd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sd)

TAGS = connector_tags("run-1", "connector-4")


def test_charging_minutes_count_requested_and_delivered():
    stats = Mock()
    emit_charging_slo(stats, "run-1", "connector-4", {"requested_current_a": 30, "current_a": 30})
    assert [c.args for c in stats.increment.call_args_list] == [
        ("chargeops.slo.charging_minutes.total", 0.25),
        ("chargeops.slo.charging_minutes.good", 0.25),
    ]
    assert all(c.kwargs == {"tags": TAGS} for c in stats.increment.call_args_list)


def test_charging_minutes_derated_to_zero_is_total_only():
    stats = Mock()
    emit_charging_slo(stats, "run-1", "connector-4", {"requested_current_a": 30, "current_a": 0})
    assert [c.args for c in stats.increment.call_args_list] == [("chargeops.slo.charging_minutes.total", 0.25)]


def test_charging_minutes_idle_or_missing_emits_nothing():
    stats = Mock()
    emit_charging_slo(stats, "run-1", "connector-4", {"requested_current_a": 0, "current_a": 0})
    emit_charging_slo(stats, "run-1", "connector-4", None)
    stats.increment.assert_not_called()


def test_incident_outcome_good_within_five_minutes():
    stats = Mock()
    emit_incident_outcome(stats, {"seconds_to_effective_reduction": 300})
    assert [c.args for c in stats.increment.call_args_list] == [
        ("chargeops.slo.mitigation.total", 1),
        ("chargeops.slo.mitigation.good", 1),
    ]


def test_incident_outcome_slow_is_total_only_and_no_reduction_is_ignored():
    stats = Mock()
    emit_incident_outcome(stats, {"seconds_to_effective_reduction": 301})
    assert [c.args for c in stats.increment.call_args_list] == [("chargeops.slo.mitigation.total", 1)]
    stats = Mock()
    emit_incident_outcome(stats, {"seconds_to_effective_reduction": None, "seconds_to_mitigated": None})
    stats.increment.assert_not_called()


def test_incident_outcome_derating_that_never_verified_counts_as_bad():
    stats = Mock()
    emit_incident_outcome(stats, {"seconds_to_effective_reduction": None, "seconds_to_mitigated": 40})
    assert [c.args for c in stats.increment.call_args_list] == [("chargeops.slo.mitigation.total", 1)]


def test_slo_specs_shape():
    specs = sd.slo_specs()
    assert set(specs) == {"slo_mitigation", "slo_charging"}
    for key, name in (("slo_mitigation", "mitigation"), ("slo_charging", "charging_minutes")):
        s = specs[key]
        assert s["type"] == "metric"
        assert s["query"]["numerator"] == f"sum:chargeops.slo.{name}.good{{env:hackathon}}.as_count()"
        assert s["query"]["denominator"] == f"sum:chargeops.slo.{name}.total{{env:hackathon}}.as_count()"
        assert s["thresholds"] == [{"timeframe": "7d", "target": 95.0}, {"timeframe": "30d", "target": 95.0}]


def test_engineering_dashboard_has_slo_widgets():
    board = sd.engineering_dashboard({"slo_mitigation": "a", "slo_charging": "b"})
    defs = [w["definition"] for w in board["widgets"]]
    slos = [d for d in defs if d["type"] == "slo"]
    assert [(d["slo_id"], d["view_type"], d["time_windows"]) for d in slos] == [
        ("a", "detail", ["7d", "30d"]),
        ("b", "detail", ["7d", "30d"]),
    ]
    assert not [d for d in sd.engineering_dashboard()["widgets"] if d["definition"]["type"] == "slo"]


def test_main_posts_slos_before_dashboards_then_puts(monkeypatch, tmp_path):
    calls = []
    counter = iter(range(1, 1000))

    def handler(request):
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.method == "GET":
            return httpx.Response(404, json={})
        if request.url.path.endswith("/slo") and request.method == "POST":
            return httpx.Response(200, json={"data": [{"id": f"slo{next(counter)}"}]})
        if "/slo/" in request.url.path:
            return httpx.Response(200, json={"data": [{"id": "x"}]})
        return httpx.Response(200, json={"id": next(counter)})

    real_client = httpx.Client
    monkeypatch.setattr(sd.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(sd, "load_dotenv", lambda *a, **k: None)
    for key in ("DD_API_KEY", "DD_APP_KEY", "WEBHOOK_SECRET"):
        monkeypatch.setenv(key, "test-value")
    monkeypatch.setenv("DD_SITE", "ap1.datadoghq.com")
    state = tmp_path / "state.json"
    monkeypatch.setattr("sys.argv", ["setup_datadog.py", "--webhook-url", "https://example.test/hook", "--state", str(state)])

    sd.main()
    posts = [(p, b) for m, p, b in calls if m == "POST" and p.endswith(("/slo", "/dashboard"))]
    assert [p.rsplit("/", 1)[1] for p, _ in posts] == ["slo", "slo", "dashboard", "dashboard"]
    saved = json.loads(state.read_text())
    assert saved["slo_mitigation"].startswith("slo") and saved["slo_charging"].startswith("slo")
    widgets = [w["definition"] for w in posts[2][1]["widgets"] if w["definition"]["type"] == "slo"]
    assert [w["slo_id"] for w in widgets] == [saved["slo_mitigation"], saved["slo_charging"]]

    calls.clear()
    sd.main()
    assert not [1 for m, p, _ in calls if m == "POST" and p.endswith(("/slo", "/dashboard"))]
    puts = [p for m, p, _ in calls if m == "PUT"]
    assert sum("/slo/" in p for p in puts) == 2 and sum("/dashboard/" in p for p in puts) == 2
