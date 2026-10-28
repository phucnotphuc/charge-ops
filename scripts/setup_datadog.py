"""Provision only this demo's resources; persist IDs to update them on subsequent runs."""

import argparse
import json
import os
from pathlib import Path
from urllib.parse import quote, urlparse

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


def timeseries(title, queries, formulas=None, display="line", markers=(), events=False):
    widget = {
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
    if events:
        widget["events"] = [{"q": "tags:source:chargeops"}]
    return widget


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


def incident_widgets():
    return [
        timeseries(
            "Sự cố đang mở theo connector",
            [metric("open", f"sum:chargeops.incident.open{SCOPE} by {{connector_id}}")],
            display="bars",
            events=True,
        ),
        timeseries(
            "Thời gian ở trạng thái hiện tại (s)",
            [metric("age", f"max:chargeops.incident.state_age_s{SCOPE} by {{connector_id,state}}")],
            markers=[(600, "warning dashed", "giảm tải quá 10 phút")],
        ),
    ]


def heartbeat_widget():
    return timeseries(
        "Worker heartbeat (tick / phút)",
        [metric("tick", "sum:chargeops.worker.tick{env:hackathon}.as_rate()")],
        ["tick * 60"],
    )


def dashboard_url(site, dashboard_id):
    return f"https://app.{site}/dashboard/{dashboard_id}"


def investigate(url):
    return f"Điều tra: {url}?tpl_var_run_id={{{{run_id.name}}}}&tpl_var_connector_id={{{{connector_id.name}}}}"


def composite_message(engineering_url, site):
    logs = f"https://app.{site}/logs?query=" + quote("service:chargeops @run_id:{{run_id.name}}")
    return "\n".join(
        ["ChargeOps thermal anomaly. @webhook-chargeops-minimal", investigate(engineering_url), f"Logs: {logs}"]
    )


def extra_monitor_specs(engineering_url):
    return {
        "stale": {
            "name": "ChargeOps telemetry stale",
            "type": "query alert",
            "query": "sum(last_2m):sum:chargeops.samples{env:hackathon} by {run_id,connector_id}.as_count() < 12",
            "message": "Connector {{connector_id.name}} gửi dưới 12 mẫu trong 2 phút. " + investigate(engineering_url),
            "options": {
                "thresholds": {"critical": 12},
                "notify_no_data": True,
                "no_data_timeframe": 4,
                "require_full_window": True,
            },
        },
        "worker": {
            "name": "ChargeOps worker heartbeat",
            "type": "query alert",
            "query": "sum(last_2m):sum:chargeops.worker.tick{env:hackathon}.as_count() < 1",
            "message": "Worker không gửi heartbeat trong 2 phút: kiểm tra `docker compose logs worker`.",
            "options": {
                "thresholds": {"critical": 1},
                "notify_no_data": True,
                "no_data_timeframe": 4,
                "require_full_window": False,
            },
        },
        "stuck": {
            "name": "ChargeOps derating not resolving",
            "type": "query alert",
            "query": "max(last_5m):max:chargeops.incident.state_age_s{env:hackathon,state:mitigating} by {run_id,connector_id} > 600",
            "message": "Connector {{connector_id.name}} ở trạng thái giảm tải quá 10 phút. " + investigate(engineering_url),
            "options": {"thresholds": {"critical": 600}, "notify_no_data": False, "require_full_window": False},
        },
    }


SLO_WINDOWS = ["7d", "30d"]


def slo_specs():
    def spec(name, description, series):
        return {
            "name": name,
            "type": "metric",
            "description": description,
            "query": {
                "numerator": f"sum:chargeops.slo.{series}.good{{env:hackathon}}.as_count()",
                "denominator": f"sum:chargeops.slo.{series}.total{{env:hackathon}}.as_count()",
            },
            "thresholds": [{"timeframe": window, "target": 95.0} for window in SLO_WINDOWS],
            "tags": ["env:hackathon", "service:chargeops"],
        }

    return {
        "slo_mitigation": spec(
            "ChargeOps effective derating within 5 min",
            "Share of incidents whose derating was verified within 300 s of opening.",
            "mitigation",
        ),
        "slo_charging": spec(
            "ChargeOps uninterrupted charging minutes",
            "Share of requested charging minutes that actually delivered current.",
            "charging_minutes",
        ),
    }


def slo_widgets(ids):
    return [
        {"type": "slo", "slo_id": ids[key], "view_type": "detail", "time_windows": SLO_WINDOWS}
        for key in ("slo_mitigation", "slo_charging")
        if ids.get(key)
    ]


def engineering_dashboard(slo_ids=None):
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
                events=True,
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
                events=True,
            ),
            timeseries(
                "Nhiệt độ môi trường (°C)",
                [metric("ambient", f"avg:chargeops.ambient_temperature_c{SCOPE} by {{station_id}}")],
            ),
            *incident_widgets(),
            *slo_widgets(slo_ids or {}),
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
            heartbeat_widget(),
        ],
    )


APP_FILE = Path(__file__).with_name("datadog_app.json")
CONNECTION_NAME = "chargeops-actions"
MANUAL_APP_STEPS = """App Builder API unavailable for this account/key; import the app by hand instead:
  1. Datadog > Actions > Connections > New: HTTP, base URL = {tunnel}, authentication = Token Auth,
     secret token 'actionSecret' = the value of DATADOG_ACTION_SECRET in .env,
     header 'X-Action-Secret' = {{{{ actionSecret }}}}. Name it '{name}'. (Never paste the secret anywhere else.)
  2. Datadog > App Builder > New App > Import JSON, paste scripts/datadog_app.json after replacing
     __TUNNEL__ with {tunnel} and __CONNECTION_ID__ with the id of the connection from step 1.
  3. Save and publish the app."""


