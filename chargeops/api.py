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
from chargeops.domain import MANIFEST, Batch, CommandRequest, CommandResult, normalize_webhook, now
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


def operator_auth(x_operator_secret: str = Header(default="")):
    # Temporary G2 guard; G3 replaces it with operator sessions (CONTRACT §2).
    if not hmac.compare_digest(x_operator_secret, settings().operator_secret.get_secret_value()):
        raise HTTPException(401, "Invalid operator credentials")


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
    if run:
        # Lets a restarted simulator resume its thermal state instead of jumping to equilibrium.
        run["last_sample"] = store.telemetry.find_one(
            {"meta.run_id": run["_id"]},
            {"_id": 0, "measured_at": 1, "connector_temperature_c": 1},
            sort=[("measured_at", -1)],
        )
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


@app.get("/api/v1/incidents/{incident_id}")
def incident(incident_id: str, store=Depends(db)):
    found = store.incidents.find_one({"_id": incident_id})
    if not found:
        raise HTTPException(404, "Incident not found")
    found["commands"] = list(store.commands.find({"incident_id": incident_id}).sort("created_at", 1))
    return jsonable_encoder(found)


@app.post("/api/v1/incidents/{incident_id}/commands", status_code=202, dependencies=[Depends(operator_auth)])
def create_command(
    incident_id: str,
    body: CommandRequest,
    idempotency_key: str = Header(min_length=1, max_length=200),
    store=Depends(db),
):
    found = store.incidents.find_one({"_id": incident_id}, {"run_id": 1})
    if not found:
        raise HTTPException(404, "Incident not found")
    command = {
        "_id": str(uuid4()),
        "idempotency_key": idempotency_key,
        "incident_id": incident_id,
        "run_id": found["run_id"],
        "type": body.type,
        "status": "pending",
        "validated": False,
        "created_at": now(),
    }
    try:
        store.commands.insert_one(command)
    except DuplicateKeyError:
        command = store.commands.find_one({"idempotency_key": idempotency_key})
        if (command["incident_id"], command["type"]) != (incident_id, body.type):
            raise HTTPException(409, "Idempotency-Key reused with a different request") from None
    event(
        logger("api"), "command_requested", incident_id=incident_id, command_id=command["_id"], type=body.type
    )
    # Accepted is not "load reduced": the worker validates, the simulator applies, telemetry verifies.
    return {"command_id": command["_id"], "status": command["status"]}


@app.get("/api/v1/commands/{command_id}")
def command(command_id: str, store=Depends(db)):
    found = store.commands.find_one({"_id": command_id})
    if not found:
        raise HTTPException(404, "Command not found")
    return jsonable_encoder(found)


@app.get("/api/v1/simulator/commands", dependencies=[Depends(simulator_auth)])
def simulator_commands(store=Depends(db)):
    run = store.runs.find_one({"status": "active"}, {"_id": 1})
    if not run:
        return []
    query = {"run_id": run["_id"], "status": {"$in": ["pending", "claimed"]}, "dispatched_at": {"$ne": None}}
    items = list(store.commands.find(query, {"type": 1, "sim": 1}))
    store.commands.update_many(
        {"_id": {"$in": [c["_id"] for c in items]}, "status": "pending"},
        {"$set": {"status": "claimed", "claimed_at": now()}},
    )
    return items


@app.post(
    "/api/v1/simulator/commands/{command_id}/result", status_code=202, dependencies=[Depends(simulator_auth)]
)
def command_result(command_id: str, body: CommandResult, store=Depends(db)):
    found = store.commands.find_one({"_id": command_id, "dispatched_at": {"$ne": None}})
    if not found:
        raise HTTPException(404, "Command not found")
    if found["status"] in ("applied", "failed"):
        return {"status": "duplicate"}
    at = now()
    if body.status == "applied":
        # The simulator already changed its device state, even when the result arrives late.
        changes = {f"sim.{key}": value for key, value in found["sim"].items()}
        store.runs.update_one({"_id": found["run_id"]}, {"$set": changes})
    done = store.commands.update_one(
        {"_id": command_id, "status": {"$in": ["pending", "claimed"]}},
        {"$set": {"status": body.status, f"{body.status}_at": at, "result": body.result}},
    )
    if not done.matched_count:  # timed out meanwhile: keep as a late event for the worker
        store.commands.update_one(
            {"_id": command_id, "late_result": None},
            {"$set": {"late_result": {"status": body.status, "at": at, "result": body.result}}},
        )
        return {"status": "late"}
    return {"status": body.status}


@app.get("/api/v1/demo-runs")
def runs(store=Depends(db)):
    return jsonable_encoder(
        {"items": list(store.runs.find().sort("created_at", -1).limit(100)), "next_cursor": None}
    )
