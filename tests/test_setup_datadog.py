import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("setup_datadog", Path(__file__).parents[1] / "scripts" / "setup_datadog.py")
sd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sd)

URL = "https://app.ap1.datadoghq.com/dashboard/abc-def-ghi"


def titles(board):
    return [w["definition"]["title"] for w in board["widgets"]]


def test_engineering_dashboard_gains_incident_panels_and_keeps_existing_ones():
    board = sd.engineering_dashboard()
    names = titles(board)
    assert "Residual nhiệt (°C, trung bình theo mẫu)" in names  # existing widget unchanged
    assert "Sự cố đang mở theo connector" in names
    assert "Thời gian ở trạng thái hiện tại (s)" in names


def test_residual_and_current_charts_overlay_incident_events():
    board = {w["definition"]["title"]: w["definition"] for w in sd.engineering_dashboard()["widgets"]}
    assert board["Residual nhiệt (°C, trung bình theo mẫu)"]["events"] == [{"q": "tags:source:chargeops"}]
    assert board["Dòng sạc và giới hạn dòng (A) — giảm tải hiện ở đây"]["events"]


def test_incident_queries_use_template_scope():
    for widget in sd.incident_widgets():
        for request in widget["requests"]:
            for query in request["queries"]:
                assert sd.SCOPE in query["query"]


def test_platform_dashboard_has_heartbeat():
    assert "Worker heartbeat (tick / phút)" in titles(sd.platform_dashboard())


def test_extra_monitors_never_call_the_webhook():
    specs = sd.extra_monitor_specs(URL)
    assert set(specs) == {"stale", "worker", "stuck"}
    for spec in specs.values():
        assert "@webhook" not in spec["message"]
    assert URL in specs["stale"]["message"] and URL in specs["stuck"]["message"]
    assert specs["worker"]["options"]["notify_no_data"] is True
    assert "state:mitigating" in specs["stuck"]["query"]


def test_composite_message_links_dashboard_and_logs_and_keeps_webhook():
    message = sd.composite_message(URL, "ap1.datadoghq.com")
    assert "@webhook-chargeops-minimal" in message
    assert URL + "?tpl_var_run_id={{run_id.name}}&tpl_var_connector_id={{connector_id.name}}" in message
    assert "https://app.ap1.datadoghq.com/logs?query=" in message


def test_dashboard_url():
    assert sd.dashboard_url("datadoghq.com", "x") == "https://app.datadoghq.com/dashboard/x"


def test_stale_and_worker_monitors_alert_on_no_data():
    specs = sd.extra_monitor_specs(URL)
    for key in ("stale", "worker"):
        assert specs[key]["options"]["notify_no_data"] is True
        assert specs[key]["options"]["no_data_timeframe"] == 4


def test_main_orders_resources_and_second_run_only_puts(monkeypatch, tmp_path):
    import json

    import httpx

    calls = []
    counter = iter(range(1, 1000))

    def handler(request):
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.method == "GET":
            return httpx.Response(404, json={})
        if request.url.path.endswith("/slo") or "/slo/" in request.url.path:
            return httpx.Response(200, json={"data": [{"id": f"slo{next(counter)}"}]})
        return httpx.Response(200, json={"id": next(counter)})

    real_client = httpx.Client
    monkeypatch.setattr(sd.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(sd, "load_dotenv", lambda *a, **k: None)
    for key in ("DD_API_KEY", "DD_APP_KEY", "WEBHOOK_SECRET"):
        monkeypatch.setenv(key, "test-value")
    monkeypatch.setenv("DD_SITE", "ap1.datadoghq.com")
    state = tmp_path / "state.json"
    monkeypatch.setattr(
        "sys.argv", ["setup_datadog.py", "--webhook-url", "https://example.test/hook", "--state", str(state)]
    )

    sd.main()
    created = [(p, b) for m, p, b in calls if m == "POST" and p.endswith(("/monitor", "/dashboard"))]
    last_dashboard = max(i for i, (p, _) in enumerate(created) if p.endswith("/dashboard"))
    composite = next(i for i, (_, b) in enumerate(created) if b.get("type") == "composite")
    assert last_dashboard < composite
    saved = json.loads(state.read_text())
    assert sd.dashboard_url("ap1.datadoghq.com", saved["dashboard"]) in created[composite][1]["message"]

    calls.clear()
    sd.main()
    assert not [1 for m, p, _ in calls if m == "POST" and p.endswith(("/monitor", "/dashboard"))]
    puts = [p for m, p, _ in calls if m == "PUT"]
    assert sum("/monitor/" in p for p in puts) == 6  # thermal, quality, stale, worker, stuck, composite
    assert sum("/dashboard/" in p for p in puts) == 2
