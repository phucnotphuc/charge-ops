# ChargeOps — Datadog-native Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn every ChargeOps incident into a Datadog Incident with a mirrored timeline, an investigation Notebook and a MongoDB-built postmortem; add SLOs, Browser RUM, and a Datadog App that sends operator commands through a dedicated, gated action API.

**Architecture:** A new `datadog-sync` process (observability profile) watches MongoDB incidents and talks to the Datadog REST API through a small client; the worker stays the only writer of incident state. Postmortems are computed by a MongoDB `$merge` pipeline. SLO counters are emitted by `datadog-sync` and the worker; SLOs, dashboard widgets and the App are provisioned by `scripts/setup_datadog.py`. RUM lives only in the web build. The Datadog App reaches ChargeOps only through two new secret-protected routes exposed by the tunnel gateway.

**Tech Stack:** Python 3.12, FastAPI, PyMongo, httpx, DogStatsD, Datadog REST API (v1 notebooks/SLO, v2 incidents/app-builder), MongoDB 8 aggregation (`$lookup`, `$dateDiff`, `$merge`), React + `@datadog/browser-rum`, Caddy, pytest + mongomock, vitest.

**Spec:** `docs/superpowers/specs/2026-10-28-datadog-operations-design.md` (approved 28/10/2026).

## Global Constraints

- Base: `feat/station-observability` at `e1aa22f`. Each task on its own branch, merged back with `--no-ff` after review.
- Never edit `chargeops/domain.py`, `chargeops/lifecycle.py`, `chargeops/model.py`, `chargeops/evaluation/*`, `chargeops/profiles/autosun_v3.csv`. `python -m chargeops.evaluation run --protocol b1-eval-v3.1 --out .pytest-tmp/eval` must still be accepted after every task.
- Only the worker writes incident state. `datadog-sync` writes only the `datadog_sync` and `incident_reports` collections.
- Only incidents of runs with `mode == "datadog"` are synced to Datadog.
- Secrets (`DD_API_KEY`, `DD_APP_KEY`, `WEBHOOK_SECRET`, `DATADOG_ACTION_SECRET`) never appear in logs, exceptions, frontend bundles, commits or test output. The RUM client token is public by design.
- No network calls in tests: Datadog HTTP goes through `httpx.MockTransport`.
- Docs in Vietnamese; code, comments, commits in English. Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Implementers use model Sonnet. Stop and report when a step needs a live Datadog account or Docker.
- Tooling (Windows, Git Bash): `UV="C:/Users/ADMIN/AppData/Local/Temp/claude/D--devops-charge-ops/72e9c51e-d7d6-41be-9f68-b8eea7f3c93e/scratchpad/uvenv/Scripts/uv"`; tests `"$UV" run pytest -q -p no:cacheprovider --basetemp .pytest-tmp`; lint `"$UV" run ruff check chargeops scripts tests`; web `cd web && npm ci && npm run build && npm test -- --run && npm run check:api`; compose `SIMULATOR_SECRET=x WEBHOOK_SECRET=x OPERATOR_SECRET=x docker compose --profile observability config --quiet`.
- Schedule: Task 1 and Task 5 run in parallel (disjoint files); then Tasks 2 → 3 → 4 → 6 in order.

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `chargeops/datadog_client.py` | Create | Thin Datadog REST client (incidents, notebooks, attachments), retries, secret-safe errors |
| `chargeops/datadog_sync.py` | Create | Sync loop: incident → Datadog Incident, timeline mirror, notebook, postmortem, SLO outcome |
| `chargeops/notebooks.py` | Create | Pure builder for the investigation/postmortem Notebook payload |
| `chargeops/postmortem.py` | Create | `$merge` pipeline into `incident_reports`, markdown summary |
| `chargeops/actions.py` | Create | Datadog App action routes (APIRouter), allowed-command gate mirror |
| `chargeops/observability.py` | Modify | `emit_charging_slo`, `emit_incident_outcome` |
| `chargeops/api.py` | Modify | Incident links from `datadog_sync`; include the actions router |
| `chargeops/config.py` | Modify | `dd_api_key`, `dd_app_key`, `dd_site`, `datadog_action_secret` settings |
| `chargeops/worker.py` | Modify | Call `emit_charging_slo` in `evaluate_window` (datadog mode) |
| `scripts/setup_datadog.py` | Modify | SLOs + SLO widget; `--with-app` App Builder provisioning |
| `scripts/datadog_app.json` | Create | App Builder definition |
| `infra/Caddyfile` | Modify | Allow the two action paths through the tunnel gateway |
| `compose.yaml`, `.env.example`, `scripts/init_env.py` | Modify | `datadog-sync` service, `DATADOG_ACTION_SECRET`, RUM build args |
| `web/src/rum.ts`, `web/src/main.tsx`, `web/src/pages/IncidentDetail.tsx`, `web/Dockerfile`, `web/package.json` | Create/Modify | RUM init and actions; Datadog links on incident page |
| `docs/OBSERVABILITY.md`, `docs/CONTRACT.md`, `docs/KIEM_TRA.md` | Modify | Keep docs true |
| tests: `tests/test_datadog_client.py`, `tests/test_datadog_sync.py`, `tests/test_notebooks.py`, `tests/test_postmortem.py`, `tests/test_slo.py`, `tests/test_actions.py`, `web/src/test/rum.test.ts` | Create | One per module |

