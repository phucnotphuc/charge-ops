import base64
import hmac
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, ValidationError
from pymongo.errors import DuplicateKeyError, PyMongoError
from starlette.exceptions import HTTPException as StarletteHTTPException

from chargeops import insights, results, station
from chargeops.auth import SESSION_COOKIE, SESSION_TTL_S, new_token, token_digest, verify_password
from chargeops.config import settings
from chargeops.domain import (
    BASELINE_VERSION,
    CONNECTORS,
    LOCATION,
    MANIFEST,
    Batch,
    CommandRequest,
    CommandResult,
    ConnectorId,
    ConnectorList,
    ConnectorOut,
    DemoRunCreate,
    DemoRunOut,
    FaultEventOut,
    FaultRequest,
    LoginRequest,
    TelemetryPage,
    UserOut,
    normalize_webhook,
    now,
    with_residuals,
)
from chargeops.fleet import fleet_plan
from chargeops.logging import event, logger
from chargeops.model import PROFILES
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


def utc_z(moment):
    """CONTRACT: UTC ISO 8601 with `Z`; Mongo returns aware UTC (naive is treated as UTC)."""
    aware = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    return aware.isoformat().replace("+00:00", "Z")


def encode(document):
    """JSON-ready raw Mongo documents (endpoints without a response model), datetimes as `...Z`."""
    return jsonable_encoder(document, custom_encoder={datetime: utc_z})


def allowed_origins():
    return [o.strip() for o in settings().allowed_origins.split(",") if o.strip()]


def login_origin(request: Request):
    """Browsers always send Origin on POST: a foreign one is refused. No Origin (curl, cli.py) is allowed."""
    origin = request.headers.get("origin")
    if origin is not None and origin not in allowed_origins():
        raise ApiError(403, "forbidden", "Origin not allowed")


def simulator_auth(x_simulator_secret: str = Header(default="")):
    if not hmac.compare_digest(x_simulator_secret, settings().simulator_secret.get_secret_value()):
        raise HTTPException(401, "Invalid simulator credentials")


class ApiError(HTTPException):
    def __init__(self, status_code, code, message):
        super().__init__(status_code, message)
        self.code = code


ERROR_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
}


def envelope(request, status_code, code, message):
    request_id = request.headers.get("x-request-id", "")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", request_id):
        request_id = str(uuid4())
    body = {"error": {"code": code, "message": message, "request_id": request_id}}
    return JSONResponse(status_code=status_code, content=body, headers={"X-Request-ID": request_id})


