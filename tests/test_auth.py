"""Operator sessions (CONTRACT §2, §4): login, cookie flags, Origin check, legacy secret header."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from chargeops.api import app
from chargeops.auth import hash_password, verify_password
from chargeops.config import settings
from chargeops.domain import now

PASSWORD = "correct horse battery"
ORIGIN = {"Origin": "http://localhost:5173"}
SECRET = {"X-Operator-Secret": "o" * 32}


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setenv("OPERATOR_USERNAME", "ops")
    monkeypatch.setenv("OPERATOR_PASSWORD_HASH", hash_password(PASSWORD, n=2**10))
    settings.cache_clear()
    return TestClient(app)


def login(client, password=PASSWORD, username="ops"):
    return client.post("/api/v1/auth/login", json={"username": username, "password": password})


def mutate(client, headers):
    body = {"scenario": "healthy", "seed": 1, "mode": "local"}
    return client.post("/api/v1/demo-runs", json=body, headers=headers)


def test_hash_roundtrip():
    encoded = hash_password("pw", n=2**10)
    assert encoded.startswith("scrypt$1024$8$1$")
    assert verify_password("pw", encoded)
    assert not verify_password("other", encoded)
    assert not verify_password("pw", "garbage")


def test_login_ok_sets_session_cookie(client, store):
    response = login(client)
    assert response.status_code == 200
    assert response.json() == {"username": "ops", "role": "operator"}
    cookie = response.headers["set-cookie"].lower()
    assert "chargeops_session=" in cookie
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    token = client.cookies["chargeops_session"]
    stored = store.sessions.find_one()
    assert stored["_id"] != token and token not in str(stored)  # only a digest is stored
    assert client.get("/api/v1/auth/me").json() == {"username": "ops", "role": "operator"}


@pytest.mark.parametrize("password,username", [("wrong", "ops"), (PASSWORD, "intruder")])
def test_login_rejects_bad_credentials(client, password, username):
    response = login(client, password, username)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert "set-cookie" not in response.headers


def test_compose_escaped_hash_still_logs_in_outside_compose(client, monkeypatch):
    # init_env.py writes '$$' so Compose keeps '$'; uvicorn reading .env directly sees the '$$' form.
    monkeypatch.setenv("OPERATOR_PASSWORD_HASH", hash_password(PASSWORD, n=2**10).replace("$", "$$"))
    settings.cache_clear()
    assert login(client).status_code == 200


def test_login_disabled_without_configured_hash(store, monkeypatch):
    monkeypatch.delenv("OPERATOR_PASSWORD_HASH", raising=False)
    settings.cache_clear()
    assert login(TestClient(app)).status_code == 401


def test_me_requires_session(client):
    assert client.get("/api/v1/auth/me").status_code == 401


def test_logout_invalidates_session(client, store):
    login(client)
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert store.sessions.count_documents({}) == 0
    assert client.get("/api/v1/auth/me").status_code == 401


def test_expired_session_rejected(client, store):
    login(client)
    store.sessions.update_many({}, {"$set": {"expires_at": now() - timedelta(seconds=1)}})
    assert client.get("/api/v1/auth/me").status_code == 401


def test_mutation_with_cookie_and_allowed_origin(client):
    login(client)
    assert mutate(client, ORIGIN).status_code == 201


def test_mutation_with_cookie_and_wrong_origin_is_403(client):
    login(client)
    response = mutate(client, {"Origin": "http://evil.example"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert mutate(client, {}).status_code == 403  # missing Origin counts as mismatch


def test_viewer_cannot_mutate(client):
    response = mutate(client, ORIGIN)
    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "unauthorized" and error["request_id"]


def test_operator_secret_header_still_works(client):
    assert mutate(client, SECRET).status_code == 201
    assert mutate(client, {"X-Operator-Secret": "x" * 32}).status_code == 401


def test_incident_command_with_session_cookie_and_origin(client, store):
    store.incidents.insert_one(
        {"_id": "inc-1", "run_id": "r1", "connector_id": "connector-1", "state": "open"}
    )
    login(client)
    url = "/api/v1/incidents/inc-1/commands"
    headers = {"Idempotency-Key": "k1"}
    good = client.post(url, json={"type": "acknowledge"}, headers={**headers, **ORIGIN})
    assert good.status_code == 202
    bad = client.post(url, json={"type": "acknowledge"}, headers={**headers, "Origin": "http://evil.example"})
    assert bad.status_code == 403