---

### Task 1 (B4): Datadog client and incident sync

**Files:**
- Create: `chargeops/datadog_client.py`, `chargeops/datadog_sync.py`
- Modify: `chargeops/config.py`, `chargeops/api.py` (incident route only), `compose.yaml`, `.env.example`
- Test: `tests/test_datadog_client.py`, `tests/test_datadog_sync.py`

**Interfaces:**
- Produces:
  - `DatadogClient(api_key: str, app_key: str, site: str, transport: httpx.BaseTransport | None = None)` with methods
    `create_incident(title: str, chargeops_incident_id: str) -> dict` (returns `{"id", "public_id"}`),
    `incident_note(dd_incident_id: str, text: str) -> None`,
    `resolve_incident(dd_incident_id: str) -> None`,
    `find_incident(chargeops_incident_id: str) -> dict | None`,
    `incident_url(public_id) -> str` (`https://app.{site}/incidents/{public_id}`),
    plus (used by Task 2) `create_notebook(payload: dict) -> int`, `update_notebook(notebook_id: int, payload: dict) -> None`, `attach_link(dd_incident_id: str, url: str, title: str) -> None`.
  - `DatadogError(Exception)` with `status: int` and a message that never contains request headers or bodies.
  - `DatadogSync(db, client, stats, log, site: str)` with `sync_once(now: datetime) -> int` (number of incidents touched) and `run_forever(interval=5)`.
  - Collection `datadog_sync`: `{_id: incident_id, run_id, dd_incident_id, dd_public_id, dd_incident_url, create_attempted_at, synced_events: int, dd_resolved: bool, notebook_id, notebook_url, postmortem_done: bool, outcome_emitted: bool, attempts: int, last_error: str|None, next_attempt_at}`.
  - Settings `dd_api_key: SecretStr = SecretStr("")`, `dd_app_key: SecretStr = SecretStr("")`, `dd_site: str = "datadoghq.com"`.
  - Incident API (`GET /api/v1/incidents/{id}`) adds `datadog_incident_url` and `datadog_notebook_url` (null when absent).

- [ ] **Step 1: Read the current Datadog Incidents API docs** with WebFetch on `https://docs.datadoghq.com/api/latest/incidents/`. Record in the report, verbatim from the docs: the create endpoint and minimal body, the update endpoint used to set state `resolved`, and whether a writable timeline endpoint exists. If it does not, use incident todos (`POST /api/v2/incidents/{incident_id}/relationships/todos`) with `content` = the step text and mark them complete, or attachments — whichever the docs show as writable — and note the choice. Also confirm whether these endpoints are marked "unstable" and need a header.

- [ ] **Step 2: Failing client tests** `tests/test_datadog_client.py`

```python
import json

import httpx
import pytest

from chargeops.datadog_client import DatadogClient, DatadogError


def client(handler):
    return DatadogClient("api-key-123", "app-key-456", "ap1.datadoghq.com", transport=httpx.MockTransport(handler))


def test_create_incident_sends_keys_and_returns_ids():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"data": {"id": "abc", "attributes": {"public_id": 42}}})

    out = client(handler).create_incident("ChargeOps connector-4 quá nhiệt", "inc-1")
    assert out == {"id": "abc", "public_id": 42}
    assert seen["url"].startswith("https://api.ap1.datadoghq.com/api/v2/incidents")
    assert seen["headers"]["DD-API-KEY"] == "api-key-123"
    assert seen["headers"]["DD-APPLICATION-KEY"] == "app-key-456"
    assert "inc-1" in json.dumps(seen["body"])


def test_error_message_never_leaks_keys_or_body():
    def handler(request):
        return httpx.Response(403, json={"errors": ["Forbidden"], "echo": "api-key-123"})

    with pytest.raises(DatadogError) as info:
        client(handler).create_incident("t", "inc-1")
    assert info.value.status == 403
    assert "api-key-123" not in str(info.value) and "app-key-456" not in str(info.value)


def test_retries_on_429_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(201, json={"data": {"id": "x", "attributes": {"public_id": 1}}})

    assert client(handler).create_incident("t", "inc-1")["id"] == "x"
    assert len(calls) == 3


def test_incident_url():
    assert client(lambda r: httpx.Response(200)).incident_url(42) == "https://app.ap1.datadoghq.com/incidents/42"
```

- [ ] **Step 3: Implement** `chargeops/datadog_client.py`

