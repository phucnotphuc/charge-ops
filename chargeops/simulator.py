import time
from uuid import UUID, uuid5

import httpx

from chargeops.config import settings
from chargeops.domain import now, synthetic_values
from chargeops.logging import event, logger


def main():
    config = settings()
    log = logger("simulator")
    headers = {"X-Simulator-Secret": config.simulator_secret.get_secret_value()}
    pending = None
    with httpx.Client(base_url=config.api_url, headers=headers, timeout=10) as client:
        while True:
            try:
                response = client.get("/api/v1/simulator/run")
                response.raise_for_status()
                run = response.json()
                if run:
                    from datetime import datetime

                    measured = now()
                    elapsed = (measured - datetime.fromisoformat(run["started_at"])).total_seconds()
                    if pending is None or pending["run_id"] != run["_id"]:
                        pending = {
                            "sample_id": str(uuid5(UUID(run["_id"]), str(int(elapsed // 5)))),
                            "run_id": run["_id"],
                            "connector_id": "connector-1",
                            "measured_at": measured.isoformat(),
                            **synthetic_values(elapsed, run["scenario"]),
                        }
                    result = client.post("/api/v1/telemetry/batch", json={"samples": [pending]})
                    if result.status_code in (409, 422):
                        pending = None
                    else:
                        result.raise_for_status()
                        pending = None
                else:
                    pending = None
            except httpx.HTTPError as exc:
                event(log, "ingest_retry", error_type=type(exc).__name__)
            # Wall-clock boundaries: Docker Desktop's VM timers run ~7% fast, its wall clock is corrected.
            time.sleep(5 - time.time() % 5)


if __name__ == "__main__":
    main()