def app_definition(tunnel, connection_id):
    text = APP_FILE.read_text(encoding="utf-8").replace("__TUNNEL__", tunnel).replace("__CONNECTION_ID__", connection_id)
    return json.loads(text)


def provision_app(client, state, tunnel, action_secret, save, site):
    """Create/update the HTTP connection and the App via the v2 API. Any 4xx from either endpoint prints
    the status and the manual Import JSON steps and returns False (the caller continues). A 404 for a stored
    id means it was deleted in Datadog: the id is cleared and the resource recreated once.
    `client` is rooted at https://api.<site>; the action secret only ever goes into the connection body and
    response bodies are never printed."""

    def fall_back(step, response):
        print(f"{step}: HTTP {response.status_code}.")
        print(MANUAL_APP_STEPS.format(tunnel=tunnel, name=CONNECTION_NAME))
        return False

    def upsert(key, create_path, update_path, make_body):
        for attempt in range(2):
            stored = state.get(key)
            if stored:
                response = client.patch(update_path.format(stored), json=make_body(stored))
            else:
                response = client.post(create_path, json=make_body(None))
            if response.status_code == 404 and stored and attempt == 0:
                del state[key]
                save()
                continue
            return response
        return response

    connection = {
        "data": {
            "type": "action_connection",
            "attributes": {
                "name": CONNECTION_NAME,
                "integration": {
                    "type": "HTTP",
                    "base_url": tunnel,
                    "credentials": {
                        "type": "HTTPTokenAuth",
                        "tokens": [{"name": "actionSecret", "type": "SECRET", "value": action_secret}],
                        "headers": [{"name": "X-Action-Secret", "value": "{{ actionSecret }}"}],
                    },
                },
            },
        }
    }
    response = upsert(
        "app_connection", "/api/v2/actions/connections", "/api/v2/actions/connections/{}", lambda _id: connection
    )
    if 400 <= response.status_code < 500:
        return fall_back("Action connection", response)
    if response.is_error:
        raise SystemExit(f"Action connection: HTTP {response.status_code}")
    state["app_connection"] = state.get("app_connection") or response.json()["data"]["id"]
    save()

    def app_body(app_id):
        data = {"type": "appDefinitions", "attributes": app_definition(tunnel, state["app_connection"])}
        if app_id:
            data["id"] = app_id
        return {"data": data}

    response = upsert("app", "/api/v2/app-builder/apps", "/api/v2/app-builder/apps/{}", app_body)
    if 400 <= response.status_code < 500:
        return fall_back("App Builder", response)
    if response.is_error:
        raise SystemExit(f"App Builder: HTTP {response.status_code}")
    state["app"] = state.get("app") or response.json()["data"]["id"]
    save()
    published = client.post(f"/api/v2/app-builder/apps/{state['app']}/deployment")
    if published.is_error:
        print(f"App saved but not published (HTTP {published.status_code}); publish it in App Builder.")
    print(f"App: https://app.{site}/app-builder/apps/{state['app']} (also under Datadog > Apps)")
    return True


DASHBOARDS = {
    "dashboard": lambda state: engineering_dashboard(state),
    "dashboard_platform": lambda state: platform_dashboard(),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--webhook-url", required=True)
    parser.add_argument("--state", default="artifacts/datadog-resources.json")
    parser.add_argument("--with-app", action="store_true", help="also create/update the Datadog App (B8)")
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
    if args.with_app and not os.getenv("DATADOG_ACTION_SECRET"):
        parser.error("Missing DATADOG_ACTION_SECRET; run scripts/init_env.py or add it to .env")
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
        # SLOs first: the engineering dashboard's SLO widgets need their ids.
        for key, spec in slo_specs().items():
            result = call(
                "PUT" if key in state else "POST",
                f"/slo/{state[key]}" if key in state else "/slo",
                spec,
            )
            state[key] = result["data"][0]["id"] if key not in state else state[key]
            save()
        for key, build in DASHBOARDS.items():
            result = call(
                "PUT" if key in state else "POST",
                f"/dashboard/{state[key]}" if key in state else "/dashboard",
                build(state),
            )
            state[key] = result["id"]
            save()
        engineering_url = dashboard_url(site, state["dashboard"])
        for key, spec in extra_monitor_specs(engineering_url).items():
            call("POST", "/monitor/validate", spec)
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
            "message": composite_message(engineering_url, site),
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
    print("Extra monitors: stale, worker heartbeat, derating not resolving (no webhook).")
    print(f"Set DD_MONITOR_ID={state['composite']} in .env, then recreate worker.")
    for key in DASHBOARDS:
        print(f"Dashboard: https://app.{site}/dashboard/{state[key]}")
    if args.with_app:
        tunnel = f"{parsed.scheme}://{parsed.netloc}"
        with httpx.Client(
            base_url=f"https://api.{site}",
            headers={"DD-API-KEY": os.environ["DD_API_KEY"], "DD-APPLICATION-KEY": os.environ["DD_APP_KEY"]},
            timeout=30,
        ) as app_client:
            provision_app(app_client, state, tunnel, os.environ["DATADOG_ACTION_SECRET"], save, site)


if __name__ == "__main__":
    main()
