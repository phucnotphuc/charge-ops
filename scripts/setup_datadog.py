"""Provision only this demo's resources; persist IDs to update them on subsequent runs."""

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv


def monitor_specs():
    group = "{env:hackathon} by {run_id,connector_id}.as_count()"
    positive = "sum:chargeops.residual_positive" + group
    negative = "sum:chargeops.residual_negative" + group
    count = "sum:chargeops.samples" + group
    return {
        "thermal": {
            "name": "ChargeOps thermal residual",
            "type": "query alert",
            "query": f"sum(last_1m):({positive} - {negative}) / {count} > 8",
            "message": "Thermal signal; notifications are sent by the composite monitor.",
            "options": {
                "thresholds": {"critical": 8, "critical_recovery": 3},
                "notify_no_data": False,
                "require_full_window": True,
                "new_group_delay": 0,
            },
        },
        "quality": {
            "name": "ChargeOps valid telemetry window",
            "type": "query alert",
            "query": "min(last_1m):min:chargeops.quality{env:hackathon} by {run_id,connector_id} > 0.5",
            "message": "Technical gate: ALERT means data quality is valid, not a device fault.",
            "options": {
                "thresholds": {"critical": 0.5},
                "notify_no_data": False,
                "require_full_window": True,
                "new_group_delay": 0,
            },
        },
    }


SCOPE = "{env:hackathon,$run_id,$station_id,$connector_id}"
TEMPLATE_VARIABLES = [
    {"name": tag, "prefix": tag, "default": "*"} for tag in ("run_id", "station_id", "connector_id")
]
COMMAND_LOGS = "service:chargeops @event:(webhook_received OR webhook_processed OR command_requested OR command_applied OR command_rejected)"


def metric(name, query):
    return {"data_source": "metrics", "name": name, "query": query}


def log_count(name, query, group_by=None):
    spec = {
        "data_source": "logs",
        "name": name,
        "search": {"query": query},
        "indexes": ["*"],
        "compute": {"aggregation": "count"},
    }
    if group_by:
        spec["group_by"] = [
            {"facet": group_by, "limit": 10, "sort": {"aggregation": "count", "order": "desc"}}
        ]
    return spec


def residual_queries(by="{connector_id}"):
    # Same sample-weighted mean as the thermal monitor: Σ residual / Σ samples.
    return [
        metric(name, f"sum:chargeops.{series}{SCOPE} by {by}.as_count()")
        for name, series in [("a", "residual_positive"), ("b", "residual_negative"), ("c", "samples")]
    ]


def timeseries(title, queries, formulas=None, display="line", markers=()):
    return {
        "type": "timeseries",
        "title": title,
        "show_legend": True,
        "requests": [
            {
                "queries": queries,
                "formulas": [{"formula": f} for f in formulas or [q["name"] for q in queries]],
                "response_format": "timeseries",
                "display_type": display,
            }
        ],
        "markers": [
            {"value": f"y = {y}", "display_type": kind, "label": label} for y, kind, label in markers
        ],
    }


def monitors(title):
    return {
        "type": "manage_status",
        "title": title,
        "query": "ChargeOps",
        "summary_type": "monitors",
        "display_format": "countsAndList",
    }


def log_stream(title, query):
    return {
        "type": "list_stream",
        "title": title,
        "requests": [
            {
                "columns": [
                    {"field": "status_line", "width": "auto"},
                    {"field": "timestamp", "width": "auto"},
                    {"field": "content", "width": "full"},
                ],
                "query": {"data_source": "logs_stream", "query_string": query, "indexes": []},
                "response_format": "event_list",
            }
        ],
    }


def dashboard(title, description, widgets):
    return {
        "title": title,
        "description": description,
        "layout_type": "ordered",
        "template_variables": TEMPLATE_VARIABLES,
        "widgets": [{"definition": widget} for widget in widgets],
    }