@app.exception_handler(StarletteHTTPException)
async def http_error(request, exc):
    code = getattr(exc, "code", None) or ERROR_CODES.get(exc.status_code, "error")
    return envelope(request, exc.status_code, code, str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    first = exc.errors()[0]
    where = ".".join(str(part) for part in first["loc"] if part != "body")
    return envelope(request, 422, "validation_error", f"{where}: {first['msg']}")


def session_for(request, store):
    token = request.cookies.get(SESSION_COOKIE)
    session = store.sessions.find_one({"_id": token_digest(token)}) if token else None
    return session if session and session["expires_at"] > now() else None


def operator_auth(request: Request, x_operator_secret: str = Header(default=""), store=Depends(db)):
    """Operator session cookie (with Origin check) or the X-Operator-Secret header used by cli.py/demo.py."""
    if x_operator_secret:
        if not hmac.compare_digest(x_operator_secret, settings().operator_secret.get_secret_value()):
            raise ApiError(401, "unauthorized", "Invalid operator credentials")
        return "operator"
    session = session_for(request, store)
    if not session:
        raise ApiError(401, "unauthorized", "Operator login required")
    if request.headers.get("origin") not in allowed_origins():
        raise ApiError(403, "forbidden", "Origin not allowed")
    return session["username"]


@app.exception_handler(PyMongoError)
async def mongo_error(request, exc):
    return envelope(request, 503, "database_unavailable", "Database unavailable")


@app.get("/api/v1/health")
def health():
    return {"status": "ok"}


@app.get("/api/v1/ready")
def ready(store=Depends(db)):
    store.command("ping")
    return {"status": "ready"}


@app.get("/api/v1/simulator/run", dependencies=[Depends(simulator_auth)])
def active_run(resume: bool = False, store=Depends(db)):
    run = store.runs.find_one({"status": "active"})
    if run and resume:
        # Lets a restarted simulator resume each connector's thermal state instead of jumping to equilibrium.
        pipeline = [
            {"$match": {"meta.run_id": run["_id"]}},
            {"$sort": {"measured_at": -1}},
            {"$group": {"_id": "$meta.connector_id", "latest": {"$first": "$$ROOT"}}},
        ]
        run["last_samples"] = {
            row["_id"]: {
                "measured_at": row["latest"]["measured_at"],
                "connector_temperature_c": row["latest"]["connector_temperature_c"],
            }
            for row in store.telemetry.aggregate(pipeline)
        }
        # ... and each connector's delivered energy, so the vehicle SoC continues instead of restarting.
        run["last_vehicles"] = {
            doc["connector_id"]: doc for doc in store.vehicle_states.find({"run_id": run["_id"]}, {"_id": 0})
        }
    return encode(run)


@app.get("/api/v1/simulator/events", dependencies=[Depends(simulator_auth)])
def simulator_events(store=Depends(db)):
    """Unconsumed fault-injection events of the active run, oldest first."""
    run = store.runs.find_one({"status": "active"}, {"_id": 1})
    if not run:
        return []
    query = {"run_id": run["_id"], "type": "inject_fault", "consumed_at": None}
    return encode(list(store.run_events.find(query).sort("requested_at", 1)))


@app.post(
    "/api/v1/simulator/events/{event_id}/consume", status_code=202, dependencies=[Depends(simulator_auth)]
)
def consume_event(event_id: str, store=Depends(db)):
    """Exactly once: consumed_at is set only while null and the fault starts on that connector at that time.

    A retry (lost response, simulator restart) re-applies the same timestamp, so it changes nothing."""
    found = store.run_events.find_one({"_id": event_id, "type": "inject_fault"})
    if not found:
        raise ApiError(404, "not_found", "Event not found")
    first = store.run_events.update_one(
        {"_id": event_id, "consumed_at": None}, {"$set": {"consumed_at": now()}}
    )
    consumed = store.run_events.find_one({"_id": event_id})["consumed_at"]
    key = f"sim.{found['connector_id']}"
    store.runs.update_one(
        {
            "_id": found["run_id"],
            "$or": [
                {f"{key}.fault_injected_at": None},
                {f"{key}.fault_injected_at": {"$lt": consumed}},
            ],
        },
        # A new injection reopens the slot; an old retry never reopens a cleared fault.
        {"$set": {f"{key}.fault_injected_at": consumed, f"{key}.{INJECTION_OPEN}": True}},
    )
    if not first.modified_count:
        return {"status": "duplicate"}
    event(
        logger("api"), "fault_injection_consumed", run_id=found["run_id"], connector_id=found["connector_id"]
    )
    return {"status": "consumed"}


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
        station_id, charger_id = LOCATION[sample.connector_id]
        document.update(
            measured_at=sample.measured_at,
            received_at=received,
            meta={
                "run_id": run["_id"],
                "connector_id": sample.connector_id,
                "station_id": station_id,
                "charger_id": charger_id,
            },
            provenance={"composition": "hybrid", "manifest_id": MANIFEST},
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
    return encode(
        {"items": list(store.incidents.find().sort("opened_at", -1).limit(100)), "next_cursor": None}
    )


@app.get("/api/v1/incidents/{incident_id}")
def incident(incident_id: str, store=Depends(db)):
    found = store.incidents.find_one({"_id": incident_id})
    if not found:
        raise HTTPException(404, "Incident not found")
    found["commands"] = list(store.commands.find({"incident_id": incident_id}).sort("created_at", 1))
    return encode(found)


@app.post("/api/v1/incidents/{incident_id}/commands", status_code=202, dependencies=[Depends(operator_auth)])
def create_command(
    incident_id: str,
    body: CommandRequest,
    idempotency_key: str = Header(min_length=1, max_length=200),
    store=Depends(db),
):
    found = store.incidents.find_one({"_id": incident_id}, {"run_id": 1, "connector_id": 1})
    if not found:
        raise HTTPException(404, "Incident not found")
    command = {
        "_id": str(uuid4()),
        "idempotency_key": idempotency_key,
        "incident_id": incident_id,
        "run_id": found["run_id"],
        "connector_id": found["connector_id"],
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
    return encode(found)


@app.get("/api/v1/simulator/commands", dependencies=[Depends(simulator_auth)])
def simulator_commands(store=Depends(db)):
    run = store.runs.find_one({"status": "active"}, {"_id": 1})
    if not run:
        return []
    query = {"run_id": run["_id"], "status": {"$in": ["pending", "claimed"]}, "dispatched_at": {"$ne": None}}
    items = list(store.commands.find(query, {"type": 1, "sim": 1, "connector_id": 1}))
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
        # The simulator already changed its device state, even when the result arrives late; only the
        # command's own connector changes.
        changes = {f"sim.{found['connector_id']}.{key}": value for key, value in found["sim"].items()}
        if found["sim"].get("fault_cleared"):  # when, so an injected fault is told apart from the seeded one
            changes[f"sim.{found['connector_id']}.fault_cleared_at"] = at
            changes[f"sim.{found['connector_id']}.{INJECTION_OPEN}"] = False
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
    return encode({"items": list(store.runs.find().sort("created_at", -1).limit(100)), "next_cursor": None})


def run_out(run):
    return DemoRunOut.model_validate({**run, "id": run["_id"]})


def get_run(store, run_id):
    run = store.runs.find_one({"_id": run_id})
    if not run:
        raise ApiError(404, "not_found", "Demo run not found")
    return run


@app.post(
    "/api/v1/demo-runs", status_code=201, response_model=DemoRunOut, dependencies=[Depends(operator_auth)]
)
def create_run(body: DemoRunCreate, store=Depends(db)):
    profile = body.profile  # None: every connector's session comes from the seed
    if profile is not None and profile not in PROFILES:
        raise ApiError(422, "validation_error", f"Unknown profile {profile}")
    # Checked here, not on DemoRunCreate: domain.py is in the evaluation fingerprint.
    if not -(2**63) <= body.seed < 2**63:
        raise ApiError(422, "validation_error", "seed must fit in a signed 64-bit integer")
    try:
        fleet_plan(body.seed, body.scenario, profile)  # the simulator replays this plan every step
    except ValueError as exc:
        raise ApiError(422, "validation_error", str(exc)) from None
    # Created runs hold no active_slot; /start claims it, like `cli start` does when it inserts "pending".
    run = {
        "_id": str(uuid4()),
        "status": "created",
        "mode": body.mode,
        "scenario": body.scenario,
        "seed": body.seed,
        "profile_id": profile,
        "created_at": now(),
        "baseline_version": BASELINE_VERSION,
        "stop_requested": False,
    }
    store.runs.insert_one(run)
    event(logger("api"), "demo_run_created", run_id=run["_id"], mode=body.mode, scenario=body.scenario)
    return run_out(run)


@app.get("/api/v1/demo-runs/{run_id}", response_model=DemoRunOut)
def demo_run(run_id: str, store=Depends(db)):
    return run_out(get_run(store, run_id))


@app.post(
    "/api/v1/demo-runs/{run_id}/start",
    status_code=202,
    response_model=DemoRunOut,
    dependencies=[Depends(operator_auth)],
)
def start_run(run_id: str, store=Depends(db)):
    run = get_run(store, run_id)
    if run["status"] != "created":
        raise ApiError(409, "conflict", f"Run is {run['status']}, only a created run can start")
    if run["mode"] == "datadog":  # the worker enforces the same rules when it activates the run
        if not settings().dd_monitor_id:
            raise ApiError(409, "configure_dd_monitor_id_first", "Set DD_MONITOR_ID before a Datadog run")
        budget = store.budgets.find_one({"_id": settings().dd_trial_id}) or {"run_ids": []}
        if len(budget["run_ids"]) >= 20:
            raise ApiError(409, "trial_run_budget_exhausted", "Datadog trial budget of 20 runs is used up")
    try:
        # Worker activates the pending run and honours stop_requested; the API never writes "active".
        claimed = store.runs.update_one(
            {"_id": run_id, "status": "created"},
            {"$set": {"status": "pending", "active_slot": "single", "start_requested_at": now()}},
        )
    except DuplicateKeyError:
        raise ApiError(409, "conflict", "A run is already pending/active. Stop it first.") from None
    if not claimed.matched_count:
        raise ApiError(409, "conflict", "Run changed state concurrently")
    event(logger("api"), "demo_run_start_requested", run_id=run_id)
    return run_out(get_run(store, run_id))


@app.post(
    "/api/v1/demo-runs/{run_id}/stop",
    status_code=202,
    response_model=DemoRunOut,
    dependencies=[Depends(operator_auth)],
)
def stop_run(run_id: str, store=Depends(db)):
    get_run(store, run_id)
    # Conditional updates only: a run the worker activates meanwhile falls through to stop_requested.
    # Cancelling before activation keeps a Datadog run from being charged against the trial budget.
    cancel = {"$set": {"status": "cancelled", "stopped_at": now()}, "$unset": {"active_slot": ""}}
    for status, change in (
        ("created", cancel),
        ("pending", cancel),
        ("active", {"$set": {"stop_requested": True}}),
    ):
        if store.runs.update_one({"_id": run_id, "status": status}, change).matched_count:
            break
    else:
        raise ApiError(409, "conflict", "Run is already finished")
    event(logger("api"), "demo_run_stop_requested", run_id=run_id)
    return run_out(get_run(store, run_id))


INJECTION_OPEN = "fault_injection_open"


@app.post(
    "/api/v1/demo-runs/{run_id}/inject-fault",
    status_code=202,
    response_model=FaultEventOut,
    dependencies=[Depends(operator_auth)],
)
def inject_fault(run_id: str, body: FaultRequest, store=Depends(db)):
    run = get_run(store, run_id)
    if run["status"] != "active":
        raise ApiError(409, "conflict", "Faults can only be injected into an active run")
    # Claimed atomically on the run, so a double click or a client retry can neither restart the fault ramp
    # nor revive it after maintenance. Maintenance closes it; consuming the event re-opens it.
    flag = f"sim.{body.connector_id}.{INJECTION_OPEN}"
    claimed = store.runs.update_one(
        {"_id": run_id, "status": "active", flag: {"$ne": True}}, {"$set": {flag: True}}
    )
    if not claimed.matched_count:
        raise ApiError(409, "conflict", "A fault injection is already pending or active on this connector")
    record = {
        "_id": str(uuid4()),
        "run_id": run_id,
        "type": "inject_fault",
        "connector_id": body.connector_id,
        "requested_at": now(),
        "consumed_at": None,
    }
    store.run_events.insert_one(record)
    event(logger("api"), "fault_injection_requested", run_id=run_id, connector_id=body.connector_id)
    return FaultEventOut(
        event_id=record["_id"],
        run_id=run_id,
        type=record["type"],
        connector_id=record["connector_id"],
        requested_at=record["requested_at"],
    )


@app.post("/api/v1/auth/login", response_model=UserOut, dependencies=[Depends(login_origin)])
def login(body: LoginRequest, response: Response, store=Depends(db)):
    config = settings()
    encoded = config.operator_password_hash.get_secret_value()
    user_ok = hmac.compare_digest(body.username.encode(), config.operator_username.encode())
    if not (encoded and verify_password(body.password, encoded) and user_ok):
        raise ApiError(401, "unauthorized", "Invalid username or password")
    token, at = new_token(), now()
    store.sessions.insert_one(
        {
            "_id": token_digest(token),
            "username": config.operator_username,
            "created_at": at,
            "expires_at": at + timedelta(seconds=SESSION_TTL_S),
        }
    )
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_TTL_S, httponly=True, samesite="strict", path="/"
    )
    return UserOut(username=config.operator_username, role="operator")


@app.post("/api/v1/auth/logout", status_code=204, dependencies=[Depends(login_origin)])
def logout(request: Request, store=Depends(db)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        store.sessions.delete_one({"_id": token_digest(token)})
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict")
    return response


@app.get("/api/v1/auth/me", response_model=UserOut)
def me(request: Request, store=Depends(db)):
    session = session_for(request, store)
    if not session:
        raise ApiError(401, "unauthorized", "Not logged in")
    return UserOut(username=session["username"], role="operator")


FRESH_S = 30


def sample_dict(document):
    """Telemetry document without Mongo internals; station/charger come from the time-series meta."""
    meta = document.get("meta", {})
    out = {k: v for k, v in document.items() if k not in ("_id", "meta")}
    return {**out, "station_id": meta.get("station_id"), "charger_id": meta.get("charger_id")}


def reporting_run(store):
    return store.runs.find_one({"status": "active"}) or store.runs.find_one(
        {"status": "stopped"}, sort=[("started_at", -1)]
    )


def connector_view(store, run, connector_id, latest):
    stamp = latest["measured_at"] if latest else None
    freshness = (now() - stamp).total_seconds() if stamp else None
    incident = store.incidents.find_one(
        {"run_id": run["_id"], "connector_id": connector_id, "unresolved": True}, sort=[("opened_at", -1)]
    )
    station_id, charger_id = LOCATION[connector_id]
    return ConnectorOut(
        connector_id=connector_id,
        station_id=station_id,
        charger_id=charger_id,
        run_id=run["_id"],
        run_status=run["status"],
        mode=run["mode"],
        latest=sample_dict(latest) if latest else None,
        measured_at=stamp,
        freshness_s=freshness,
        stale=freshness is None or freshness > FRESH_S,
        active_incident={"id": incident["_id"], "state": incident["state"]} if incident else None,
    )


def connector_views(store, ids=CONNECTORS):
    run = reporting_run(store)
    if not run:
        return []
    # Newest by measured_at, not arrival order: a late older sample must not replace the latest state.
    pipeline = [
        {"$match": {"meta.run_id": run["_id"], "meta.connector_id": {"$in": list(ids)}}},
        {"$sort": {"measured_at": -1, "received_at": -1}},
        {"$group": {"_id": "$meta.connector_id", "latest": {"$first": "$$ROOT"}}},
    ]
    latest = {row["_id"]: row["latest"] for row in store.telemetry.aggregate(pipeline)}
    return [connector_view(store, run, cid, latest.get(cid)) for cid in ids]


@app.get("/api/v1/connectors", response_model=ConnectorList)
def connectors(store=Depends(db)):
    return ConnectorList(items=connector_views(store))


@app.post("/api/v1/simulator/vehicles", status_code=202, dependencies=[Depends(simulator_auth)])
def ingest_vehicles(batch: station.VehicleBatch, store=Depends(db)):
    for v in batch.vehicles:
        store.vehicle_states.update_one(
            {"_id": f"{v.run_id}:{v.connector_id}"},
            {"$set": {**v.model_dump(), "received_at": now()}},
            upsert=True,
        )
    return {"accepted": len(batch.vehicles)}


@app.get("/api/v1/stations/{station_id}", response_model=station.StationOut)
def station_display(station_id: Literal["station-1", "station-2"], store=Depends(db)):
    """Anonymous, read-only: the driver-facing screen. Reuses the connector latest-state pipeline."""
    ids = [cid for cid in CONNECTORS if LOCATION[cid][0] == station_id]
    views = [v.model_dump() for v in connector_views(store, ids)] or [
        {"connector_id": cid, "stale": True} for cid in ids  # no run yet
    ]
    out = []
    for c in views:
        run_id = c.get("run_id")
        vehicle = store.vehicle_states.find_one({"_id": f"{run_id}:{c['connector_id']}"}) if run_id else None
        latest = c.get("latest") or {}
        out.append(
            station.StationConnector(
                connector_id=c["connector_id"],
                status=station.public_status(c, vehicle),
                soc_percent=vehicle["soc_percent"] if vehicle else None,
                power_kw=latest.get("power_kw"),
                current_a=latest.get("current_a"),
                connector_temperature_c=latest.get("connector_temperature_c"),
                energy_kwh=vehicle["energy_kwh"] if vehicle else None,
                minutes_to_full=vehicle.get("minutes_to_full") if vehicle else None,
                measured_at=latest.get("measured_at"),
            )
        )
    return station.StationOut(station_id=station_id, connectors=out)


@app.get("/api/v1/connectors/{connector_id}", response_model=ConnectorOut)
def connector(connector_id: str, store=Depends(db)):
    if connector_id not in CONNECTORS:
        raise ApiError(404, "not_found", "Connector not found")
    views = connector_views(store, [connector_id])
    if views:
        return views[0]
    # No run to report yet (fresh database): a known connector without data, not a 404.
    station_id, charger_id = LOCATION[connector_id]
    return ConnectorOut(connector_id=connector_id, station_id=station_id, charger_id=charger_id, stale=True)


def encode_cursor(row):
    return base64.urlsafe_b64encode(f"{row['measured_at'].isoformat()}|{row['sample_id']}".encode()).decode()


def decode_cursor(cursor):
    try:
        stamp, sample_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        moment = datetime.fromisoformat(stamp)
        if moment.tzinfo is None:
            raise ValueError("naive")
        return moment, sample_id
    except (ValueError, UnicodeDecodeError):
        raise ApiError(422, "validation_error", "cursor: invalid") from None


@app.get("/api/v1/connectors/{connector_id}/telemetry", response_model=TelemetryPage)
def connector_telemetry(
    connector_id: str,
    start: Annotated[AwareDatetime, Query(alias="from")],
    to: AwareDatetime,
    run_id: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    store=Depends(db),
):
    if to <= start or to - start > timedelta(hours=24):
        raise ApiError(422, "validation_error", "from/to: range must be positive and at most 24 hours")
    after = decode_cursor(cursor) if cursor else None
    if run_id is None:
        run = reporting_run(store)
        run_id = run["_id"] if run else None
    if connector_id not in CONNECTORS:
        raise ApiError(404, "not_found", "Connector not found")
    if run_id is None:
        return TelemetryPage(items=[])
    # Whole run history up to `to`, deduplicated by sample_id: the lagged baseline starts cold at run start.
    pipeline = [
        {"$match": {"meta.run_id": run_id, "meta.connector_id": connector_id, "measured_at": {"$lte": to}}},
        {"$sort": {"received_at": 1}},
        {"$group": {"_id": "$sample_id", "sample": {"$first": "$$ROOT"}}},
        {"$replaceRoot": {"newRoot": "$sample"}},
        {"$sort": {"measured_at": 1, "sample_id": 1}},
    ]
    rows = with_residuals(list(store.telemetry.aggregate(pipeline)))
    rows = [
        r
        for r in rows
        if r["measured_at"] >= start and (after is None or (r["measured_at"], r["sample_id"]) > after)
    ]
    page = rows[:limit]
    return TelemetryPage(
        items=[sample_dict(r) for r in page],
        next_cursor=encode_cursor(page[-1]) if len(rows) > limit else None,
    )


@app.get("/api/v1/connectors/{connector_id}/trend", response_model=insights.TrendOut)
def connector_trend(connector_id: ConnectorId, run_id: str, store=Depends(db)):
    rows = store.windows.aggregate(insights.trend_pipeline(run_id, connector_id))
    return insights.TrendOut(
        run_id=run_id, connector_id=connector_id, points=[insights.TrendPoint(**r) for r in rows]
    )


@app.get("/api/v1/insights/fleet", response_model=insights.FleetOut)
def fleet_insights(run_id: str, store=Depends(db)):
    rows = store.windows.aggregate(insights.fleet_pipeline(run_id))
    return insights.FleetOut(run_id=run_id, windows=[insights.FleetWindow(**r) for r in rows])


@app.get("/api/v1/insights/incidents", response_model=insights.IncidentSummary)
def incident_insights(run_id: str | None = None, store=Depends(db)):
    docs = list(store.incidents.aggregate(insights.incident_summary_pipeline(run_id)))
    return insights.shape_summary(docs[0] if docs else {})


@app.get("/api/v1/evaluations", response_model=results.EvaluationList)
def evaluations():
    """Committed offline evaluation summaries (simulated clock), read-only and anonymous like other GETs."""
    return results.EvaluationList(items=results.list_all())


@app.get("/api/v1/evaluations/{evaluation_id}", response_model=results.EvaluationOut)
def evaluation(evaluation_id: str):
    found = results.load(evaluation_id)
    if not found:
        raise ApiError(404, "not_found", "Evaluation not found")
    return found
