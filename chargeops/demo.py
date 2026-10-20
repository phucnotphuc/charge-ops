"""Scripted operator for the active run: drives an incident open → resolved through the HTTP API only.

Usage (inside the api container, after `cli start`):  python -m chargeops.demo --delay 10
"""

import argparse
import time
from datetime import datetime
from uuid import uuid4

import httpx

from chargeops.config import settings

STEPS = [
    ("open", "acknowledge"),
    ("acknowledged", "reduce_load"),
    ("mitigated", "maintenance"),
    ("mitigated+maintenance", "verify_recovery"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--delay", type=float, default=10, help="operator reaction time per step, seconds")
    parser.add_argument("--timeout", type=float, default=1800)
    args = parser.parse_args()
    config = settings()
    headers = {"X-Operator-Secret": config.operator_secret.get_secret_value()}
    deadline = time.monotonic() + args.timeout
    done = set()
    with httpx.Client(base_url=config.api_url, timeout=10) as client:
        run = next(r for r in client.get("/api/v1/demo-runs").json()["items"] if r["status"] == "active")
        started = datetime.fromisoformat(run["started_at"])
        print(f"run {run['_id']} mode={run['mode']} scenario={run['scenario']}")
        seen = 0
        while time.monotonic() < deadline:
            items = [i for i in client.get("/api/v1/incidents").json()["items"] if i["run_id"] == run["_id"]]
            if items:
                incident = client.get(f"/api/v1/incidents/{items[0]['_id']}").json()
                for entry in incident["timeline"][seen:]:
                    elapsed = (datetime.fromisoformat(entry["at"]) - started).total_seconds()
                    extra = {k: v for k, v in entry.items() if k not in ("at", "event")}
                    print(f"t+{elapsed:6.0f}s  {entry['event']:28} {extra or ''}")
                seen = len(incident["timeline"])
                if incident["state"] == "resolved":
                    print("resolved")
                    return
                state = incident["state"] + ("+maintenance" if incident.get("maintenance_applied_at") else "")
                for when, kind in STEPS:
                    if state == when and (state, kind) not in done:
                        time.sleep(args.delay)
                        response = client.post(
                            f"/api/v1/incidents/{incident['_id']}/commands",
                            json={"type": kind},
                            headers={**headers, "Idempotency-Key": str(uuid4())},
                        )
                        response.raise_for_status()
                        done.add((state, kind))
                        print(f"           -> {kind} requested")
            time.sleep(5)
    raise SystemExit("timed out before resolution")


if __name__ == "__main__":
    main()