def engineering_dashboard():
    thresholds = [(8, "error dashed", "cảnh báo 8 °C"), (3, "ok dashed", "phục hồi 3 °C")]
    return dashboard(
        "ChargeOps — Kỹ sư điện / vận hành trạm",
        "Connector nào đang nóng bất thường so với tải và nhiệt môi trường, giảm tải có tác dụng không.",
        [
            monitors("Trạng thái cảnh báo"),
            {
                "type": "toplist",
                "title": "Residual trung bình theo connector (trong khung thời gian)",
                "requests": [
                    {
                        "queries": residual_queries(),
                        "formulas": [
                            {
                                "formula": "(a-b)/c",
                                "conditional_formats": [
                                    {"comparator": ">", "value": 8, "palette": "white_on_red"},
                                    {"comparator": ">", "value": 3, "palette": "white_on_yellow"},
                                    {"comparator": "<=", "value": 3, "palette": "white_on_green"},
                                ],
                            }
                        ],
                        "response_format": "scalar",
                    }
                ],
            },
            timeseries(
                "Residual nhiệt (°C, trung bình theo mẫu)",
                residual_queries(),
                ["(a-b)/c"],
                markers=thresholds,
            ),
            timeseries(
                "Nhiệt đo được và nhiệt kỳ vọng theo tải (°C)",
                [
                    metric("measured", f"avg:chargeops.connector_temperature_c{SCOPE} by {{connector_id}}"),
                    metric("expected", f"avg:chargeops.expected_temperature_c{SCOPE} by {{connector_id}}"),
                ],
            ),
            timeseries(
                "Dòng sạc và giới hạn dòng (A) — giảm tải hiện ở đây",
                [
                    metric("current", f"avg:chargeops.current_a{SCOPE} by {{connector_id}}"),
                    metric("limit", f"avg:chargeops.current_limit_a{SCOPE} by {{connector_id}}"),
                ],
            ),
            timeseries(
                "Nhiệt độ môi trường (°C)",
                [metric("ambient", f"avg:chargeops.ambient_temperature_c{SCOPE} by {{station_id}}")],
            ),
            log_stream("Webhook và lệnh vận hành", COMMAND_LOGS),
        ],
    )


def platform_dashboard():
    api = "{env:hackathon,service:chargeops-api}"
    worker = "{env:hackathon,service:chargeops-worker}"
    simulator = "{env:hackathon,service:chargeops-simulator}"
    return dashboard(
        "ChargeOps — IT / SRE nền tảng",
        "Hệ thống có khỏe không: API, worker, luồng telemetry, chất lượng dữ liệu và lỗi.",
        [
            monitors("Trạng thái monitor"),
            timeseries(
                "API — số request theo endpoint",
                [metric("a", f"sum:trace.fastapi.request.hits{api} by {{resource_name}}.as_count()")],
                display="bars",
            ),
            timeseries(
                "API — độ trễ p50 / p95 (s)",
                [
                    metric("p50", f"p50:trace.fastapi.request{api}"),
                    metric("p95", f"p95:trace.fastapi.request{api}"),
                ],
            ),
            timeseries(
                "API — response theo nhóm mã HTTP",
                [
                    metric(
                        "a",
                        f"sum:trace.fastapi.request.hits.by_http_status{api} by {{http.status_class}}.as_count()",
                    )
                ],
                display="bars",
            ),
            timeseries(
                "Simulator → API — request theo mã HTTP",
                [
                    metric(
                        "a",
                        f"sum:trace.http.request.hits.by_http_status{simulator} by {{http.status_class}}.as_count()",
                    )
                ],
                display="bars",
            ),
            timeseries(
                "Worker — số vòng xử lý và độ trễ p95 (s)",
                [
                    metric("ticks", f"sum:trace.worker.tick.hits{worker}.as_count()"),
                    metric("p95", f"p95:trace.worker.tick{worker}"),
                ],
            ),
            timeseries(
                "Mẫu telemetry nhận theo connector (mỗi block 15 s)",
                [metric("a", f"sum:chargeops.samples{SCOPE} by {{connector_id}}.as_count()")],
                display="bars",
            ),
            timeseries(
                "Chất lượng cửa sổ telemetry (1 = hợp lệ, 0 = thiếu/cũ)",
                [metric("a", f"min:chargeops.quality{SCOPE} by {{connector_id}}")],
            ),
            timeseries("Log theo sự kiện", [log_count("a", "service:chargeops", "@event")], display="bars"),
            log_stream(
                "Lỗi, retry và lệnh bị từ chối",
                "service:chargeops (status:error OR @event:ingest_retry OR @event:worker_retry OR @event:command_rejected)",
            ),
        ],
    )