```python
"""Minimal Datadog REST client for datadog-sync. Errors carry the HTTP status and a short reason only — never
headers, request bodies or response bodies (they can echo keys or webhook headers)."""

import time

import httpx

RETRY_STATUS = {429, 500, 502, 503, 504}


class DatadogError(Exception):
    def __init__(self, method, path, status):
        super().__init__(f"Datadog {method} {path}: HTTP {status}")
        self.status = status


class DatadogClient:
    def __init__(self, api_key, app_key, site, transport=None, retries=3):
        self.site = site
        self.retries = retries
        self.http = httpx.Client(
            base_url=f"https://api.{site}",
            headers={"DD-API-KEY": api_key, "DD-APPLICATION-KEY": app_key, "Content-Type": "application/json"},
            timeout=20,
            transport=transport,
        )

    def call(self, method, path, payload=None):
        for attempt in range(self.retries):
            response = self.http.request(method, path, json=payload)
            if response.status_code in RETRY_STATUS and attempt < self.retries - 1:
                time.sleep(float(response.headers.get("Retry-After", 2**attempt)))
                continue
            if response.is_error:
                raise DatadogError(method, path, response.status_code)
            return response.json() if response.content else {}
        raise DatadogError(method, path, response.status_code)

    def incident_url(self, public_id):
        return f"https://app.{self.site}/incidents/{public_id}"

    def create_incident(self, title, chargeops_incident_id):
        body = {
            "data": {
                "type": "incidents",
                "attributes": {
                    "title": title,
                    "customer_impacted": False,
                    "fields": {"state": {"type": "dropdown", "value": "active"}},
                },
            }
        }
        # Keep the ChargeOps id searchable for the restart duplicate guard (find_incident).
        body["data"]["attributes"]["title"] = f"{title} [{chargeops_incident_id}]"
        data = self.call("POST", "/api/v2/incidents", body)["data"]
        return {"id": data["id"], "public_id": data["attributes"]["public_id"]}
```

Then add `incident_note`, `resolve_incident`, `find_incident`, `attach_link`, `create_notebook`, `update_notebook` using the endpoints recorded in Step 1 (notebooks: `POST /api/v1/notebooks` returns `data.id`; `PUT /api/v1/notebooks/{id}` with the same `{"data": {"type": "notebooks", "attributes": …}}` shape). Each gets one MockTransport test asserting method, path and that the ChargeOps text/URL is in the body.

- [ ] **Step 4: Failing sync tests** `tests/test_datadog_sync.py`

```python
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import mongomock

from chargeops.datadog_sync import DatadogSync

T = datetime(2026, 10, 28, 3, 0, tzinfo=UTC)
RUN = "00000000-0000-4000-8000-0000000000f9"


def setup(mode="datadog", timeline=None, state="open"):
    db = mongomock.MongoClient(tz_aware=True).chargeops
    db.runs.insert_one({"_id": RUN, "mode": mode, "status": "active"})
    db.incidents.insert_one(
        {"_id": "inc-1", "run_id": RUN, "connector_id": "connector-4", "state": state, "state_since": T,
         "opened_at": T, "unresolved": state != "resolved",
         "timeline": timeline or [{"at": T, "event": "opened", "source": "datadog"}]}
    )
    client = Mock()
    client.create_incident.return_value = {"id": "dd-1", "public_id": 7}
    client.incident_url.return_value = "https://app.ap1.datadoghq.com/incidents/7"
    client.find_incident.return_value = None
    return db, client, DatadogSync(db, client, Mock(), Mock(), "ap1.datadoghq.com")


def test_creates_one_datadog_incident_and_saves_the_id():
    db, client, sync = setup()
    sync.sync_once(T)
    sync.sync_once(T + timedelta(seconds=5))
    client.create_incident.assert_called_once()
    doc = db.datadog_sync.find_one({"_id": "inc-1"})
    assert doc["dd_incident_id"] == "dd-1" and doc["dd_incident_url"].endswith("/incidents/7")


def test_local_mode_runs_are_never_synced():
    db, client, sync = setup(mode="local")
    sync.sync_once(T)
    client.create_incident.assert_not_called()


def test_each_timeline_step_is_mirrored_once():
    db, client, sync = setup()
    sync.sync_once(T)
    db.incidents.update_one({"_id": "inc-1"}, {"$push": {"timeline": {"at": T, "event": "acknowledge"}}})
    sync.sync_once(T)
    sync.sync_once(T)
    texts = [c.args[1] for c in client.incident_note.call_args_list]
    assert len(texts) == 2 and "acknowledge" in texts[1]


def test_resolved_incident_resolves_datadog_incident_once():
    db, client, sync = setup(state="resolved", timeline=[{"at": T, "event": "opened"}, {"at": T, "event": "resolved"}])
    sync.sync_once(T)
    sync.sync_once(T)
    client.resolve_incident.assert_called_once_with("dd-1")


def test_api_error_is_recorded_and_retried_later_without_touching_the_incident():
    from chargeops.datadog_client import DatadogError

    db, client, sync = setup()
    client.create_incident.side_effect = [DatadogError("POST", "/api/v2/incidents", 503), {"id": "dd-1", "public_id": 7}]
    before = db.incidents.find_one({"_id": "inc-1"})
    sync.sync_once(T)
    doc = db.datadog_sync.find_one({"_id": "inc-1"})
    assert doc["attempts"] == 1 and "503" in doc["last_error"]
    assert db.incidents.find_one({"_id": "inc-1"}) == before
    sync.sync_once(T + timedelta(minutes=2))  # past next_attempt_at
    assert db.datadog_sync.find_one({"_id": "inc-1"})["dd_incident_id"] == "dd-1"


def test_restart_after_create_without_saved_id_reuses_existing_incident():
    db, client, sync = setup()
    db.datadog_sync.insert_one({"_id": "inc-1", "run_id": RUN, "create_attempted_at": T, "synced_events": 0, "attempts": 0})
    client.find_incident.return_value = {"id": "dd-old", "public_id": 3}
    sync.sync_once(T)
    client.create_incident.assert_not_called()
    assert db.datadog_sync.find_one({"_id": "inc-1"})["dd_incident_id"] == "dd-old"
```

