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


def dashboard():
    widgets = []
    for title, query in [
        ("Telemetry quality (1 = valid)", "min:chargeops.quality{env:hackathon} by {run_id,connector_id}"),
        ("Samples received", "sum:chargeops.samples{env:hackathon} by {run_id,connector_id}.as_count()"),
    ]:
        widgets.append(
            {
                "definition": {
                    "type": "timeseries",
                    "title": title,
                    "requests": [{"q": query, "display_type": "line"}],
                }
            }
        )
    widgets.append(
        {
            "definition": {
                "type": "timeseries",
                "title": "Thermal residual (sample weighted)",
                "requests": [
                    {
                        "queries": [
                            {
                                "data_source": "metrics",
                                "name": name,
                                "query": f"sum:chargeops.{metric}{{env:hackathon}} by {{run_id,connector_id}}.as_count()",
                            }
                            for name, metric in [
                                ("a", "residual_positive"),
                                ("b", "residual_negative"),
                                ("c", "samples"),
                            ]
                        ],
                        "formulas": [{"formula": "(a-b)/c"}],
                        "response_format": "timeseries",
                        "display_type": "line",
                    }
                ],
            }
        }
    )
    return {"title": "ChargeOps — minimal operational loop", "layout_type": "ordered", "widgets": widgets}


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
        result = call(
            "PUT" if "dashboard" in state else "POST",
            f"/dashboard/{state['dashboard']}" if "dashboard" in state else "/dashboard",
            dashboard(),
        )
        state["dashboard"] = result["id"]
        save()
    print(f"Set DD_MONITOR_ID={state['composite']} in .env, then recreate worker.")
    print(f"Dashboard: https://app.{site}/dashboard/{state['dashboard']}")


if __name__ == "__main__":
    main()