DASHBOARDS = {"dashboard": engineering_dashboard, "dashboard_platform": platform_dashboard}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--webhook-url", required=True)
    parser.add_argument("--state", default="artifacts/datadog-resources.json")
    args = parser.parse_args()
    load_dotenv()
    site = os.getenv("DD_SITE", "datadoghq.com")
    if site not in {
        "datadoghq.com",
        "datadoghq.eu",
        "us3.datadoghq.com",
        "us5.datadoghq.com",
        "ap1.datadoghq.com",
        "ap2.datadoghq.com",
    }:
        parser.error("Unsupported DD_SITE")
    parsed = urlparse(args.webhook_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        parser.error("Use the HTTPS webhook URL without credentials")
    for key in ["DD_API_KEY", "DD_APP_KEY", "WEBHOOK_SECRET"]:
        if not os.getenv(key):
            parser.error(f"Missing {key}; configure locally in .env")
    state_path = Path(args.state)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if state.get("site", site) != site:
        parser.error("State belongs to a different Datadog site")
    state["site"] = site

    def save():
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    with httpx.Client(
        base_url=f"https://api.{site}/api/v1",
        headers={"DD-API-KEY": os.environ["DD_API_KEY"], "DD-APPLICATION-KEY": os.environ["DD_APP_KEY"]},
        timeout=30,
    ) as client:

        def call(method, path, payload=None):
            response = client.request(method, path, json=payload)
            if response.is_error:
                # Error bodies may echo webhook custom headers. Never print them.
                detail = "check permissions/config" if "webhooks" in path else response.text[:500]
                raise SystemExit(f"Datadog {method} {path}: HTTP {response.status_code}; {detail}")
            return response.json()

        specs = monitor_specs()
        # Validate metric expressions before creating any resources.
        for spec in specs.values():
            call("POST", "/monitor/validate", spec)
        name = "chargeops-minimal"
        hook = {
            "name": name,
            "url": args.webhook_url,
            "encode_as": "json",
            "custom_headers": json.dumps({"X-Webhook-Secret": os.environ["WEBHOOK_SECRET"]}),
            "payload": json.dumps(
                {
                    "event_id": "$ID",
                    "monitor_id": "$ALERT_ID",
                    "scope": "$ALERT_SCOPE",
                    "date_ms": "$DATE",
                    "transition": "$ALERT_TRANSITION",
                }
            ),
        }
        found = client.get(f"/integration/webhooks/configuration/webhooks/{name}")
        if found.status_code not in (200, 404):
            raise SystemExit(f"Webhook lookup: HTTP {found.status_code}")
        call(
            "PUT" if found.status_code == 200 else "POST",
            f"/integration/webhooks/configuration/webhooks/{name}"
            if found.status_code == 200
            else "/integration/webhooks/configuration/webhooks",
            hook,
        )
        for key, spec in specs.items():
            result = call(
                "PUT" if key in state else "POST",
                f"/monitor/{state[key]}" if key in state else "/monitor",
                spec,
            )
            state[key] = result["id"]
            save()
        composite = {
            "name": "ChargeOps thermal incident",
            "type": "composite",
            "query": f"{state['thermal']} && {state['quality']}",
            "message": "ChargeOps thermal anomaly. @webhook-chargeops-minimal",
            "options": {"notify_no_data": False},
        }
        call("POST", "/monitor/validate", composite)
        result = call(
            "PUT" if "composite" in state else "POST",
            f"/monitor/{state['composite']}" if "composite" in state else "/monitor",
            composite,
        )
        state["composite"] = result["id"]
        save()
        for key, build in DASHBOARDS.items():
            result = call(
                "PUT" if key in state else "POST",
                f"/dashboard/{state[key]}" if key in state else "/dashboard",
                build(),
            )
            state[key] = result["id"]
            save()
    print(f"Set DD_MONITOR_ID={state['composite']} in .env, then recreate worker.")
    for key in DASHBOARDS:
        print(f"Dashboard: https://app.{site}/dashboard/{state[key]}")


if __name__ == "__main__":
    main()