- [ ] **Step 5: Implement** `chargeops/datadog_sync.py`

```python
"""datadog-sync: mirror ChargeOps incidents of datadog-mode runs into Datadog (Incident, timeline notes, resolve;
notebook and postmortem in later tasks). Never writes incidents; only datadog_sync / incident_reports."""

import time
from datetime import timedelta

from pymongo.errors import PyMongoError

from chargeops.config import settings
from chargeops.datadog_client import DatadogClient, DatadogError
from chargeops.domain import now
from chargeops.logging import event, logger
from chargeops.storage import database

BACKOFF_S = (30, 60, 120, 300)


def note_text(incident, entry):
    reason = entry.get("reason") or entry.get("command_id") or ""
    return f"{entry['at'].isoformat()} · {entry['event']} · {incident['connector_id']}" + (f" · {reason}" if reason else "")


class DatadogSync:
    def __init__(self, db, client, stats, log, site):
        self.db, self.client, self.stats, self.log, self.site = db, client, stats, log, site

    def sync_once(self, at):
        touched = 0
        runs = {r["_id"] for r in self.db.runs.find({"mode": "datadog"}, {"_id": 1})}
        for incident in self.db.incidents.find({"run_id": {"$in": list(runs)}}):
            state = self.db.datadog_sync.find_one({"_id": incident["_id"]}) or {
                "_id": incident["_id"], "run_id": incident["run_id"], "synced_events": 0, "attempts": 0,
            }
            if state.get("next_attempt_at") and state["next_attempt_at"] > at:
                continue
            try:
                self.sync_incident(incident, state, at)
                state.update(attempts=0, last_error=None, next_attempt_at=None)
            except DatadogError as error:
                state["attempts"] = state.get("attempts", 0) + 1
                state["last_error"] = str(error)
                state["next_attempt_at"] = at + timedelta(seconds=BACKOFF_S[min(state["attempts"], len(BACKOFF_S)) - 1])
                event(self.log, "datadog_sync_failed", incident_id=incident["_id"], status=error.status)
            self.db.datadog_sync.replace_one({"_id": incident["_id"]}, state, upsert=True)
            touched += 1
        return touched

    def sync_incident(self, incident, state, at):
        if not state.get("dd_incident_id"):
            found = self.client.find_incident(incident["_id"]) if state.get("create_attempted_at") else None
            if not found:
                state["create_attempted_at"] = at
                self.db.datadog_sync.replace_one({"_id": incident["_id"]}, state, upsert=True)
                found = self.client.create_incident(f"ChargeOps {incident['connector_id']} quá nhiệt", incident["_id"])
            state.update(dd_incident_id=found["id"], dd_public_id=found["public_id"],
                         dd_incident_url=self.client.incident_url(found["public_id"]))
            self.db.datadog_sync.replace_one({"_id": incident["_id"]}, state, upsert=True)
        timeline = incident.get("timeline", [])
        for entry in timeline[state.get("synced_events", 0):]:
            self.client.incident_note(state["dd_incident_id"], note_text(incident, entry))
            state["synced_events"] = state.get("synced_events", 0) + 1
            self.db.datadog_sync.replace_one({"_id": incident["_id"]}, state, upsert=True)
        if incident["state"] == "resolved" and not state.get("dd_resolved"):
            self.client.resolve_incident(state["dd_incident_id"])
            state["dd_resolved"] = True


def main():
    config = settings()
    db = database()
    client = DatadogClient(config.dd_api_key.get_secret_value(), config.dd_app_key.get_secret_value(), config.dd_site)
    from datadog import DogStatsd

    sync = DatadogSync(db, client, DogStatsd(host=config.dd_agent_host, port=8125), logger("datadog-sync"), config.dd_site)
    while True:
        try:
            sync.sync_once(now())
        except PyMongoError as error:
            event(sync.log, "datadog_sync_retry", error_type=type(error).__name__)
        time.sleep(5)


if __name__ == "__main__":
    main()
```

(Polling every 5 s is enough here; Change Streams are optional — if added, follow `worker.ChangeWaker` and keep the timed loop.)

- [ ] **Step 6: Settings, Compose, env.** `config.py`: `dd_api_key: SecretStr = SecretStr("")`, `dd_app_key: SecretStr = SecretStr("")`, `dd_site: str = "datadoghq.com"`. `compose.yaml` new service:

```yaml
  datadog-sync:
    <<: *app
    profiles: [observability]
    environment: {<<: *env, DD_SERVICE: chargeops-datadog-sync, DD_API_KEY: "${DD_API_KEY:-}", DD_APP_KEY: "${DD_APP_KEY:-}", DD_SITE: "${DD_SITE:-datadoghq.com}"}
    command: [python, -m, chargeops.datadog_sync]
```

`.env.example`: one comment line saying datadog-sync reuses `DD_API_KEY`/`DD_APP_KEY`/`DD_SITE`.

- [ ] **Step 7: Incident links in the API.** In `api.py` `incident()` after loading commands:

```python
    sync = store.datadog_sync.find_one({"_id": incident_id}, {"dd_incident_url": 1, "notebook_url": 1}) or {}
    found["datadog_incident_url"] = sync.get("dd_incident_url")
    found["datadog_notebook_url"] = sync.get("notebook_url")
```

