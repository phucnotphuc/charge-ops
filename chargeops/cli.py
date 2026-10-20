import argparse
import json
from uuid import uuid4

import httpx
from pymongo.errors import DuplicateKeyError

from chargeops.config import settings
from chargeops.domain import BASELINE_VERSION, now
from chargeops.storage import database, initialize


def main():
    parser = argparse.ArgumentParser(description="Local-only demo administration via MongoDB")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("--mode", choices=["local", "datadog"], default="datadog")
    start.add_argument("--scenario", choices=["healthy", "thermal_fault"], default="thermal_fault")
    sub.add_parser("stop")
    sub.add_parser("status")
    command = sub.add_parser("command", help="Operator command through the HTTP API")
    command.add_argument("incident_id")
    command.add_argument("type", choices=["acknowledge", "reduce_load", "maintenance", "verify_recovery"])
    command.add_argument("--key", default=None, help="Idempotency-Key; reuse it to retry safely")
    show = sub.add_parser("incident", help="Incident state, timeline and commands")
    show.add_argument("incident_id")
    args = parser.parse_args()
    if args.command in ("command", "incident"):
        config = settings()
        with httpx.Client(base_url=config.api_url, timeout=10) as client:
            if args.command == "command":
                response = client.post(
                    f"/api/v1/incidents/{args.incident_id}/commands",
                    json={"type": args.type},
                    headers={
                        "X-Operator-Secret": config.operator_secret.get_secret_value(),
                        "Idempotency-Key": args.key or str(uuid4()),
                    },
                )
            else:
                response = client.get(f"/api/v1/incidents/{args.incident_id}")
        if not response.is_success:
            parser.exit(1, f"HTTP {response.status_code}: {response.text}\n")
        body = response.json()
        body.pop("evidence", None)  # large sample list; see GET /api/v1/incidents/{id}
        print(json.dumps(body, indent=2))
        return
    db = database()
    initialize(db)
    if args.command == "start":
        run_id = str(uuid4())
        try:
            db.runs.insert_one(
                {
                    "_id": run_id,
                    "active_slot": "single",
                    "status": "pending",
                    "mode": args.mode,
                    "scenario": args.scenario,
                    "created_at": now(),
                    "baseline_version": BASELINE_VERSION,
                    "stop_requested": False,
                }
            )
        except DuplicateKeyError:
            parser.exit(1, "A run is already pending/active. Stop it first.\n")
        print(json.dumps({"run_id": run_id, "status": "pending"}))
    elif args.command == "stop":
        result = db.runs.update_one({"active_slot": "single"}, {"$set": {"stop_requested": True}})
        print(json.dumps({"requested": result.matched_count}))
    else:
        print(
            json.dumps(
                {
                    "runs": list(db.runs.find().sort("created_at", -1).limit(5)),
                    "incidents": list(db.incidents.find()),
                    "inbox": list(db.inbox.find({}, {"message": 0}).sort("received_at", -1).limit(10)),
                },
                default=str,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
