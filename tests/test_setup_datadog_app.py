"""B8: Datadog App provisioning (App Builder + HTTP connection) through httpx.MockTransport."""

import importlib.util
import json
from pathlib import Path

import httpx

spec = importlib.util.spec_from_file_location("setup_datadog", Path(__file__).parents[1] / "scripts" / "setup_datadog.py")
sd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sd)

TUNNEL = "https://abc.trycloudflare.com"
ACTION_SECRET = "z" * 32


def app_client(handler):
    return httpx.Client(base_url="https://api.datadoghq.com", transport=httpx.MockTransport(handler))


def provision(handler, state):
    return sd.provision_app(app_client(handler), state, TUNNEL, ACTION_SECRET, lambda: None, "datadoghq.com")


def test_app_definition_substitutes_and_has_four_buttons_and_no_secret():
    app = sd.app_definition(TUNNEL, "conn-1")
    text = json.dumps(app)
    assert "__" not in text and ACTION_SECRET not in text
    assert TUNNEL + "/api/v1/integrations/datadog/incidents" in text
    posts = [q for q in app["queries"] if q["properties"]["spec"]["inputs"]["verb"] == "POST"]
    commands = sorted(q["properties"]["spec"]["inputs"]["body"]["command"] for q in posts)
    assert commands == ["acknowledge", "maintenance", "reduce_load", "verify_recovery"]
    for q in posts:
        assert q["properties"]["onlyTriggerManually"] is True
        assert q["properties"]["spec"]["inputs"]["url"] == TUNNEL + "/api/v1/integrations/datadog/actions"
    buttons = [c for c in app["components"][0]["properties"]["children"] if c["type"] == "button"]
    assert len(buttons) == 4 and all("allowed_commands" in b["properties"]["isDisabled"] for b in buttons)


def test_provision_happy_path_creates_then_updates_without_leaking(capsys):
    seen, state = [], {}

    def handler(request):
        seen.append((request.method, request.url.path, request.content.decode()))
        if request.method == "POST" and request.url.path == "/api/v2/actions/connections":
            return httpx.Response(201, json={"data": {"id": "conn-1"}})
        if request.method == "POST" and request.url.path == "/api/v2/app-builder/apps":
            return httpx.Response(201, json={"data": {"id": "app-1", "type": "appDefinitions"}})
        return httpx.Response(200, json={"data": {"id": "x"}})

    assert provision(handler, state)
    assert state["app_connection"] == "conn-1" and state["app"] == "app-1"
    conn = json.loads(seen[0][2])["data"]["attributes"]["integration"]
    assert conn["base_url"] == TUNNEL and conn["type"] == "HTTP"
    assert any(t["value"] == ACTION_SECRET for t in conn["credentials"]["tokens"])
    assert any(h["name"] == "X-Action-Secret" for h in conn["credentials"]["headers"])
    assert ACTION_SECRET not in capsys.readouterr().out
    seen.clear()
    assert provision(handler, state)
    assert [m for m, _, _ in seen[:2]] == ["PATCH", "PATCH"]
    assert seen[0][1].endswith("/actions/connections/conn-1") and seen[1][1].endswith("/apps/app-1")
    assert seen[-1][0] == "POST" and seen[-1][1].endswith("/apps/app-1/deployment")


def test_provision_falls_back_to_manual_import_on_404(capsys):
    def handler(request):
        return httpx.Response(404, text=f"nope {ACTION_SECRET}")

    state = {}
    assert provision(handler, state) is False
    out = capsys.readouterr().out
    assert "Import JSON" in out and "datadog_app.json" in out and "X-Action-Secret" in out
    assert ACTION_SECRET not in out and "app" not in state