Add a test in `tests/test_datadog_sync.py` using the `store` fixture and `TestClient(app)`.

- [ ] **Step 8: Run** tests, ruff, evaluation lock, compose config. Expected: all pass.
- [ ] **Step 9: Commit** — `git commit -m "feat(datadog): datadog-sync mirrors incidents of datadog runs into Datadog Incidents"`

---

### Task 2 (B5): Investigation Notebook per incident

**Files:** Create `chargeops/notebooks.py`; Modify `chargeops/datadog_sync.py`, `web/src/pages/IncidentDetail.tsx`, `web/src/api/types.ts`; Test `tests/test_notebooks.py`, `tests/test_datadog_sync.py`, `web/src/test/app.test.tsx`.

**Interfaces:**
- Consumes: `DatadogClient.create_notebook/update_notebook/attach_link` (Task 1), incident doc fields, windows doc fields (`end`, `residual_mean`, `quality`).
- Produces: `investigation_notebook(incident: dict, site: str, postmortem_md: str | None = None, now: datetime | None = None) -> dict` (full `{"data": {"type": "notebooks", "attributes": {...}}}` payload); `notebook_url(site, notebook_id) -> str` (`https://app.{site}/notebook/{id}`); sync fields `notebook_id`, `notebook_url`.

- [ ] **Step 1: Failing tests** `tests/test_notebooks.py`

```python
from datetime import UTC, datetime

from chargeops.notebooks import investigation_notebook, notebook_url

T = datetime(2026, 10, 28, 3, 0, tzinfo=UTC)
INCIDENT = {"_id": "inc-1", "run_id": "r1", "connector_id": "connector-4", "state": "mitigating", "opened_at": T,
            "evidence": {"residual_mean": 9.4, "end": T}, "timeline": [{"at": T, "event": "opened"}]}


def cells(payload):
    return [c["attributes"]["definition"] for c in payload["data"]["attributes"]["cells"]]


def test_notebook_is_scoped_to_run_and_connector():
    defs = cells(investigation_notebook(INCIDENT, "ap1.datadoghq.com", now=T))
    queries = [r.get("q", "") for d in defs if d["type"] == "timeseries" for r in d["requests"]]
    assert queries and all("run_id:r1" in q and "connector_id:connector-4" in q for q in queries)


def test_notebook_has_evidence_markdown_and_log_cell():
    defs = cells(investigation_notebook(INCIDENT, "ap1.datadoghq.com", now=T))
    text = "\n".join(d.get("text", "") for d in defs if d["type"] == "markdown")
    assert "9.4" in text and "connector-4" in text
    assert any(d["type"] == "log_stream" and "@incident_id:inc-1" in d["query"] for d in defs)


def test_postmortem_cell_added_when_given():
    defs = cells(investigation_notebook(INCIDENT, "ap1.datadoghq.com", postmortem_md="## Postmortem\nMTTR 14 phút", now=T))
    assert any("MTTR 14 phút" in d.get("text", "") for d in defs)


def test_time_span_covers_incident():
    attrs = investigation_notebook(INCIDENT, "ap1.datadoghq.com", now=T)["data"]["attributes"]
    assert attrs["time"]["start"] <= int(T.timestamp() * 1000) - 5 * 60 * 1000


def test_url():
    assert notebook_url("ap1.datadoghq.com", 12) == "https://app.ap1.datadoghq.com/notebook/12"
```

- [ ] **Step 2: Implement** `chargeops/notebooks.py` building cells: markdown header (title, run, connector, opened_at, evidence residual), timeseries residual (`(sum:chargeops.residual_positive{...}.as_count()-sum:chargeops.residual_negative{...}.as_count())/sum:chargeops.samples{...}.as_count()` with `{env:hackathon,run_id:R,connector_id:C}`), measured vs expected temperature, current vs limit, `chargeops.incident.state_age_s`, a `log_stream` cell `service:chargeops @incident_id:<id>` (fallback `@run_id:<run>` cell too), and optional postmortem markdown. Time: `{"start": opened_at − 5 min (ms), "end": (resolved time or now) + 5 min (ms)}`. Use the cell shape `{"type": "notebook_cells", "attributes": {"definition": {...}, "graph_size": "m"}}` (markdown cells without `graph_size`).
- [ ] **Step 3: Sync wiring.** In `DatadogSync.sync_incident`, right after the Datadog Incident exists and before mirroring the timeline: if no `notebook_id`, `create_notebook(investigation_notebook(...))`, save `notebook_id`/`notebook_url`, then `attach_link(dd_incident_id, notebook_url, "Notebook điều tra")`. Tests: notebook created once; attach called once; a notebook API failure is retried like Task 1.
- [ ] **Step 4: Web links.** `types.ts` `Incident` gains `datadog_incident_url?: string | null; datadog_notebook_url?: string | null;`. `IncidentDetail.tsx` shows "Mở trên Datadog" and "Notebook điều tra" links (`target="_blank" rel="noreferrer"`) when present, nothing otherwise; vitest test for both cases.
- [ ] **Step 5: Run all checks, commit** — `feat(datadog): investigation notebook per incident, linked from Datadog and the UI`

---

### Task 3 (B7): MongoDB postmortem and Datadog update

