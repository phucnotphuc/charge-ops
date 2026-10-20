import hmac
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError, PyMongoError

from chargeops.config import settings
from chargeops.domain import MANIFEST, Batch, normalize_webhook, now
from chargeops.logging import event, logger
from chargeops.storage import database, initialize


@asynccontextmanager
async def lifespan(app):
    app.state.db = database()
    initialize(app.state.db)
    yield
    app.state.db.client.close()


app = FastAPI(title="ChargeOps minimal loop", lifespan=lifespan)


def db(request: Request):
    return request.app.state.db


def simulator_auth(x_simulator_secret: str = Header(default="")):
    if not hmac.compare_digest(x_simulator_secret, settings().simulator_secret.get_secret_value()):
        raise HTTPException(401, "Invalid simulator credentials")


@app.exception_handler(PyMongoError)
async def mongo_error(request, exc):
    return JSONResponse(status_code=503, content={"error": "Database unavailable"})


@app.get("/api/v1/health")
def health():
    return {"status": "ok"}


@app.get("/api/v1/ready")
def ready(store=Depends(db)):
    store.command("ping")
    return {"status": "ready"}


@app.get("/api/v1/simulator/run", dependencies=[Depends(simulator_auth)])
def active_run(store=Depends(db)):
    run = store.runs.find_one({"status": "active"})
    return jsonable_encoder(run)


@app.post("/api/v1/telemetry/batch", status_code=202, dependencies=[Depends(simulator_auth)])
def ingest(batch: Batch, store=Depends(db)):
    received = now()
    run = store.runs.find_one({"status": "active"})
    if not run:
        raise HTTPException(409, "No active run")
    documents = []
    for sample in batch.samples:
        if str(sample.run_id) != run["_id"] or not (
            run["started_at"] <= sample.measured_at <= received + timedelta(seconds=5)
        ):
            raise HTTPException(422, "Sample outside active run or future timestamp")
        document = sample.model_dump(mode="json")
        document.update(
            measured_at=sample.measured_at,
            received_at=received,
            meta={
                "run_id": run["_id"],
                "connector_id": sample.connector_id,
                "station_id": "station-1",
                "charger_id": "charger-1",
            },
            provenance={"composition": "synthetic", "manifest_id": MANIFEST},
        )
        documents.append(document)
    store.telemetry.insert_many(documents)
    event(logger("api"), "telemetry_received", run_id=run["_id"], count=len(documents))
    return {"accepted": len(documents)}


@app.post("/api/v1/webhooks/datadog", status_code=202)
async def webhook(request: Request, x_webhook_secret: str = Header(default=""), store=Depends(db)):
    if not hmac.compare_digest(x_webhook_secret, settings().webhook_secret.get_secret_value()):
        raise HTTPException(401, "Invalid webhook credentials")
    body = await request.body()
    if len(body) > 16384:
        raise HTTPException(413, "Payload too large")
    try:
        raw = await request.json()
        payload = normalize_webhook(raw)
    except (ValidationError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
        # No raw content: avoid persisting attacker-controlled secrets/messages.
        store.quarantine.insert_one(
            {"_id": str(uuid4()), "received_at": now(), "reason": "invalid_identity_timestamp_or_status"}
        )
        return {"status": "quarantined"}
    document = payload.model_dump(mode="json")
    document.update(_id=payload.event_id, occurred_at=payload.occurred_at, received_at=now(), processed=False)
    try:
        store.inbox.insert_one(document)
    except DuplicateKeyError:
        return {"status": "duplicate"}
    event(logger("api"), "webhook_received", run_id=document["run_id"], event_id=document["_id"])
    return {"status": "accepted"}


@app.get("/api/v1/incidents")
def incidents(store=Depends(db)):
    return jsonable_encoder(
        {"items": list(store.incidents.find().sort("opened_at", -1).limit(100)), "next_cursor": None}
    )


@app.get("/api/v1/demo-runs")
def runs(store=Depends(db)):
    return jsonable_encoder(
        {"items": list(store.runs.find().sort("created_at", -1).limit(100)), "next_cursor": None}
    )
