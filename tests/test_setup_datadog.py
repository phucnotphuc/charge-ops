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