**Files:** Create `chargeops/postmortem.py`; Modify `chargeops/datadog_sync.py`, `chargeops/check_mongo.py`; Test `tests/test_postmortem.py`, `tests/test_datadog_sync.py`.

**Interfaces:**
- Consumes: incidents (`opened_at`, `timeline[{at,event}]`, `reference_current_a`, `target_current_a`, `run_id`, `connector_id`), windows (`run_id`, `connector_id`, `end`, `residual_mean`, `quality`), telemetry (`meta.run_id`, `meta.connector_id`, `measured_at`, `power_kw`).
- Produces: `postmortem_pipeline(incident_id: str) -> list[dict]` ending in `{"$merge": {"into": "incident_reports", "whenMatched": "replace", "whenNotMatched": "insert"}}`; `report_markdown(report: dict) -> str` (Vietnamese); report fields `_id, run_id, connector_id, opened_at, closed_at, closed_by ("resolved"|"run_stopped"), seconds_to_ack, seconds_to_effective_reduction, seconds_to_mitigated, seconds_to_resolved, peak_residual_c, retest_resets, reference_current_a, target_current_a, energy_kwh_while_derated, simulated: true`.

- [ ] **Step 1: Failing tests** `tests/test_postmortem.py`: (a) pipeline shape — first stage `$match {"_id": id}`, contains `$lookup` from `windows` with `pipeline` filtering by run/connector and `end` between opened and closed, last stage the `$merge` above; (b) `report_markdown` renders durations as "x phút y giây", shows "—" for null, includes "Dữ liệu mô phỏng"; (c) a `@pytest.mark.mongo` real-Mongo test (skipped without `MONGODB_TEST_URI`) inserting one incident with timeline and 3 windows, running the pipeline, and asserting `peak_residual_c` and `seconds_to_resolved`.
- [ ] **Step 2: Implement** the pipeline with `$set` of `ack_at`/`verified_at`/`mitigated_at`/`resolved_at`/`stopped_at` via `$first` of `$map`/`$filter` over `timeline` (event names `acknowledge`, `load_reduction_verified`, `mitigated`, `resolved`, `run_stopped`), `closed_at = $ifNull[resolved_at, stopped_at]`, `$dateDiff` second durations from `opened_at`, `$lookup` windows → `peak_residual_c: {$max: "$windows.residual_mean"}`, `$lookup` telemetry between `verified_at` and `$ifNull[mitigated_at, closed_at]` → `energy_kwh_while_derated: sum(power_kw) * 5 / 3600`, `retest_resets: $size of $filter timeline event == "retest_reset"`, `$project` to the report fields, `$merge`.
- [ ] **Step 3: Sync wiring.** When an incident is `resolved`, or its run is `stopped` while unresolved, and `postmortem_done` is false: run `db.incidents.aggregate(postmortem_pipeline(id))` (MongoDB 8 only; on mongomock the test monkeypatches it), read `incident_reports`, `update_notebook(notebook_id, investigation_notebook(..., postmortem_md=report_markdown(report)))`, `incident_note(dd_id, short summary)`, set `postmortem_done`. Tests with mocks.
- [ ] **Step 4: `check_mongo.py`** runs the pipeline on its smoke incident and prints `postmortem $merge ok`.
- [ ] **Step 5: Run all checks, commit** — `feat(mongo): postmortem via $lookup/$dateDiff/$merge into incident_reports, pushed to Datadog`

---

### Task 4 (B6): SLO metrics, SLOs and dashboard widget

**Files:** Modify `chargeops/observability.py`, `chargeops/worker.py` (`evaluate_window` only), `chargeops/datadog_sync.py`, `scripts/setup_datadog.py`; Test `tests/test_slo.py`, `tests/test_setup_datadog.py`.

**Interfaces:**
- Produces:
  - `emit_charging_slo(stats, run_id, connector_id, latest: dict | None) -> None`: if `latest` and `latest["requested_current_a"] > 0`: increment `chargeops.slo.charging_minutes.total` by 0.25 (one 15-second block = 0.25 min) and, if `latest["current_a"] > 0`, `chargeops.slo.charging_minutes.good` by 0.25; tags `connector_tags(run_id, connector_id)`.
  - `emit_incident_outcome(stats, report: dict) -> None`: if `report["seconds_to_effective_reduction"]` is not None or the incident reached reduce_load: increment `chargeops.slo.mitigation.total`; increment `.good` when `seconds_to_effective_reduction <= 300`. Called by `datadog-sync` once per incident (guard `outcome_emitted`).
  - `slo_specs() -> dict[str, dict]` keys `slo_mitigation`, `slo_charging`; `slo_widgets(ids: dict) -> list[dict]`; `engineering_dashboard(slo_ids: dict | None = None)`.
