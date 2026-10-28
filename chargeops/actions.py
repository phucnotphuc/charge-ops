"""Datadog App (App Builder) action surface.

Reachable through the public tunnel, so it is deliberately narrow: only `X-Action-Secret` authenticates
(operator cookies and the operator secret are ignored), the command set is the four incident commands, and
every command is an ordinary pending command that the worker validates against its own gates.
"""

import hmac
from typing import Literal

from fastapi import APIRouter, Depends, Header, Request
from fastapi.exceptions import HTTPException
from pydantic import BaseModel, ConfigDict

from chargeops.config import settings
from chargeops.logging import event, logger

# Not in the public OpenAPI/web client: this surface is for the Datadog App only.
router = APIRouter(prefix="/api/v1/integrations/datadog", include_in_schema=False)


class ActionError(HTTPException):
    def __init__(self, status_code, code, message):
        super().__init__(status_code, message)
        self.code = code


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    incident_id: str
    command: Literal["acknowledge", "reduce_load", "maintenance", "verify_recovery"]


def action_auth(x_action_secret: str = Header(default="")):
    secret = settings().datadog_action_secret.get_secret_value()
    if not secret:
        raise ActionError(503, "actions_disabled", "Datadog App actions are not configured")
    if not hmac.compare_digest(x_action_secret.encode(), secret.encode()):
        raise ActionError(401, "unauthorized", "Invalid action credentials")


def db(request: Request):
    return request.app.state.db


def allowed_commands(incident):
    """Same gates as the UI (IncidentDetail) and lifecycle.handle_command."""
    state, allowed = incident.get("state"), []
    if state == "open":
        allowed.append("acknowledge")
    if state in ("acknowledged", "mitigating") and not incident.get("load_reduction_verified_at"):
        allowed.append("reduce_load")
    if state == "mitigated" or (
        state == "mitigating" and incident.get("reduction_check_reason") == "residual_not_recovered"
    ):
        allowed.append("maintenance")
    if state == "mitigated" and incident.get("maintenance_applied_at"):
        allowed.append("verify_recovery")
    return allowed


@router.get("/incidents", dependencies=[Depends(action_auth)])
def incidents(store=Depends(db)):
    run = store.runs.find_one({"status": "active"}, {"_id": 1})
    if not run:
        return {"items": []}
    rows = store.incidents.find({"run_id": run["_id"], "unresolved": True}).sort("opened_at", 1).limit(100)
    return {
        "items": [
            {
                "incident_id": row["_id"],
                "connector_id": row["connector_id"],
                "station_id": row.get("station_id"),
                "state": row["state"],
                "allowed_commands": allowed_commands(row),
                "ui_url": f"/incidents/{row['_id']}",
            }
            for row in rows
        ]
    }


@router.post("/actions", status_code=202, dependencies=[Depends(action_auth)])
def act(
    body: ActionRequest,
    idempotency_key: str = Header(min_length=1, max_length=200),
    store=Depends(db),
):
    from chargeops.api import create_command_doc  # api includes this router at import time

    incident = store.incidents.find_one({"_id": body.incident_id})
    if not incident:
        raise ActionError(404, "not_found", "Incident not found")
    replay = store.commands.find_one({"idempotency_key": idempotency_key})
    # A retried click must return its command even after the incident has moved on.
    if not replay and body.command not in allowed_commands(incident):
        raise ActionError(
            409, "command_not_allowed", f"{body.command} is not allowed in state {incident['state']}"
        )
    created = create_command_doc(store, body.incident_id, body.command, idempotency_key, "datadog-app")
    event(
        logger("api"),
        "datadog_action_requested",
        incident_id=body.incident_id,
        type=body.command,
        command_id=created["command_id"],
    )
    return created
