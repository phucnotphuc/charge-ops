import argparse
import json
from uuid import uuid4

from pymongo.errors import DuplicateKeyError

from chargeops.domain import now
from chargeops.storage import database, initialize


def main():
    parser = argparse.ArgumentParser(description="Local-only demo administration via MongoDB")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("--mode", choices=["local", "datadog"], default="datadog")
    start.add_argument("--scenario", choices=["healthy", "thermal_fault"], default="thermal_fault")
    sub.add_parser("stop")
    sub.add_parser("status")
    args = parser.parse_args()
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
                    "baseline_version": "equilibrium-v1",
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