- [ ] **Step 1: Failing tests** for both emitters (Mock stats; exact names/values/tags), for `slo_specs()` (type `metric`, numerator/denominator queries on the names above with `{env:hackathon}` and `.as_count()`, thresholds `[{"timeframe": "7d", "target": 95.0}, {"timeframe": "30d", "target": 95.0}]`), for `engineering_dashboard({"slo_mitigation": "a", "slo_charging": "b"})` containing two `{"type": "slo", "slo_id": …, "view_type": "detail", "time_windows": ["7d", "30d"]}` widgets, and a MockTransport `main()` test that SLOs are POSTed before dashboards on the first run and PUT on the second.
- [ ] **Step 2: Implement.** Worker: in `evaluate_window`, Datadog branch, after the existing gauges: `emit_charging_slo(stats, run["_id"], connector_id, block[-1] if block else None)`. setup: create/update SLOs (`POST/PUT /api/v1/slo`, state keys above) before the dashboards loop; pass ids into `engineering_dashboard`. Sync: after the postmortem, `emit_incident_outcome` once.
- [ ] **Step 3: Docs** — OBSERVABILITY.md: SLO table (name, good/total, target, what it means).
- [ ] **Step 4: Run all checks, commit** — `feat(datadog): SLOs for effective derating within 5 min and uninterrupted charging minutes`

---

### Task 5 (B3): Browser RUM (parallel with Task 1)

**Files:** Create `web/src/rum.ts`, `web/src/test/rum.test.ts`; Modify `web/src/main.tsx`, `web/src/pages/IncidentDetail.tsx`, `web/package.json` + lock, `web/Dockerfile`, `compose.yaml` (web build args), `.env.example`, `web/src/vite-env.d.ts` (env types); docs `docs/OBSERVABILITY.md`.

**Interfaces:**
- Produces: `initRum(env = import.meta.env): boolean` (true only when application id and client token are non-empty); `trackCommand(type: string, context: Record<string, unknown>): void`; `markIncidentSeen(incidentId: string): void`; timing name `incident_seen_to_command_ms`.
- [ ] **Step 1:** `cd web && npm install @datadog/browser-rum@5` (pins an exact 5.x in the lockfile; record the version in DISCLOSURE.md's OSS table).
- [ ] **Step 2: Failing tests** `web/src/test/rum.test.ts` with `vi.mock("@datadog/browser-rum", () => ({ datadogRum: { init: vi.fn(), addAction: vi.fn(), addTiming: vi.fn() } }))`: no env → `initRum` false and `init` not called; full env → `init` called once with `{applicationId, clientToken, site, service: "chargeops-web", env, version, sessionSampleRate: 100, sessionReplaySampleRate: 0, trackUserInteractions: true, defaultPrivacyLevel: "mask-user-input"}` and an `allowedTracingUrls` entry matching `${origin}/api/`; `trackCommand` calls `addAction("command_sent", …)` and, after `markIncidentSeen`, `addTiming("incident_seen_to_command_ms")`; all helpers are no-ops when RUM is off.
- [ ] **Step 3: Implement** `rum.ts` (keep state in a module variable), call `initRum()` in `main.tsx` before render, call `markIncidentSeen` when IncidentDetail first loads an incident and `trackCommand` in the command click handler (after the request is sent).
- [ ] **Step 4: Build args.** `web/Dockerfile` build stage: `ARG VITE_DD_RUM_APPLICATION_ID= VITE_DD_RUM_CLIENT_TOKEN= VITE_DD_SITE=datadoghq.com VITE_DD_ENV=hackathon VITE_DD_VERSION=0.1.0` and `ENV` them before `npm run build`. `compose.yaml` web `build.args` maps them from `${DD_RUM_APPLICATION_ID:-}`, `${DD_RUM_CLIENT_TOKEN:-}`, `${DD_SITE:-datadoghq.com}`, `hackathon`, `${APP_VERSION:-0.1.0}`. `.env.example`: `DD_RUM_APPLICATION_ID=`, `DD_RUM_CLIENT_TOKEN=` with a comment "tạo RUM application (JS) trên Datadog, rồi build lại web".
- [ ] **Step 5: Docs** — OBSERVABILITY.md RUM section: how to create the RUM app, which actions/timings exist, privacy settings; KIEM_TRA.md mục 7 one step for RUM.
- [ ] **Step 6: Web checks, compose config, commit** — `feat(web): Datadog Browser RUM with operator command actions and APM linking`

---

### Task 6 (B8): Act from Datadog — action API, gateway, App

**Files:** Create `chargeops/actions.py`, `scripts/datadog_app.json`, `tests/test_actions.py`; Modify `chargeops/api.py` (include router), `chargeops/config.py`, `infra/Caddyfile`, `scripts/setup_datadog.py` (`--with-app`), `scripts/init_env.py` (generate `DATADOG_ACTION_SECRET`), `compose.yaml`, `.env.example`, `docs/CONTRACT.md`, `docs/OBSERVABILITY.md`, `docs/KIEM_TRA.md`; Test `tests/test_actions.py`, `tests/test_setup_datadog.py`, `tests/test_init_env.py`.

**Interfaces:**
- Produces:
  - Setting `datadog_action_secret: SecretStr = SecretStr("")` (empty → routes return 503 `actions_disabled`).
  - `router = APIRouter(prefix="/api/v1/integrations/datadog")` with
    `GET /incidents` → `{"items": [{"incident_id", "connector_id", "station_id", "state", "allowed_commands": [...], "ui_url"}]}` (open incidents of the active run only);
    `POST /actions` body `{"incident_id": str, "command": "acknowledge"|"reduce_load"|"maintenance"|"verify_recovery"}`, headers `X-Action-Secret`, `Idempotency-Key` (1–200 chars) → 202 `{"command_id", "status"}`.
  - `allowed_commands(incident: dict) -> list[str]` mirroring the UI/worker gates: acknowledge if `open`; reduce_load if (`acknowledged` or `mitigating`) and no `load_reduction_verified_at`; maintenance if `mitigated` or (`mitigating` and `reduction_check_reason == "residual_not_recovered"`); verify_recovery if `mitigated` and `maintenance_applied_at`.
  - Commands created here carry `actor: "datadog-app"`; everything else identical to `api.create_command` (factor the shared insert into `create_command_doc(store, incident_id, type, idempotency_key, actor)` in `api.py` and use it from both routes).
- [ ] **Step 1: Failing tests** `tests/test_actions.py`: missing/wrong secret → 401 (constant-time compare); empty configured secret → 503; unknown incident → 404; command not in `allowed_commands` → 409 `command_not_allowed`; missing Idempotency-Key → 422; same key twice → same command id; created command has `actor == "datadog-app"`, `validated == False`, `status == "pending"`; `GET /incidents` lists only unresolved incidents of the active run and the right `allowed_commands`; the operator route still works and gets `actor == "operator"`; none of the action routes accept an operator session cookie as auth.
- [ ] **Step 2: Implement** `actions.py` and the `api.py` refactor (`create_command_doc`), include the router; log `datadog_action_requested` with incident id, command type and command id only.
- [ ] **Step 3: Gateway.** `infra/Caddyfile`: add an `@actions` matcher — `POST /api/v1/integrations/datadog/actions` and `GET /api/v1/integrations/datadog/incidents` — proxied to `api:8000` with `request_body max_size 4KB`; keep the webhook block and the final 404. Add `tests/test_gateway.py` that parses the file text and asserts exactly these three paths are proxied and everything else falls to 404.
- [ ] **Step 4: App definition and provisioning.** WebFetch the App Builder API docs (`https://docs.datadoghq.com/api/latest/app-builder/`) and the Action Connections docs (`https://docs.datadoghq.com/api/latest/action-connection/`); record endpoints in the report. `scripts/datadog_app.json`: an app with a table bound to an HTTP query `GET {tunnel}/api/v1/integrations/datadog/incidents` and four buttons calling `POST {tunnel}/api/v1/integrations/datadog/actions` with `{incident_id, command}` and a generated Idempotency-Key, each button enabled only when the command is in `allowed_commands`. `setup_datadog.py --with-app`: create/update an HTTP connection whose base URL is the tunnel origin and whose header `X-Action-Secret` comes from `DATADOG_ACTION_SECRET` (never printed), then create/update the app (state keys `app_connection`, `app`), and add an `app` widget to the engineering dashboard if the dashboard API accepts it; if any App Builder call returns 404/403, print the manual "Import JSON" steps and continue. MockTransport test for the happy path and the 404 fallback.
- [ ] **Step 5: Secret generation.** `init_env.py` writes `DATADOG_ACTION_SECRET=<token_urlsafe(32)>`; test that it is generated and never printed. `compose.yaml` passes `DATADOG_ACTION_SECRET: ${DATADOG_ACTION_SECRET:-}` to the api service.
- [ ] **Step 6: Docs.** CONTRACT §2: the two routes, auth, 401/404/409/422/503, `actor`. OBSERVABILITY.md: App section and the security boundary (tunnel exposes only webhook + these two routes; same gates as the UI; human click required). KIEM_TRA.md mục 7: `--with-app` and one action clicked from Datadog.
- [ ] **Step 7: Run all checks, commit** — `feat(datadog): act on incidents from a Datadog App through a gated action API`

---

## Live verification (user, backup Datadog account) — after Task 6

Stop here and hand over:
1. `.env`: Datadog keys/site, `DD_TRACE_ENABLED=true`, `DD_METRICS_ENABLED=true`, `DD_RUM_APPLICATION_ID`/`DD_RUM_CLIENT_TOKEN`, `DATADOG_ACTION_SECRET`; `docker compose --profile observability up --build -d`.
2. `python scripts/setup_datadog.py --webhook-url https://…/api/v1/webhooks/datadog --with-app`; set `DD_MONITOR_ID`; restart worker.
3. One datadog-mode thermal_fault run: Datadog Incident created with each step mirrored; Notebook linked from Incident and UI; postmortem filled after resolve; SLO values move; RUM session shows `command_sent`; one command sent from the Datadog App and visible in the ChargeOps timeline with `actor: datadog-app`.
4. `docker compose exec api python -m chargeops.check_mongo` prints `$setWindowFields ok` and `postmortem $merge ok`.

## Self-review notes

- Spec coverage: B4 → Task 1; B5 → Task 2; B7 → Task 3; B6 → Task 4; B3 → Task 5; B8 → Task 6; error handling (retry/backoff, duplicate guard, 401/404/409/422/503) and testing sections → tests in each task; live checks → final section.
- Datadog endpoint details that can drift (incident timeline/todos, App Builder, connections) are verified from current docs in Task 1 Step 1 and Task 6 Step 4 and recorded in the reports.
- Names used across tasks: `DatadogClient` methods (1→2,3), `investigation_notebook`/`notebook_url` (2→3), `postmortem_pipeline`/`report_markdown` (3→4), `emit_charging_slo`/`emit_incident_outcome`/`slo_specs` (4), `allowed_commands`/`create_command_doc` (6).
