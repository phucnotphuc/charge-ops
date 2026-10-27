# ChargeOps — Competition Observability & Realism Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Before the 31/10/2026 23:59 ICT deadline, give operators and judges far richer views of the running system (incident signals and events on the existing role-based Datadog dashboards, extra monitors and investigation links, MongoDB window-function trends, a per-station monitor with a driver view showing battery %, power and connector temperature, plus an operator view) and make the live fleet behave closer to a real charging site (diurnal per-station ambient, sensor noise, imperfect telemetry).

**Architecture:** New signals live in new modules (`chargeops/observability.py`, `chargeops/insights.py`, `chargeops/vehicles.py`, `chargeops/station.py`, `chargeops/realism.py`) so the evaluation-fingerprinted core is untouched. The worker emits the extra Datadog signals only for `datadog`-mode runs; the API serves new aggregation and station endpoints; the web app gets per-station driver and operator screens fed by real Autosun SoC; realism is applied only inside the live simulator, behind a setting that defaults to off in tests.

**Tech Stack:** Python 3.12, FastAPI, PyMongo 4, MongoDB 8 (time series, `$setWindowFields`, `$facet`), DogStatsD (`datadog` package), ddtrace, Datadog REST API v1, React 18 + TypeScript + Vite + Recharts, Vitest, pytest + mongomock.

## Global Constraints

- Base branch: `feat/station-observability` (from `main` after PR #2, with `wp/final-fixes` merged; Task 0). Each task works on its own branch from the latest integration tip and is merged back with `--no-ff`. `main` also carries the teammate's role-based Datadog dashboards in `scripts/setup_datadog.py`; extend them, never replace them.
- **Never modify evaluation-fingerprinted files:** `chargeops/domain.py`, `chargeops/lifecycle.py`, `chargeops/model.py`, `chargeops/evaluation/*.py`. `python -m chargeops.evaluation run --protocol b1-eval-v3.1 --out <scratch dir>` must still be accepted at the end of every task.
- Metric tags only: `run_id`, `connector_id`, `station_id`, `env:hackathon`, plus `state:<incident state>` on `chargeops.incident.state_age_s`. No `incident_id`/`session_id` metric tags (events may carry `incident_id`). Hard cap of 20 live Datadog run IDs per trial stays in force.
- Extra Datadog signals are emitted only for runs with `mode == "datadog"`, except the worker heartbeat, which is gated by the new setting `DD_METRICS_ENABLED` (default `false`).
- Only the composite thermal monitor notifies `@webhook-chargeops-minimal`. New monitors must not mention the webhook.
- Zero paid services. Docs in Vietnamese matching existing style; code, comments and commits in English.
- Commit trailer on every commit: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (or the model that actually wrote it).
- Tests use mongomock. Anything needing real MongoDB is marked `@pytest.mark.mongo` and skipped unless `MONGODB_TEST_URI` is set.
- Never claim live verification in docs unless it was run; Task 8 is the only task that records live evidence.
- Tooling on this Windows host (Git Bash):
  - `UV="C:/Users/ADMIN/AppData/Local/Temp/claude/D--devops-charge-ops/72e9c51e-d7d6-41be-9f68-b8eea7f3c93e/scratchpad/uvenv/Scripts/uv"`
  - Tests: `"$UV" run pytest -q -p no:cacheprovider --basetemp .pytest-tmp`
  - Lint: `"$UV" run ruff check chargeops scripts tests`
  - Web: `cd web && npm ci && npm run build && npm test -- --run && npm run check:api`
- Schedule: Tasks 1–3 merged by **27/10**, so Task 8's live Datadog check runs on 27–28/10 before the trial ends (~29/10). Tasks 4–7 by 29/10. 30–31/10 is reserved for rehearsals, video and slides.

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `chargeops/observability.py` | Create | Worker heartbeat, incident open/age gauges, incident timeline → Datadog events (measurement gauges already exist in `worker.py`) |
| `chargeops/insights.py` | Create | MongoDB pipelines (`$setWindowFields` trend, `$facet` incident summary) and their response models |
| `chargeops/realism.py` | Create | Diurnal per-station ambient, seeded sensor noise, seeded telemetry imperfections |
| `chargeops/worker.py` | Modify | Call `observability` from `run_phases` |
| `chargeops/config.py` | Modify | `dd_metrics_enabled`, `sim_realism` settings |
| `chargeops/api.py` | Modify | `GET /api/v1/connectors/{id}/trend`, `GET /api/v1/insights/incidents` |
| `chargeops/fleet.py` | Modify | `physics()` takes the ambient temperature as an argument |
| `chargeops/simulator.py` | Modify | Use `realism` for ambient, noise and imperfections |
| `chargeops/check_mongo.py` | Modify | Run the trend pipeline against real MongoDB |
| `scripts/setup_datadog.py` | Modify | Add incident panels, event overlay, heartbeat widget, 3 extra monitors, investigation links; create dashboards before the composite |
| `compose.yaml`, `.env.example` | Modify | `DD_METRICS_ENABLED`, `SIM_REALISM` |
| `chargeops/vehicles.py`, `chargeops/station.py`, `chargeops/profiles/autosun_v3_soc.csv` | Create | Real Autosun start SoC/capacity, live SoC from delivered energy, public station models |
| `web/src/pages/StationDisplay.tsx`, `web/src/pages/StationOperator.tsx`, `web/src/components/ConnectorCard.tsx`, `web/src/components/Insights.tsx` | Create | Driver (kiosk) view and operator view per station |
| `web/src/api/client.ts`, `web/src/api/types.ts`, `web/src/components/Layout.tsx`, `web/src/App.tsx` | Modify | Client calls, types, nav link, route |
| `docs/OBSERVABILITY.md` | Create | Vietnamese guide to every dashboard group, monitor and investigation path |
| tests: `tests/test_observability.py`, `tests/test_setup_datadog.py`, `tests/test_insights.py`, `tests/test_vehicles.py`, `tests/test_station_api.py`, `tests/test_realism.py`, `web/src/test/station.test.tsx` | Create | One test file per new module |

---

### Task 0: Merge the reviewed final fixes — DONE 27/10

`wp/final-fixes` was merged onto `main` as branch `feat/station-observability` (commit `902e019`): conflicts resolved with main's command gate, plus the `maintenance_applied_at` condition on `verify_recovery`. 166 Python + 26 web tests pass; the v3.1 lock is accepted.

---

### Task 1: Datadog-mode incident signals: open count, state age, timeline events, heartbeat

**Context (27/10):** `main` already exports per-block gauges in Datadog mode (`chargeops.{current_a,current_limit_a,connector_temperature_c,expected_temperature_c,ambient_temperature_c}` via `GAUGE_FIELDS` in `worker.evaluate_window`) and `scripts/setup_datadog.py` provisions two role-based dashboards (station engineer, IT/SRE). This task adds only what is missing: incident signals and a worker heartbeat. Do not duplicate or rename the existing gauges.

**Files:**
- Create: `chargeops/observability.py`
- Modify: `chargeops/worker.py` (`run_phases` only), `chargeops/config.py`, `compose.yaml` (shared `x-env` anchor), `.env.example`
- Test: `tests/test_observability.py`, `tests/test_fleet.py`

**Interfaces:**
- Consumes: `incidents` documents (`_id`, `run_id`, `connector_id`, `state`, `state_since`, `unresolved`, `timeline[{at, event, reason?}]`; timeline event names include `opened`, `acknowledge`, `reduce_load`, `maintenance`, `verify_recovery`, `mitigated`, `verifying`, `resolved`, `recurrence`, `load_reduction_verified`, `retest_reset`, `run_stopped`); `domain.LOCATION`, `domain.CONNECTORS`.
- Produces:
  - `connector_tags(run_id: str, connector_id: str) -> list[str]`
  - `emit_heartbeat(stats) -> None` → `chargeops.worker.tick` with tags `["env:hackathon"]`
  - `emit_run_signals(db, stats, run: dict, at: datetime) -> None` → gauges `chargeops.incident.open` (per connector, 0 included) and `chargeops.incident.state_age_s` (per unresolved incident, extra tag `state:<state>`), plus one Datadog event per new timeline entry (tags include `incident_id:<id>`, `event:<name>`, `source:chargeops`)
  - Collection `dd_event_cursor` `{_id: incident_id, sent: int}`
  - Setting `dd_metrics_enabled: bool = False` (env `DD_METRICS_ENABLED`)

- [ ] **Step 1: Write the failing tests** `tests/test_observability.py`

```python
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import mongomock

from chargeops.observability import connector_tags, emit_heartbeat, emit_run_signals

T = datetime(2026, 10, 27, 3, 0, tzinfo=UTC)
RUN = "00000000-0000-4000-8000-0000000000a1"


def db_with(*incidents):
    db = mongomock.MongoClient(tz_aware=True).chargeops
    for incident in incidents:
        db.incidents.insert_one(incident)
    return db


def incident(**extra):
    base = {
        "_id": "i1",
        "run_id": RUN,
        "connector_id": "connector-2",
        "state": "mitigating",
        "state_since": T - timedelta(seconds=90),
        "unresolved": True,
        "timeline": [],
    }
    return {**base, **extra}


def test_connector_tags():
    assert connector_tags(RUN, "connector-4") == [
        f"run_id:{RUN}",
        "connector_id:connector-4",
        "station_id:station-2",
        "env:hackathon",
    ]


def test_heartbeat_is_env_tagged_only():
    stats = Mock()
    emit_heartbeat(stats)
    stats.increment.assert_called_once_with("chargeops.worker.tick", tags=["env:hackathon"])


def test_open_count_covers_every_connector_and_state_age_is_tagged():
    stats = Mock()
    emit_run_signals(db_with(incident()), stats, {"_id": RUN}, T)
    open_by = {
        c.kwargs["tags"][1]: c.args[1] for c in stats.gauge.call_args_list if c.args[0] == "chargeops.incident.open"
    }
    assert len(open_by) == 6
    assert open_by["connector_id:connector-2"] == 1 and open_by["connector_id:connector-5"] == 0
    age = [c for c in stats.gauge.call_args_list if c.args[0] == "chargeops.incident.state_age_s"]
    assert age[0].args[1] == 90.0
    assert "state:mitigating" in age[0].kwargs["tags"]


def test_resolved_incident_has_no_state_age():
    stats = Mock()
    emit_run_signals(db_with(incident(state="resolved", unresolved=None)), stats, {"_id": RUN}, T)
    assert not [c for c in stats.gauge.call_args_list if c.args[0] == "chargeops.incident.state_age_s"]


def test_other_runs_are_ignored():
    stats = Mock()
    emit_run_signals(db_with(incident(run_id="other")), stats, {"_id": RUN}, T)
    assert all(c.args[1] == 0 for c in stats.gauge.call_args_list if c.args[0] == "chargeops.incident.open")
    stats.event.assert_not_called()


def test_timeline_events_are_sent_exactly_once():
    db = db_with(incident(timeline=[{"at": T, "event": "opened"}, {"at": T, "event": "acknowledge"}]))
    stats = Mock()
    emit_run_signals(db, stats, {"_id": RUN}, T)
    emit_run_signals(db, stats, {"_id": RUN}, T)
    assert stats.event.call_count == 2
    assert stats.event.call_args_list[0].kwargs["alert_type"] == "error"
    db.incidents.update_one({"_id": "i1"}, {"$push": {"timeline": {"at": T, "event": "resolved"}}})
    emit_run_signals(db, stats, {"_id": RUN}, T)
    assert stats.event.call_count == 3
    last = stats.event.call_args
    assert last.kwargs["alert_type"] == "success"
    assert last.kwargs["aggregation_key"] == "i1"
    assert {"incident_id:i1", "event:resolved", "source:chargeops"} <= set(last.kwargs["tags"])
```

- [ ] **Step 2: Run to verify failure**

Run: `"$UV" run pytest tests/test_observability.py -q -p no:cacheprovider --basetemp .pytest-tmp`
Expected: FAIL with `ModuleNotFoundError: No module named 'chargeops.observability'`.

- [ ] **Step 3: Implement** `chargeops/observability.py`

```python
"""Datadog-mode incident signals (open count, time in state, one event per timeline entry) and a worker
heartbeat. The per-connector measurement gauges live in worker.evaluate_window (GAUGE_FIELDS).

Metric tags stay within run/connector/station/env (+ incident state on the age gauge); events may carry
incident_id because events are not custom-metric series."""

from chargeops.domain import CONNECTORS, LOCATION

ALERT_TYPE = {"opened": "error", "recurrence": "error", "mitigated": "warning", "resolved": "success"}


def connector_tags(run_id, connector_id):
    return [
        f"run_id:{run_id}",
        f"connector_id:{connector_id}",
        f"station_id:{LOCATION[connector_id][0]}",
        "env:hackathon",
    ]


def emit_heartbeat(stats):
    stats.increment("chargeops.worker.tick", tags=["env:hackathon"])


def emit_run_signals(db, stats, run, at):
    open_by_connector = dict.fromkeys(CONNECTORS, 0)
    for incident in db.incidents.find({"run_id": run["_id"]}):
        tags = connector_tags(run["_id"], incident["connector_id"])
        if incident.get("unresolved"):
            open_by_connector[incident["connector_id"]] += 1
            age = (at - incident["state_since"]).total_seconds()
            stats.gauge("chargeops.incident.state_age_s", age, tags=[*tags, f"state:{incident['state']}"])
        emit_timeline_events(db, stats, incident, tags)
    for connector_id, count in open_by_connector.items():
        stats.gauge("chargeops.incident.open", count, tags=connector_tags(run["_id"], connector_id))


def emit_timeline_events(db, stats, incident, tags):
    """At-least-once: the cursor advances after the events are handed to DogStatsD."""
    cursor = db.dd_event_cursor.find_one({"_id": incident["_id"]}) or {"sent": 0}
    timeline = incident.get("timeline", [])
    for entry in timeline[cursor["sent"] :]:
        stats.event(
            f"ChargeOps {entry['event']} · {incident['connector_id']}",
            f"Incident {incident['_id']} is {incident['state']}; reason {entry.get('reason') or '-'}",
            alert_type=ALERT_TYPE.get(entry["event"], "info"),
            aggregation_key=incident["_id"],
            source_type_name="chargeops",
            tags=[*tags, f"incident_id:{incident['_id']}", f"event:{entry['event']}", "source:chargeops"],
        )
    if len(timeline) > cursor["sent"]:
        db.dd_event_cursor.update_one({"_id": incident["_id"]}, {"$set": {"sent": len(timeline)}}, upsert=True)
```

- [ ] **Step 4: Run the new tests**

Run: `"$UV" run pytest tests/test_observability.py -q -p no:cacheprovider --basetemp .pytest-tmp`
Expected: `6 passed`.

- [ ] **Step 5: Setting and Compose.** `chargeops/config.py`, after `dd_trial_id`: `dd_metrics_enabled: bool = False`. `compose.yaml`, shared `x-env: &env` anchor next to `DD_TRACE_ENABLED`: `DD_METRICS_ENABLED: ${DD_METRICS_ENABLED:-false}`. `.env.example` after `DD_TRACE_ENABLED=false`:

```
# Set true with the observability profile: worker heartbeat metric for the liveness monitor.
DD_METRICS_ENABLED=false
```

- [ ] **Step 6: Wire into `run_phases`** (`chargeops/worker.py`). Import `from chargeops.observability import emit_heartbeat, emit_run_signals`. Replace

```python
    run = db.runs.find_one({"status": "active"})
    if run:
        evaluate_windows(db, stats, log, run, at)
```

with

```python
    if settings().dd_metrics_enabled:
        emit_heartbeat(stats)
    run = db.runs.find_one({"status": "active"})
    if run:
        evaluate_windows(db, stats, log, run, at)
        if run["mode"] == "datadog":
            with span("worker.signals", run_id=run["_id"]):
                emit_run_signals(db, stats, run, at)
```

If `run_phases` in `main` differs from this snippet, keep its structure and insert the two new blocks at the same points (heartbeat before the active-run lookup, signals right after `evaluate_windows`).

- [ ] **Step 7: Fleet tests.** In `tests/test_fleet.py`, in the existing Datadog-mode test that inspects `fleet.stats.gauge.call_args_list` (search for `station_id` tag assertions), append:

```python
    names = {call.args[0] for call in fleet.stats.gauge.call_args_list}
    assert "chargeops.incident.open" in names
```

and add:

```python
def test_local_mode_emits_no_incident_signals(store, monkeypatch):
    fleet = Harness(store, monkeypatch)
    fleet.start("healthy", seed=3, mode="local")
    fleet.run_until(120, worker=True)
    names = {call.args[0] for call in fleet.stats.gauge.call_args_list}
    assert "chargeops.incident.open" not in names
    fleet.stats.event.assert_not_called()
```

Use the real names of the harness helpers from the top of `tests/test_fleet.py` (`start`, the clock-advancing helper and its `worker=` flag).

- [ ] **Step 8: Full suite, lint, evaluation lock, compose config**

```bash
"$UV" run pytest -q -p no:cacheprovider --basetemp .pytest-tmp
"$UV" run ruff check chargeops scripts tests
"$UV" run python -m chargeops.evaluation run --protocol b1-eval-v3.1 --out .pytest-tmp/eval
SIMULATOR_SECRET=x WEBHOOK_SECRET=x OPERATOR_SECRET=x docker compose --profile observability config --quiet
```

Expected: all pass; the evaluation writes `120 runs`; compose exits 0.

- [ ] **Step 9: Commit**

```bash
git add chargeops/observability.py chargeops/worker.py chargeops/config.py compose.yaml .env.example tests/test_observability.py tests/test_fleet.py
git commit -m "feat(observability): incident open/age gauges, timeline events and worker heartbeat for Datadog runs"
```

---

### Task 2: Extend the existing dashboards: incident panels, event overlay, extra monitors, investigation links

**Context:** `scripts/setup_datadog.py` on `main` already builds `engineering_dashboard()` (station engineer) and `platform_dashboard()` (IT/SRE) with helpers `metric`, `log_count`, `residual_queries`, `timeseries(title, queries, formulas=None, display="line", markers=())`, `monitors`, `log_stream`, `dashboard`, constants `SCOPE`, `TEMPLATE_VARIABLES`, `COMMAND_LOGS`, and `monitor_specs()` (thermal, quality). Extend these; do not move them into a new module and do not rename existing widgets or state keys (dashboards are updated in place by id).

**Files:**
- Modify: `scripts/setup_datadog.py`
- Create: `docs/OBSERVABILITY.md`
- Test: `tests/test_setup_datadog.py`

**Interfaces:**
- Consumes: metric names from Task 1 (`chargeops.incident.open`, `chargeops.incident.state_age_s`, `chargeops.worker.tick`) and Datadog events tagged `source:chargeops`.
- Produces:
  - `timeseries(..., events=False)` → adds `"events": [{"q": "tags:source:chargeops"}]` when true
  - `incident_widgets() -> list[dict]` appended to the engineering dashboard
  - `heartbeat_widget() -> dict` appended to the platform dashboard
  - `extra_monitor_specs(engineering_url: str) -> dict[str, dict]` with keys `stale`, `worker`, `stuck`
  - `composite_message(engineering_url: str, site: str) -> str`
  - `dashboard_url(site: str, dashboard_id: str) -> str`

- [ ] **Step 1: Write the failing tests** `tests/test_setup_datadog.py`

```python
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("setup_datadog", Path(__file__).parents[1] / "scripts" / "setup_datadog.py")
sd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sd)

URL = "https://app.ap1.datadoghq.com/dashboard/abc-def-ghi"


def titles(board):
    return [w["definition"]["title"] for w in board["widgets"]]


def test_engineering_dashboard_gains_incident_panels_and_keeps_existing_ones():
    board = sd.engineering_dashboard()
    names = titles(board)
    assert "Residual nhiệt (°C, trung bình theo mẫu)" in names  # existing widget unchanged
    assert "Sự cố đang mở theo connector" in names
    assert "Thời gian ở trạng thái hiện tại (s)" in names


def test_residual_and_current_charts_overlay_incident_events():
    board = {w["definition"]["title"]: w["definition"] for w in sd.engineering_dashboard()["widgets"]}
    assert board["Residual nhiệt (°C, trung bình theo mẫu)"]["events"] == [{"q": "tags:source:chargeops"}]
    assert board["Dòng sạc và giới hạn dòng (A) — giảm tải hiện ở đây"]["events"]


def test_incident_queries_use_template_scope():
    for widget in sd.incident_widgets():
        for request in widget["requests"]:
            for query in request["queries"]:
                assert sd.SCOPE in query["query"]


def test_platform_dashboard_has_heartbeat():
    assert "Worker heartbeat (tick / phút)" in titles(sd.platform_dashboard())


def test_extra_monitors_never_call_the_webhook():
    specs = sd.extra_monitor_specs(URL)
    assert set(specs) == {"stale", "worker", "stuck"}
    for spec in specs.values():
        assert "@webhook" not in spec["message"]
    assert URL in specs["stale"]["message"] and URL in specs["stuck"]["message"]
    assert specs["worker"]["options"]["notify_no_data"] is True
    assert "state:mitigating" in specs["stuck"]["query"]


def test_composite_message_links_dashboard_and_logs_and_keeps_webhook():
    message = sd.composite_message(URL, "ap1.datadoghq.com")
    assert "@webhook-chargeops-minimal" in message
    assert URL + "?tpl_var_run_id={{run_id.name}}&tpl_var_connector_id={{connector_id.name}}" in message
    assert "https://app.ap1.datadoghq.com/logs?query=" in message


def test_dashboard_url():
    assert sd.dashboard_url("datadoghq.com", "x") == "https://app.datadoghq.com/dashboard/x"
```

- [ ] **Step 2: Run to verify failure**

Run: `"$UV" run pytest tests/test_setup_datadog.py -q -p no:cacheprovider --basetemp .pytest-tmp`
Expected: FAIL (`AttributeError: module 'setup_datadog' has no attribute 'incident_widgets'`, or a KeyError on `events`).

- [ ] **Step 3: Implement in `scripts/setup_datadog.py`**

Add `from urllib.parse import quote` to the imports. Change `timeseries` to accept the overlay:

```python
def timeseries(title, queries, formulas=None, display="line", markers=(), events=False):
    widget = {
        "type": "timeseries",
        "title": title,
        "show_legend": True,
        "requests": [
            {
                "queries": queries,
                "formulas": [{"formula": f} for f in formulas or [q["name"] for q in queries]],
                "response_format": "timeseries",
                "display_type": display,
            }
        ],
        "markers": [
            {"value": f"y = {y}", "display_type": kind, "label": label} for y, kind, label in markers
        ],
    }
    if events:
        widget["events"] = [{"q": "tags:source:chargeops"}]
    return widget
```

In `engineering_dashboard()`, pass `events=True` to the residual timeseries (`"Residual nhiệt (°C, trung bình theo mẫu)"`) and to `"Dòng sạc và giới hạn dòng (A) — giảm tải hiện ở đây"`, and insert `*incident_widgets(),` directly before the final `log_stream(...)` entry. In `platform_dashboard()`, append `heartbeat_widget()` to its widget list. Add:

```python
def incident_widgets():
    return [
        timeseries(
            "Sự cố đang mở theo connector",
            [metric("open", f"sum:chargeops.incident.open{SCOPE} by {{connector_id}}")],
            display="bars",
            events=True,
        ),
        timeseries(
            "Thời gian ở trạng thái hiện tại (s)",
            [metric("age", f"max:chargeops.incident.state_age_s{SCOPE} by {{connector_id,state}}")],
            markers=[(600, "warning dashed", "giảm tải quá 10 phút")],
        ),
    ]


def heartbeat_widget():
    return timeseries(
        "Worker heartbeat (tick / phút)",
        [metric("tick", "sum:chargeops.worker.tick{env:hackathon}.as_rate()")],
        ["tick * 60"],
    )


def dashboard_url(site, dashboard_id):
    return f"https://app.{site}/dashboard/{dashboard_id}"


def investigate(url):
    return f"Điều tra: {url}?tpl_var_run_id={{{{run_id.name}}}}&tpl_var_connector_id={{{{connector_id.name}}}}"


def composite_message(engineering_url, site):
    logs = f"https://app.{site}/logs?query=" + quote("service:chargeops @run_id:{{run_id.name}}")
    return "\n".join(
        ["ChargeOps thermal anomaly. @webhook-chargeops-minimal", investigate(engineering_url), f"Logs: {logs}"]
    )


def extra_monitor_specs(engineering_url):
    return {
        "stale": {
            "name": "ChargeOps telemetry stale",
            "type": "query alert",
            "query": "sum(last_2m):sum:chargeops.samples{env:hackathon} by {run_id,connector_id}.as_count() < 12",
            "message": "Connector {{connector_id.name}} gửi dưới 12 mẫu trong 2 phút. " + investigate(engineering_url),
            "options": {"thresholds": {"critical": 12}, "notify_no_data": False, "require_full_window": True},
        },
        "worker": {
            "name": "ChargeOps worker heartbeat",
            "type": "query alert",
            "query": "sum(last_2m):sum:chargeops.worker.tick{env:hackathon}.as_count() < 1",
            "message": "Worker không gửi heartbeat trong 2 phút: kiểm tra `docker compose logs worker`.",
            "options": {
                "thresholds": {"critical": 1},
                "notify_no_data": True,
                "no_data_timeframe": 3,
                "require_full_window": False,
            },
        },
        "stuck": {
            "name": "ChargeOps derating not resolving",
            "type": "query alert",
            "query": "max(last_5m):max:chargeops.incident.state_age_s{env:hackathon,state:mitigating} by {run_id,connector_id} > 600",
            "message": "Connector {{connector_id.name}} ở trạng thái giảm tải quá 10 phút. " + investigate(engineering_url),
            "options": {"thresholds": {"critical": 600}, "notify_no_data": False, "require_full_window": False},
        },
    }
```

The composite's existing `name`, `type`, `query` and `options` stay the same; only its `message` becomes `composite_message(...)`.

- [ ] **Step 4: Reorder `main()` so dashboards exist before the composite and extra monitors link to them.** Read `main()`. The current order is: webhook → thermal/quality monitors → composite → dashboards. Change it to: webhook → thermal/quality monitors → **dashboards (unchanged loop and state keys)** → extra monitors (`extra_monitor_specs(dashboard_url(site, state[<engineering dashboard state key>]))`, each validated with `/monitor/validate`, then upserted with new state keys `stale`, `worker`, `stuck`) → composite with `"message": composite_message(dashboard_url(site, state[<engineering key>]), site)`. Use the existing state key for the engineering dashboard (it reuses the original `dashboard` id per the teammate's commit). Keep every existing print line and add `print("Extra monitors: stale, worker heartbeat, derating not resolving (no webhook).")`.

- [ ] **Step 5: Run the tests**

Run: `"$UV" run pytest tests/test_setup_datadog.py -q -p no:cacheprovider --basetemp .pytest-tmp`
Expected: `7 passed`.

- [ ] **Step 6: Write `docs/OBSERVABILITY.md`** (Vietnamese), in this order:
  1. Which signals exist in local vs datadog mode; `DD_METRICS_ENABLED=true` enables the heartbeat.
  2. Table of both dashboards (engineering, IT/SRE): each widget title, metric/log query behind it, the question it answers.
  3. Table of all monitors (thermal, quality, composite, stale, worker, stuck): query, threshold, webhook yes/no (only composite).
  4. "Lộ trình điều tra một sự cố": composite alert → link to the engineering dashboard filtered by run/connector → residual with 8/3 °C markers and the event overlay → current vs limit → command log stream → logs link → APM trace from a log's `dd.trace_id` on the IT/SRE dashboard.
  5. "Chưa kiểm chứng thật": event overlay query `tags:source:chargeops`, template-variable links in composite messages, `as_rate()` heartbeat widget, `no_data` behaviour of the worker monitor. These are verified in Task 8.

- [ ] **Step 7: Full suite and lint** (same commands as Task 1 Step 8).

- [ ] **Step 8: Commit**

```bash
git add scripts/setup_datadog.py docs/OBSERVABILITY.md tests/test_setup_datadog.py
git commit -m "feat(datadog): incident panels, event overlay, extra monitors and investigation links on the role dashboards"
```

---
### Task 3: MongoDB insights: `$setWindowFields` trend and `$facet` incident summary

**Files:**
- Create: `chargeops/insights.py`
- Modify: `chargeops/api.py` (two routes)
- Modify: `chargeops/check_mongo.py` (run the trend pipeline on real MongoDB)
- Modify: `pyproject.toml` (register the `mongo` marker)
- Test: `tests/test_insights.py`

**Interfaces:**
- Consumes: `windows` documents `{_id, run_id, connector_id, end, quality, residual_mean, count}`; `incidents` documents with `state`, `connector_id`, `run_id`, `unresolved`, `opened_at`, `timeline[{at, event}]` (event names `opened`, `acknowledge`, `mitigated`, `resolved`).
- Produces:
  - `trend_pipeline(run_id: str, connector_id: str, limit: int = 240) -> list[dict]`
  - `fleet_pipeline(run_id: str) -> list[dict]` (latest window per connector)
  - `incident_summary_pipeline(run_id: str | None) -> list[dict]`
  - `shape_summary(doc: dict) -> IncidentSummary`
  - Models `TrendPoint`, `TrendOut`, `FleetWindow`, `FleetOut`, `StateCount`, `ConnectorCount`, `IncidentSummary`
  - Routes `GET /api/v1/connectors/{connector_id}/trend?run_id=` → `TrendOut`, `GET /api/v1/insights/fleet?run_id=` → `FleetOut`, `GET /api/v1/insights/incidents?run_id=` → `IncidentSummary`
  - Why a fleet route: `ConnectorOut.latest` is a raw sample without `residual`, and `domain.py` (where `ConnectorOut` lives) is fingerprinted, so the residual per connector comes from the latest `windows` document instead.

- [ ] **Step 1: Write the failing tests** in `tests/test_insights.py`

```python
import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from chargeops.api import app
from chargeops.insights import fleet_pipeline, incident_summary_pipeline, shape_summary, trend_pipeline

RUN = "00000000-0000-4000-8000-0000000000b1"
T0 = datetime(2026, 10, 25, 3, 0, tzinfo=UTC)


def test_trend_pipeline_uses_window_functions_on_quality_windows():
    pipeline = trend_pipeline(RUN, "connector-3")
    assert pipeline[0] == {"$match": {"run_id": RUN, "connector_id": "connector-3", "quality": True}}
    stage = next(s["$setWindowFields"] for s in pipeline if "$setWindowFields" in s)
    assert stage["sortBy"] == {"end": 1}
    out = stage["output"]
    assert out["residual_avg_5m"]["window"] == {"range": [-300, 0], "unit": "second"}
    assert out["residual_slope_c_per_min"]["$derivative"] == {"input": "$residual_mean", "unit": "minute"}
    assert pipeline[-1] == {"$sort": {"end": 1}}


def test_fleet_pipeline_takes_latest_window_per_connector(store):
    for i, residual in enumerate([1.0, 2.0, 9.5]):
        store.windows.insert_one(
            {"_id": f"w{i}", "run_id": RUN, "connector_id": "connector-4", "end": T0 + timedelta(seconds=15 * i), "quality": True, "residual_mean": residual}
        )
    store.windows.insert_one(
        {"_id": "w9", "run_id": RUN, "connector_id": "connector-1", "end": T0, "quality": False, "residual_mean": None}
    )
    rows = {r["connector_id"]: r for r in store.windows.aggregate(fleet_pipeline(RUN))}
    assert rows["connector-4"]["residual_mean"] == 9.5
    assert rows["connector-1"]["quality"] is False and rows["connector-1"]["residual_mean"] is None


def test_fleet_route(store):
    store.windows.insert_one(
        {"_id": "w1", "run_id": RUN, "connector_id": "connector-2", "end": T0, "quality": True, "residual_mean": 4.0}
    )
    body = TestClient(app).get(f"/api/v1/insights/fleet?run_id={RUN}").json()
    assert body["windows"] == [{"connector_id": "connector-2", "end": "2026-10-25T03:00:00Z", "quality": True, "residual_mean": 4.0}]


def test_summary_pipeline_facets():
    facet = incident_summary_pipeline(RUN)[1]["$facet"]
    assert set(facet) == {"by_state", "by_connector", "durations"}
    assert incident_summary_pipeline(None)[0] == {"$match": {}}


def test_shape_summary_fills_missing_values_with_none():
    doc = {"by_state": [{"_id": "open", "count": 2}], "by_connector": [], "durations": []}
    summary = shape_summary(doc)
    assert summary.by_state[0].state == "open"
    assert summary.mtta_s is None and summary.mttr_s is None and summary.total == 2


def test_trend_route_shapes_rows(store, monkeypatch):
    rows = [{"end": T0, "residual_mean": 1.0, "residual_avg_5m": 1.0, "residual_slope_c_per_min": None, "residual_max_15m": 1.0}]
    monkeypatch.setattr(type(store.windows), "aggregate", lambda self, pipeline: iter(rows))
    response = TestClient(app).get(f"/api/v1/connectors/connector-3/trend?run_id={RUN}")
    assert response.status_code == 200
    body = response.json()
    assert body["connector_id"] == "connector-3"
    assert body["points"][0]["end"].endswith("Z")
    assert body["points"][0]["residual_slope_c_per_min"] is None


def test_trend_route_rejects_unknown_connector(store):
    response = TestClient(app).get(f"/api/v1/connectors/connector-9/trend?run_id={RUN}")
    assert response.status_code == 422


def test_incident_summary_route(store, monkeypatch):
    doc = {
        "by_state": [{"_id": "resolved", "count": 1}],
        "by_connector": [{"_id": "connector-4", "count": 1, "open": 0}],
        "durations": [{"_id": None, "mtta_s": 30.0, "mttm_s": 160.0, "mttr_s": 840.0}],
    }
    monkeypatch.setattr(type(store.incidents), "aggregate", lambda self, pipeline: iter([doc]))
    body = TestClient(app).get(f"/api/v1/insights/incidents?run_id={RUN}").json()
    assert body["total"] == 1 and body["mttr_s"] == 840.0
    assert body["by_connector"] == [{"connector_id": "connector-4", "count": 1, "open": 0}]


@pytest.mark.mongo
@pytest.mark.skipif(not os.getenv("MONGODB_TEST_URI"), reason="needs a real MongoDB (MONGODB_TEST_URI)")
def test_trend_pipeline_on_real_mongo():
    from pymongo import MongoClient

    db = MongoClient(os.environ["MONGODB_TEST_URI"], tz_aware=True).chargeops_insights_test
    db.windows.drop()
    db.windows.insert_many(
        [
            {"run_id": RUN, "connector_id": "connector-3", "end": T0 + timedelta(seconds=15 * i), "quality": True, "residual_mean": float(i)}
            for i in range(12)
        ]
    )
    rows = list(db.windows.aggregate(trend_pipeline(RUN, "connector-3")))
    assert len(rows) == 12
    assert rows[-1]["residual_slope_c_per_min"] == pytest.approx(4.0)  # +1 °C per 15 s
    assert rows[-1]["residual_max_15m"] == 11.0
    db.windows.drop()
```

- [ ] **Step 2: Register the marker** in `pyproject.toml` under `[tool.pytest.ini_options]`:

```toml
markers = ["mongo: needs a real MongoDB via MONGODB_TEST_URI"]
```

- [ ] **Step 3: Run to verify failure**

Run: `"$UV" run pytest tests/test_insights.py -q -p no:cacheprovider --basetemp .pytest-tmp`
Expected: FAIL with `ModuleNotFoundError: No module named 'chargeops.insights'`.

- [ ] **Step 4: Implement** `chargeops/insights.py`

```python
"""Read-only MongoDB analytics for operators: window-function residual trend per connector and an incident
summary (counts by state/connector, MTTA/MTTM/MTTR) computed in one $facet aggregation."""

from datetime import datetime

from pydantic import BaseModel


def trend_pipeline(run_id, connector_id, limit=240):
    """Newest `limit` quality windows (15 s apart → 60 min), oldest first, with moving average, slope and max."""
    return [
        {"$match": {"run_id": run_id, "connector_id": connector_id, "quality": True}},
        {"$sort": {"end": -1}},
        {"$limit": limit},
        {
            "$setWindowFields": {
                "partitionBy": "$connector_id",
                "sortBy": {"end": 1},
                "output": {
                    "residual_avg_5m": {
                        "$avg": "$residual_mean",
                        "window": {"range": [-300, 0], "unit": "second"},
                    },
                    "residual_slope_c_per_min": {
                        "$derivative": {"input": "$residual_mean", "unit": "minute"},
                        "window": {"range": [-120, 0], "unit": "second"},
                    },
                    "residual_max_15m": {
                        "$max": "$residual_mean",
                        "window": {"range": [-900, 0], "unit": "second"},
                    },
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "end": 1,
                "residual_mean": 1,
                "residual_avg_5m": 1,
                "residual_slope_c_per_min": 1,
                "residual_max_15m": 1,
            }
        },
        {"$sort": {"end": 1}},
    ]


def fleet_pipeline(run_id):
    """Latest 60-second window of every connector in the run (quality and residual mean), one row per connector."""
    return [
        {"$match": {"run_id": run_id}},
        {"$sort": {"end": -1}},
        {
            "$group": {
                "_id": "$connector_id",
                "end": {"$first": "$end"},
                "quality": {"$first": "$quality"},
                "residual_mean": {"$first": "$residual_mean"},
            }
        },
        {"$project": {"_id": 0, "connector_id": "$_id", "end": 1, "quality": 1, "residual_mean": 1}},
        {"$sort": {"connector_id": 1}},
    ]


def first_event_at(name):
    return {
        "$first": {
            "$map": {
                "input": {"$filter": {"input": "$timeline", "cond": {"$eq": ["$$this.event", name]}}},
                "in": "$$this.at",
            }
        }
    }


def seconds_between(start, end):
    return {"$dateDiff": {"startDate": start, "endDate": end, "unit": "second"}}


def incident_summary_pipeline(run_id):
    return [
        {"$match": {"run_id": run_id} if run_id else {}},
        {
            "$facet": {
                "by_state": [{"$group": {"_id": "$state", "count": {"$sum": 1}}}, {"$sort": {"_id": 1}}],
                "by_connector": [
                    {
                        "$group": {
                            "_id": "$connector_id",
                            "count": {"$sum": 1},
                            "open": {"$sum": {"$cond": [{"$eq": ["$unresolved", True]}, 1, 0]}},
                        }
                    },
                    {"$sort": {"_id": 1}},
                ],
                "durations": [
                    {
                        "$project": {
                            "opened_at": 1,
                            "ack_at": first_event_at("acknowledge"),
                            "mitigated_at": first_event_at("mitigated"),
                            "resolved_at": first_event_at("resolved"),
                        }
                    },
                    {
                        "$group": {
                            "_id": None,
                            "mtta_s": {"$avg": seconds_between("$opened_at", "$ack_at")},
                            "mttm_s": {"$avg": seconds_between("$opened_at", "$mitigated_at")},
                            "mttr_s": {"$avg": seconds_between("$opened_at", "$resolved_at")},
                        }
                    },
                ],
            }
        },
    ]


class TrendPoint(BaseModel):
    end: datetime
    residual_mean: float | None
    residual_avg_5m: float | None
    residual_slope_c_per_min: float | None
    residual_max_15m: float | None


class TrendOut(BaseModel):
    run_id: str
    connector_id: str
    points: list[TrendPoint]


class FleetWindow(BaseModel):
    connector_id: str
    end: datetime
    quality: bool
    residual_mean: float | None


class FleetOut(BaseModel):
    run_id: str
    windows: list[FleetWindow]


class StateCount(BaseModel):
    state: str
    count: int


class ConnectorCount(BaseModel):
    connector_id: str
    count: int
    open: int


class IncidentSummary(BaseModel):
    total: int
    by_state: list[StateCount]
    by_connector: list[ConnectorCount]
    mtta_s: float | None
    mttm_s: float | None
    mttr_s: float | None


def shape_summary(doc):
    durations = (doc.get("durations") or [{}])[0]
    by_state = [StateCount(state=r["_id"], count=r["count"]) for r in doc.get("by_state", [])]
    return IncidentSummary(
        total=sum(s.count for s in by_state),
        by_state=by_state,
        by_connector=[ConnectorCount(connector_id=r["_id"], count=r["count"], open=r["open"]) for r in doc.get("by_connector", [])],
        mtta_s=durations.get("mtta_s"),
        mttm_s=durations.get("mttm_s"),
        mttr_s=durations.get("mttr_s"),
    )
```

- [ ] **Step 5: Add the routes** in `chargeops/api.py`. Add to the imports:

```python
from chargeops import insights
```

Add after the existing `GET /api/v1/connectors/{connector_id}/telemetry` route. `response_model` routes already serialise aware datetimes with `Z` (checked in the final review), so return the models directly:

```python
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
```

`ConnectorId` is already imported in `api.py` from `chargeops.domain`; if not, add it to that import (do not edit `domain.py`).

- [ ] **Step 6: Run the tests**

Run: `"$UV" run pytest tests/test_insights.py -q -p no:cacheprovider --basetemp .pytest-tmp`
Expected: `8 passed, 1 skipped`. (`fleet_pipeline` uses only `$match/$sort/$group/$first/$project`, which mongomock supports; if mongomock rejects `$first` inside `$group`, keep the pipeline and switch that test to the monkeypatched-aggregate style used for the trend route.)

- [ ] **Step 7: Real-MongoDB check.** In `chargeops/check_mongo.py`, at the end of the real-MongoDB path (after windows are written for `connector-1`), add:

```python
    from chargeops.insights import trend_pipeline

    trend = list(db.windows.aggregate(trend_pipeline(run_id, "connector-1")))
    print(f"trend points: {len(trend)}; $setWindowFields ok")
```

The real-MongoDB check runs later, by the user, inside the stack (MongoDB is not published on the host): `docker compose exec api python -m chargeops.check_mongo` must print `$setWindowFields ok`. Task 8 records that output. The `@pytest.mark.mongo` test stays skipped in normal runs; record "real-Mongo pytest not run" in the report unless `MONGODB_TEST_URI` was available.

- [ ] **Step 8: Regenerate API types** (route shapes changed):

```bash
"$UV" run python scripts/export_openapi.py
cd web && npm run gen:api && npm run check:api; cd ..
```

Expected: `check:api` exits 0 after committing the regenerated files.

- [ ] **Step 9: Full suite, lint, evaluation lock** (same as Task 1 Step 8).

- [ ] **Step 10: Commit**

```bash
git add chargeops/insights.py chargeops/api.py chargeops/check_mongo.py pyproject.toml tests/test_insights.py web/openapi.json web/src/api/schema.d.ts
git commit -m "feat(mongo): \$setWindowFields residual trend and \$facet incident summary endpoints"
```

---

### Task 4: Vehicle state from real Autosun SoC (backend for the station monitor)

**Files:**
- Modify: `scripts/audit_autosun.py` (export a per-session SoC/capacity table)
- Create (generated, committed): `chargeops/profiles/autosun_v3_soc.csv`
- Create: `chargeops/vehicles.py`, `chargeops/station.py`
- Modify: `chargeops/simulator.py` (track delivered energy, post vehicle states), `chargeops/api.py` (`POST /api/v1/simulator/vehicles`, `GET /api/v1/stations/{station_id}`, `last_vehicles` in `/simulator/run?resume=true`), `chargeops/storage.py` (index), `docs/CONTRACT.md` §2, `docs/DATA_CARD.md`, `PLAN.md` (scope line)
- Test: `tests/test_vehicles.py`, `tests/test_station_api.py`, `tests/test_fleet.py`

**Why a separate collection:** SoC is vehicle state, not connector telemetry, and adding a field to `Sample` would change the fingerprinted `domain.py` and force an evaluation re-lock. Vehicle states go to a new `vehicle_states` collection (latest state per run and connector), with their models in `chargeops/station.py`. `chargeops/profiles/autosun_v3.csv` must stay byte-identical (its SHA-256 is part of the evaluation fingerprint).

**SoC model (documented in DATA_CARD):** for each replayed Autosun session the export stores the real `current_soc` at the first and last published minute and the session energy `Σ out_power / 60` (kWh). Usable capacity is `energy_kwh / ((soc_end - soc_start) / 100)` when the SoC rise is ≥ 5 points, otherwise `null` and the live model uses 60 kWh (synthetic assumption, marked as such). Live SoC = `soc_start + delivered_kwh / capacity_kwh × 100`, capped at 100, where `delivered_kwh` integrates the **actual** current the simulator delivers. A derated connector therefore charges visibly slower, which is the trade-off the station screen must show honestly.

**Interfaces:**
- Consumes: `fleet_plan(seed, scenario, profile)` → `Plan.profile_id`; `Simulator.samples()` per-connector `current_a`, `voltage_v`; the existing function in `api.py` that builds `ConnectorOut` for `GET /api/v1/connectors` (read `api.py` to find its name; reuse it, do not duplicate the latest-state pipeline).
- Produces:
  - CSV `chargeops/profiles/autosun_v3_soc.csv`: `transaction_id,soc_start_percent,soc_end_percent,energy_kwh,capacity_kwh` (one row per replayed session; empty `capacity_kwh` when unknown)
  - `vehicles.SOC_PROFILES: dict[str, SocProfile]`, `SocProfile(soc_start_percent, soc_end_percent, energy_kwh, capacity_kwh)`, `DEFAULT_CAPACITY_KWH = 60.0`
  - `vehicles.soc_percent(profile: SocProfile, delivered_kwh: float) -> float`
  - `vehicles.minutes_to_full(profile: SocProfile, soc: float, power_kw: float) -> float | None`
  - `station.VehicleState` (input), `station.StationConnector`, `station.StationOut`, `station.public_status(connector: dict, vehicle: dict | None) -> str` with values `offline | idle | charging | limited | checking`
  - Routes: `POST /api/v1/simulator/vehicles` (simulator secret, body `{"vehicles": [VehicleState, ...]}`), `GET /api/v1/stations/{station_id}` → `StationOut` (anonymous, read-only)

- [ ] **Step 1: Export the SoC table.** In `scripts/audit_autosun.py` `main()`, directly after `profiles.to_csv(PROFILES, ...)`, add:

```python
    # Station monitor: real SoC at session start/end and session energy for the replayed sessions.
    replayed = data[data["transaction_id"].isin(chosen.index)]
    soc = replayed.groupby("transaction_id").agg(
        soc_start_percent=("current_soc", "first"),
        soc_end_percent=("current_soc", "last"),
        energy_kwh=("out_power", lambda p: p.sum() / 60),
    )
    rise = soc["soc_end_percent"] - soc["soc_start_percent"]
    soc["capacity_kwh"] = (soc["energy_kwh"] / (rise / 100)).where(rise >= 5).round(1)
    soc["energy_kwh"] = soc["energy_kwh"].round(3)
    soc.reset_index().to_csv(SOC_PROFILES, index=False, lineterminator="\n")
```

with `SOC_PROFILES = Path("chargeops/profiles/autosun_v3_soc.csv")` next to `PROFILES`, and add `"soc_file": {"path": str(SOC_PROFILES.as_posix()), "rows": len(soc), "sha256": sha256(SOC_PROFILES)}` inside `manifest["selection"]`. Change the manifest's `"use"` line to `"load profiles (charginga/chargingv) and per-session SoC/energy for the station display; battery label is not charger ground truth"`.

- [ ] **Step 2: Re-run the audit and prove the replay file did not change** (needs network; downloads ~the published XLSX into git-ignored `data/raw/`):

```bash
cd /d/devops/charge-ops   # or your task worktree
sha256sum chargeops/profiles/autosun_v3.csv > .pytest-tmp/replay.sha
"$UV" run --with pandas==2.2.3 --with openpyxl==3.1.5 python -I scripts/audit_autosun.py
sha256sum -c .pytest-tmp/replay.sha
head -3 chargeops/profiles/autosun_v3_soc.csv
```

Expected: the audit prints the same `eligible sessions` count as in `docs/DATA_CARD.md`; `sha256sum -c` prints `chargeops/profiles/autosun_v3.csv: OK`; the SoC file has a header plus 50 rows. **If the replay file changed, stop**: restore it with `git checkout chargeops/profiles/autosun_v3.csv` and report, because the evaluation lock depends on it. If the download fails, report BLOCKED (do not fabricate SoC values).

- [ ] **Step 3: Failing tests** `tests/test_vehicles.py`

```python
import pytest

from chargeops.model import PROFILES
from chargeops.vehicles import DEFAULT_CAPACITY_KWH, SOC_PROFILES, SocProfile, minutes_to_full, soc_percent

P = SocProfile(soc_start_percent=30.0, soc_end_percent=80.0, energy_kwh=30.0, capacity_kwh=60.0)


def test_every_replayed_session_has_a_soc_row():
    assert set(SOC_PROFILES) == set(PROFILES)
    assert all(0 <= p.soc_start_percent <= 100 for p in SOC_PROFILES.values())


def test_soc_rises_with_delivered_energy_and_caps_at_100():
    assert soc_percent(P, 0.0) == 30.0
    assert soc_percent(P, 6.0) == pytest.approx(40.0)
    assert soc_percent(P, 1000.0) == 100.0


def test_unknown_capacity_uses_documented_default():
    p = SocProfile(soc_start_percent=50.0, soc_end_percent=52.0, energy_kwh=1.0, capacity_kwh=None)
    assert soc_percent(p, DEFAULT_CAPACITY_KWH / 10) == pytest.approx(60.0)


def test_minutes_to_full_from_remaining_energy_and_power():
    assert minutes_to_full(P, 40.0, 36.0) == pytest.approx(60.0)  # 36 kWh left at 36 kW
    assert minutes_to_full(P, 40.0, 0.2) is None  # not charging
    assert minutes_to_full(P, 100.0, 36.0) == 0.0
```

- [ ] **Step 4: Implement** `chargeops/vehicles.py`

```python
"""Vehicle state for the station display: real start SoC and usable capacity per replayed Autosun session
(chargeops/profiles/autosun_v3_soc.csv), advanced by the energy the connector actually delivers."""

import csv
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CAPACITY_KWH = 60.0  # synthetic fallback when a session's SoC rise is too small to estimate capacity
CHARGING_KW = 0.5


@dataclass(frozen=True)
class SocProfile:
    soc_start_percent: float
    soc_end_percent: float
    energy_kwh: float
    capacity_kwh: float | None


def load_soc_profiles():
    path = Path(__file__).parent / "profiles" / "autosun_v3_soc.csv"
    with path.open(encoding="utf-8") as f:
        return {
            row["transaction_id"]: SocProfile(
                float(row["soc_start_percent"]),
                float(row["soc_end_percent"]),
                float(row["energy_kwh"]),
                float(row["capacity_kwh"]) if row["capacity_kwh"] else None,
            )
            for row in csv.DictReader(f)
        }


SOC_PROFILES = load_soc_profiles()


def capacity(profile):
    return profile.capacity_kwh or DEFAULT_CAPACITY_KWH


def soc_percent(profile, delivered_kwh):
    return min(100.0, profile.soc_start_percent + delivered_kwh / capacity(profile) * 100)


def minutes_to_full(profile, soc, power_kw):
    if soc >= 100:
        return 0.0
    if power_kw < CHARGING_KW:
        return None
    return (100 - soc) / 100 * capacity(profile) / power_kw * 60
```

- [ ] **Step 5: Station models and status mapping** `chargeops/station.py`

```python
"""Public station display: what a driver needs (status, SoC, power, current, connector temperature, energy, time
to full) and nothing operational (no residual, incident ids, commands or thresholds)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from chargeops.domain import ConnectorId

PublicStatus = Literal["offline", "idle", "charging", "limited", "checking"]


class VehicleState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    connector_id: ConnectorId
    measured_at: datetime
    soc_percent: float = Field(ge=0, le=100)
    energy_kwh: float = Field(ge=0)
    minutes_to_full: float | None = Field(default=None, ge=0)
    capacity_source: Literal["autosun", "default"]


class VehicleBatch(BaseModel):
    vehicles: list[VehicleState] = Field(min_length=1, max_length=6)


class StationConnector(BaseModel):
    connector_id: str
    status: PublicStatus
    soc_percent: float | None
    power_kw: float | None
    current_a: float | None
    connector_temperature_c: float | None
    energy_kwh: float | None
    minutes_to_full: float | None
    measured_at: datetime | None


class StationOut(BaseModel):
    station_id: str
    connectors: list[StationConnector]


def public_status(connector, vehicle):
    """`connector` is the ConnectorOut dict from GET /connectors; `vehicle` the latest vehicle state or None."""
    latest = connector.get("latest") or {}
    if connector.get("stale") or not latest:
        return "offline"
    state = (connector.get("active_incident") or {}).get("state")
    if state == "verifying":
        return "checking"
    if state in ("mitigating", "mitigated") or latest.get("current_limit_a", 0) < latest.get("requested_current_a", 0):
        return "limited" if latest.get("current_a", 0) >= 1 else "checking"
    if latest.get("current_a", 0) < 1 or vehicle is None:
        return "idle"
    return "charging"
```

- [ ] **Step 6: API routes.** In `chargeops/api.py` add `from chargeops import station` and `from chargeops.domain import LOCATION` (if not already imported). Add:

```python
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
    connectors = [c for c in CONNECTOR_STATES(store) if LOCATION[c["connector_id"]][0] == station_id]
    out = []
    for c in connectors:
        vehicle = store.vehicle_states.find_one({"_id": f"{c.get('run_id')}:{c['connector_id']}"}) if c.get("run_id") else None
        latest = c.get("latest") or {}
        status = station.public_status(c, vehicle)
        out.append(
            station.StationConnector(
                connector_id=c["connector_id"],
                status=status,
                soc_percent=vehicle and vehicle["soc_percent"],
                power_kw=latest.get("power_kw"),
                current_a=latest.get("current_a"),
                connector_temperature_c=latest.get("connector_temperature_c"),
                energy_kwh=vehicle and vehicle["energy_kwh"],
                minutes_to_full=vehicle and vehicle.get("minutes_to_full"),
                measured_at=latest.get("measured_at"),
            )
        )
    return station.StationOut(station_id=station_id, connectors=out)
```

Replace `CONNECTOR_STATES(store)` with the real function that `GET /api/v1/connectors` uses to build its items (as plain dicts; call `.model_dump()` if it returns models). In the `/simulator/run` handler, where `last_samples` is added for `resume=true`, also add `last_vehicles = {doc["connector_id"]: doc for doc in store.vehicle_states.find({"run_id": run["_id"]}, {"_id": 0})}`. In `storage.initialize()` add `db.vehicle_states.create_index("run_id")`.

- [ ] **Step 7: Simulator posts vehicle states.** In `chargeops/simulator.py`:

```python
from chargeops.vehicles import SOC_PROFILES, minutes_to_full, soc_percent
```

In `__init__`: `self.delivered = {}` (per run: `{connector_id: (kwh, last_measured)}`). In `samples()`, when the thermal state is reset for a new `run_id`, seed `self.delivered` from `run.get("last_vehicles")` (`energy_kwh`, `measured_at`) or `(0.0, measured)`. After computing each connector's actual `current` and `voltage`, integrate:

```python
            kwh, last = self.delivered[cid]
            kwh += voltage * current / 1000 * max((measured - last).total_seconds(), 0) / 3600
            self.delivered[cid] = (kwh, measured)
```

Add a method that builds the batch from the same step (uses the noise-free current, so SoC is not noisy):

```python
    def vehicles(self, run, plans, measured):
        out = []
        for cid in CONNECTORS:
            profile = SOC_PROFILES[plans[cid].profile_id]
            kwh, _ = self.delivered[cid]
            soc = soc_percent(profile, kwh)
            power = self.last_power.get(cid, 0.0)
            out.append(
                {
                    "run_id": run["_id"],
                    "connector_id": cid,
                    "measured_at": measured.isoformat(),
                    "soc_percent": round(soc, 1),
                    "energy_kwh": round(kwh, 3),
                    "minutes_to_full": minutes_to_full(profile, soc, power),
                    "capacity_source": "autosun" if profile.capacity_kwh else "default",
                }
            )
        return out
```

with `self.last_power[cid] = voltage * current / 1000` stored in the loop. In `step()`, after a successful telemetry post, post `{"vehicles": self.vehicles(run, plans, measured)}` to `/api/v1/simulator/vehicles` (keep `plans` and `measured` from the `samples()` call; return them or store them on `self`). A failed vehicle post logs `vehicle_post_retry` and does not block telemetry.

- [ ] **Step 8: Failing-first API tests** `tests/test_station_api.py`

```python
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from chargeops.api import app
from chargeops.station import public_status

SIM = {"X-Simulator-Secret": "s" * 32}
RUN = "00000000-0000-4000-8000-0000000000e1"


def connector(**extra):
    base = {"connector_id": "connector-1", "run_id": RUN, "stale": False, "active_incident": None,
            "latest": {"current_a": 120.0, "requested_current_a": 120.0, "current_limit_a": 250.0, "power_kw": 48.0, "connector_temperature_c": 41.0}}
    return {**base, **extra}


def test_public_status_mapping():
    assert public_status(connector(stale=True), None) == "offline"
    assert public_status(connector(), {"soc_percent": 40}) == "charging"
    assert public_status(connector(active_incident={"id": "i", "state": "mitigating"}), {"soc_percent": 40}) == "limited"
    assert public_status(connector(active_incident={"id": "i", "state": "verifying"}), {"soc_percent": 40}) == "checking"
    idle = connector(latest={"current_a": 0.0, "requested_current_a": 0.0, "current_limit_a": 250.0})
    assert public_status(idle, None) == "idle"


def test_vehicle_ingest_needs_simulator_secret(store):
    body = {"vehicles": [{"run_id": RUN, "connector_id": "connector-1", "measured_at": "2026-10-25T03:00:00Z",
                          "soc_percent": 41.5, "energy_kwh": 6.9, "minutes_to_full": 70.0, "capacity_source": "autosun"}]}
    assert TestClient(app).post("/api/v1/simulator/vehicles", json=body).status_code == 401
    assert TestClient(app, headers=SIM).post("/api/v1/simulator/vehicles", json=body).status_code == 202
    assert store.vehicle_states.find_one({"_id": f"{RUN}:connector-1"})["soc_percent"] == 41.5


def test_station_display_hides_operational_fields(store, monkeypatch):
    # Uses the fleet harness pattern: start a healthy run, run 60 s with the simulator so telemetry and
    # vehicle states exist, then read the public station endpoint anonymously.
    from tests.test_fleet import Harness

    fleet = Harness(store, monkeypatch)
    fleet.start("healthy", seed=3)
    fleet.run_until(60, worker=True)
    body = TestClient(app).get("/api/v1/stations/station-2").json()
    assert [c["connector_id"] for c in body["connectors"]] == ["connector-4", "connector-5", "connector-6"]
    text = str(body)
    for forbidden in ("residual", "incident", "command", "threshold", "run_id"):
        assert forbidden not in text
    assert all(c["soc_percent"] is not None for c in body["connectors"])


def test_unknown_station_is_422(store):
    assert TestClient(app).get("/api/v1/stations/station-9").status_code == 422
```

(Use the real harness helper names from `tests/test_fleet.py`; if importing `Harness` from a test module is awkward, move `Harness` into `tests/conftest.py` as a fixture in this task.)

- [ ] **Step 9: Fleet test: derating slows SoC.** In `tests/test_fleet.py` add a test that runs `thermal_fault` seed 4 to `mitigating` (reuse the existing full-loop helper), records the faulty connector's `soc_percent` rise over 120 s before and after the reduce_load command applied, and asserts the rise after derating is smaller. This is the behaviour the station screen explains to drivers.

- [ ] **Step 10: Docs.** CONTRACT §2: add both routes ("Công khai local, chỉ đọc" for the station route) and the `vehicle_states` collection. DATA_CARD: SoC fields are `public` (start SoC, session energy) plus `derived` (live SoC from delivered energy) and `synthetic` (60 kWh fallback), with the formula above. PLAN.md: replace "Không thanh toán, ứng dụng tài xế, …" with "Không thanh toán, tài khoản/ứng dụng tài xế, …; có màn hình trạm chỉ đọc (pin %, công suất, nhiệt độ) cho người dùng tại trạm."

- [ ] **Step 11: Full suite, lint, evaluation lock, API types**

```bash
"$UV" run pytest -q -p no:cacheprovider --basetemp .pytest-tmp
"$UV" run ruff check chargeops scripts tests
"$UV" run python -m chargeops.evaluation run --protocol b1-eval-v3.1 --out .pytest-tmp/eval
"$UV" run python scripts/export_openapi.py && (cd web && npm run gen:api && npm run check:api)
```

Expected: all pass; the evaluation lock is accepted (the replay CSV and fingerprinted files are unchanged).

- [ ] **Step 12: Commit**

```bash
git add scripts/audit_autosun.py data/manifests/autosun-v3.json chargeops/profiles/autosun_v3_soc.csv chargeops/vehicles.py chargeops/station.py chargeops/simulator.py chargeops/api.py chargeops/storage.py docs/CONTRACT.md docs/DATA_CARD.md PLAN.md tests web/openapi.json web/src/api/schema.d.ts
git commit -m "feat(station): vehicle SoC from real Autosun sessions and a public station endpoint"
```

---

### Task 5: Station monitor screens: driver view and operator view

**Files:**
- Create: `web/src/pages/StationDisplay.tsx` (driver/kiosk view), `web/src/pages/StationOperator.tsx` (operator view), `web/src/components/ConnectorCard.tsx` (shared card)
- Modify: `web/src/App.tsx` (routes), `web/src/components/Layout.tsx` (nav "Trạm"), `web/src/pages/Overview.tsx` (links to both stations), `web/src/api/client.ts`, `web/src/api/types.ts`, `web/src/lib/format.ts` (labels), stylesheet
- Modify: `chargeops/insights.py` + `chargeops/api.py` (optional `station_id` filter on `/insights/incidents`)
- Test: `web/src/test/station.test.tsx`, `tests/test_insights.py`

**Interfaces:**
- Consumes: `GET /api/v1/stations/{station_id}` (Task 4), `GET /api/v1/insights/fleet?run_id=`, `GET /api/v1/insights/incidents?run_id=&station_id=`, `GET /api/v1/connectors/{id}/trend?run_id=` (Task 3), `GET /api/v1/connectors` (run id, active incident ids for links).
- Produces: routes `/stations/:stationId` (outside `Layout`, no login, large text, 5 s refresh) and `/stations/:stationId/operator` (inside `Layout`); `api.station(id)`; `PUBLIC_STATUS_LABEL`; `fmtEta(minutes)`.

**Screen content (Vietnamese):**

| | Driver view `/stations/station-1` | Operator view `/stations/station-1/operator` |
|---|---|---|
| Per connector | Status in words, **pin %** with a progress bar, công suất kW, dòng A, nhiệt độ đầu sạc °C, đã sạc kWh, "Đầy sau khoảng …" | Everything in the driver view **plus** residual (from `/insights/fleet`), incident state badge linking to `/incidents/{id}`, trend chart for the selected connector |
| Status words | `idle` "Trống", `charging` "Đang sạc", `limited` "Đang sạc chậm để bảo vệ đầu sạc", `checking` "Đang kiểm tra sau bảo trì", `offline` "Tạm mất kết nối" | Same words plus the technical state |
| Station level | Station name, clock (Asia/Ho_Chi_Minh), "Cập nhật lúc …" | Incident funnel and MTTA/MTTR for this station's connectors |
| Never shown | residual, incident ids, commands, thresholds, run ids | — |

- [ ] **Step 1: Station filter for the incident summary.** In `chargeops/insights.py`, change the signature to `incident_summary_pipeline(run_id, connector_ids=None)` and the match to

```python
    match = {"run_id": run_id} if run_id else {}
    if connector_ids:
        match["connector_id"] = {"$in": list(connector_ids)}
    return [{"$match": match}, ...]
```

and in `api.py` `incident_insights(run_id: str | None = None, station_id: Literal["station-1", "station-2"] | None = None, ...)` pass `[c for c, (s, _) in LOCATION.items() if s == station_id]` when `station_id` is given. Add a test in `tests/test_insights.py`: `incident_summary_pipeline("r", ["connector-4"])[0] == {"$match": {"run_id": "r", "connector_id": {"$in": ["connector-4"]}}}`.

- [ ] **Step 2: Client, types, labels.** `types.ts`: `export type StationOut = components["schemas"]["StationOut"]; export type StationConnector = components["schemas"]["StationConnector"];`. `client.ts`: `station: (id: string) => request<StationOut>(`/stations/${encodeURIComponent(id)}`),` and `incidentSummary` gains an optional `stationId` query parameter. `format.ts`:

```ts
export const PUBLIC_STATUS_LABEL: Record<string, string> = {
  idle: "Trống",
  charging: "Đang sạc",
  limited: "Đang sạc chậm để bảo vệ đầu sạc",
  checking: "Đang kiểm tra sau bảo trì",
  offline: "Tạm mất kết nối",
};

export function fmtEta(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined || !Number.isFinite(minutes)) return "—";
  if (minutes <= 0) return "Đã đầy";
  const m = Math.round(minutes);
  return m >= 60 ? `khoảng ${Math.floor(m / 60)} giờ ${m % 60} phút` : `khoảng ${m} phút`;
}
```

- [ ] **Step 3: Failing tests** `web/src/test/station.test.tsx`

```tsx
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { StationDisplay } from "../pages/StationDisplay";
import { StationOperator } from "../pages/StationOperator";
import { fmtEta } from "../lib/format";

const STATION = {
  station_id: "station-2",
  connectors: [
    { connector_id: "connector-4", status: "limited", soc_percent: 47.3, power_kw: 12.1, current_a: 30, connector_temperature_c: 52.4, energy_kwh: 9.8, minutes_to_full: 140, measured_at: "2026-10-25T03:00:00Z" },
    { connector_id: "connector-5", status: "charging", soc_percent: 63, power_kw: 48, current_a: 120, connector_temperature_c: 41, energy_kwh: 15.2, minutes_to_full: 28, measured_at: "2026-10-25T03:00:00Z" },
    { connector_id: "connector-6", status: "idle", soc_percent: null, power_kw: 0, current_a: 0, connector_temperature_c: 30, energy_kwh: null, minutes_to_full: null, measured_at: "2026-10-25T03:00:00Z" },
  ],
};

function mock(routes: Record<string, unknown>) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const key = Object.keys(routes).find((k) => url.includes(k));
    return key
      ? new Response(JSON.stringify(routes[key]), { status: 200, headers: { "Content-Type": "application/json" } })
      : new Response(JSON.stringify({ error: { code: "not_found", message: "x", request_id: null } }), { status: 404 });
  }));
}

afterEach(() => vi.unstubAllGlobals());

const at = (path: string, el: JSX.Element, pattern: string) =>
  render(<MemoryRouter initialEntries={[path]}><Routes><Route path={pattern} element={el} /></Routes></MemoryRouter>);

test("driver view shows battery, power, temperature and friendly status only", async () => {
  mock({ "/stations/station-2": STATION });
  at("/stations/station-2", <StationDisplay />, "/stations/:stationId");
  const card = await screen.findByTestId("card-connector-4");
  expect(within(card).getByText("Đang sạc chậm để bảo vệ đầu sạc")).toBeInTheDocument();
  expect(within(card).getByText("47%")).toBeInTheDocument();
  expect(within(card).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "47.3");
  expect(within(card).getByText(/52[.,]4 °C/)).toBeInTheDocument();
  expect(within(screen.getByTestId("card-connector-6")).getByText("Trống")).toBeInTheDocument();
  const page = document.body.textContent ?? "";
  for (const word of ["Residual", "residual", "Sự cố", "incident", "Idempotency"]) expect(page).not.toContain(word);
});

test("operator view adds residual and incident link", async () => {
  mock({
    "/stations/station-2": STATION,
    "/insights/fleet": { run_id: "r1", windows: [{ connector_id: "connector-4", end: "2026-10-25T03:00:00Z", quality: true, residual_mean: 9.1 }] },
    "/insights/incidents": { total: 1, by_state: [{ state: "mitigating", count: 1 }], by_connector: [], mtta_s: 30, mttm_s: null, mttr_s: null },
    "/connectors/connector-4/trend": { run_id: "r1", connector_id: "connector-4", points: [] },
    "/connectors": { items: [{ connector_id: "connector-4", station_id: "station-2", charger_id: "charger-2", run_id: "r1", run_status: "active", mode: "local", measured_at: "2026-10-25T03:00:00Z", freshness_s: 3, stale: false, active_incident: { id: "i1", state: "mitigating" }, latest: null }], next_cursor: null },
  });
  at("/stations/station-2/operator", <StationOperator />, "/stations/:stationId/operator");
  const card = await screen.findByTestId("card-connector-4");
  expect(await within(card).findByText(/9[.,]1 °C/)).toBeInTheDocument();
  expect(within(card).getByRole("link", { name: /đang giảm tải/i })).toHaveAttribute("href", "/incidents/i1");
});

test("eta formatting", () => {
  expect(fmtEta(28)).toBe("khoảng 28 phút");
  expect(fmtEta(140)).toBe("khoảng 2 giờ 20 phút");
  expect(fmtEta(0)).toBe("Đã đầy");
  expect(fmtEta(null)).toBe("—");
});
```

`STATE_LABEL.mitigating` is "Đang giảm tải" in `web/src/lib/format.ts`.

- [ ] **Step 4: Shared card** `web/src/components/ConnectorCard.tsx`

```tsx
import type { ReactNode } from "react";
import type { StationConnector } from "../api/types";
import { PUBLIC_STATUS_LABEL, fmtEta, fmtNum } from "../lib/format";

export function ConnectorCard({ c, extra }: { c: StationConnector; extra?: ReactNode }) {
  const soc = c.soc_percent;
  return (
    <article className={`station-card status-${c.status}`} data-testid={`card-${c.connector_id}`}>
      <header>
        <h2>{c.connector_id.replace("connector-", "Đầu sạc ")}</h2>
        <span className="status">{PUBLIC_STATUS_LABEL[c.status] ?? c.status}</span>
      </header>
      {soc !== null && soc !== undefined ? (
        <div className="soc">
          <strong>{Math.round(soc)}%</strong>
          <div role="progressbar" aria-label="Pin xe" aria-valuemin={0} aria-valuemax={100} aria-valuenow={soc}>
            <div className="soc-fill" style={{ width: `${soc}%` }} />
          </div>
        </div>
      ) : (
        <div className="soc muted">Chưa có xe</div>
      )}
      <dl className="station-kv">
        <dt>Công suất</dt>
        <dd>{fmtNum(c.power_kw, "kW")}</dd>
        <dt>Dòng điện</dt>
        <dd>{fmtNum(c.current_a, "A", 0)}</dd>
        <dt>Nhiệt độ đầu sạc</dt>
        <dd>{fmtNum(c.connector_temperature_c, "°C")}</dd>
        <dt>Đã sạc</dt>
        <dd>{fmtNum(c.energy_kwh, "kWh")}</dd>
        <dt>Đầy sau</dt>
        <dd>{fmtEta(c.minutes_to_full)}</dd>
      </dl>
      {extra}
    </article>
  );
}
```

(`fmtNum` already renders null as "—" and uses Vietnamese decimals; keep it.)

- [ ] **Step 5: Driver view** `web/src/pages/StationDisplay.tsx`

```tsx
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import { usePolling } from "../api/hooks";
import { Async } from "../components/Async";
import { ConnectorCard } from "../components/ConnectorCard";
import { fmtTime } from "../lib/format";

const NAMES: Record<string, string> = { "station-1": "Trạm sạc số 1", "station-2": "Trạm sạc số 2" };

export function StationDisplay() {
  const { stationId = "station-1" } = useParams();
  const polled = usePolling(() => api.station(stationId), [stationId]);
  return (
    <main className="kiosk">
      <h1>{NAMES[stationId] ?? stationId}</h1>
      <Async polled={polled}>
        {(s) => (
          <>
            <div className="station-grid">
              {s.connectors.map((c) => (
                <ConnectorCard key={c.connector_id} c={c} />
              ))}
            </div>
            <p className="muted small">
              Cập nhật lúc {fmtTime(s.connectors[0]?.measured_at)} · Dữ liệu demo: tải từ phiên sạc thật (Autosun), nhiệt
              độ đầu sạc mô phỏng.
            </p>
          </>
        )}
      </Async>
    </main>
  );
}
```

- [ ] **Step 6: Shared insight blocks** `web/src/components/Insights.tsx`

```tsx
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client";
import { usePolling } from "../api/hooks";
import { Async } from "./Async";
import { STATE_LABEL, fmtTime } from "../lib/format";

const FUNNEL = ["open", "acknowledged", "mitigating", "mitigated", "verifying", "resolved"] as const;

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "—";
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)} phút ${s % 60} giây`;
}

export function Summary({ runId, stationId }: { runId: string | null; stationId?: string }) {
  const polled = usePolling(() => api.incidentSummary(runId, stationId), [runId, stationId]);
  return (
    <Async polled={polled} isEmpty={(s) => s.total === 0} empty="Chưa có sự cố trong run này.">
      {(s) => (
        <section className="card">
          <h2>Sự cố</h2>
          <div className="funnel">
            {FUNNEL.map((state) => (
              <div key={state} data-testid={`funnel-${state}`}>
                <span className="muted small">{STATE_LABEL[state] ?? state}</span>
                <strong>{s.by_state.find((x) => x.state === state)?.count ?? 0}</strong>
              </div>
            ))}
          </div>
          <dl className="kv">
            <dt>Thời gian tới xác nhận (MTTA)</dt>
            <dd>{fmtDuration(s.mtta_s)}</dd>
            <dt>Thời gian tới giảm tải có tác dụng</dt>
            <dd>{fmtDuration(s.mttm_s)}</dd>
            <dt>Thời gian tới phục hồi (MTTR)</dt>
            <dd>{fmtDuration(s.mttr_s)}</dd>
          </dl>
        </section>
      )}
    </Async>
  );
}

export function Trend({ connectorId, runId }: { connectorId: string; runId: string }) {
  const polled = usePolling(() => api.connectorTrend(connectorId, runId), [connectorId, runId]);
  return (
    <Async polled={polled} isEmpty={(t) => t.points.length === 0} empty="Chưa có cửa sổ hợp lệ.">
      {(t) => (
        <section className="card">
          <h2>Xu hướng residual · {connectorId}</h2>
          <p className="muted small">Tính bằng MongoDB $setWindowFields trên các cửa sổ 60 s hợp lệ.</p>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={t.points.map((p) => ({ ...p, at: fmtTime(p.end) }))}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="at" minTickGap={40} />
              <YAxis unit=" °C" />
              <Tooltip />
              <ReferenceLine y={8} stroke="var(--danger)" strokeDasharray="4 4" />
              <ReferenceLine y={3} stroke="var(--ok)" strokeDasharray="4 4" />
              <Line dataKey="residual_mean" name="Residual 60 s" dot={false} />
              <Line dataKey="residual_avg_5m" name="Trung bình 5 phút" dot={false} strokeDasharray="5 3" />
            </LineChart>
          </ResponsiveContainer>
        </section>
      )}
    </Async>
  );
}
```

In `client.ts`, `incidentSummary` becomes:

```ts
  incidentSummary: (runId: string | null, stationId?: string) => {
    const query = new URLSearchParams({ ...(runId ? { run_id: runId } : {}), ...(stationId ? { station_id: stationId } : {}) });
    return request<IncidentSummary>(`/insights/incidents${query.size ? `?${query}` : ""}`);
  },
```

- [ ] **Step 6b: Operator view** `web/src/pages/StationOperator.tsx`

```tsx
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { usePolling } from "../api/hooks";
import { Async } from "../components/Async";
import { ConnectorCard } from "../components/ConnectorCard";
import { Summary, Trend } from "../components/Insights";
import { STATE_LABEL, fmtNum } from "../lib/format";

function Residual({ runId, connectorId }: { runId: string; connectorId: string }) {
  const fleet = usePolling(() => api.fleetInsights(runId), [runId]);
  const w = fleet.data?.windows.find((x) => x.connector_id === connectorId);
  return <p>Residual nhiệt: {fmtNum(w && w.quality ? w.residual_mean : null, "°C")}</p>;
}

export function StationOperator() {
  const { stationId = "station-1" } = useParams();
  const station = usePolling(() => api.station(stationId), [stationId]);
  const connectors = usePolling(() => api.connectors(), []);
  const [picked, setPicked] = useState<string | null>(null);
  const items = connectors.data?.items ?? [];
  const runId = items.find((c) => c.run_id)?.run_id ?? null;
  const incidents = Object.fromEntries(items.map((c) => [c.connector_id, c.active_incident]));
  return (
    <div className="page">
      <h1>Vận hành · {stationId}</h1>
      <p>
        <Link to={`/stations/${stationId}`}>Mở màn hình trạm cho người dùng</Link>
      </p>
      <Async polled={station}>
        {(s) => {
          const focus = picked ?? s.connectors[0]?.connector_id ?? null;
          return (
            <>
              <div className="station-grid">
                {s.connectors.map((c) => {
                  const incident = incidents[c.connector_id];
                  return (
                    <ConnectorCard
                      key={c.connector_id}
                      c={c}
                      extra={
                        <>
                          {runId && <Residual runId={runId} connectorId={c.connector_id} />}
                          {incident && (
                            <Link to={`/incidents/${incident.id}`}>{STATE_LABEL[incident.state] ?? incident.state}</Link>
                          )}
                          <button type="button" className="btn ghost" onClick={() => setPicked(c.connector_id)}>
                            Xem xu hướng
                          </button>
                        </>
                      }
                    />
                  );
                })}
              </div>
              {runId && focus && <Trend connectorId={focus} runId={runId} />}
              <Summary runId={runId} stationId={stationId} />
            </>
          );
        }}
      </Async>
    </div>
  );
}
```

`Residual` polls `/insights/fleet` once per card; that is 3 requests every 5 s per station page and acceptable for the demo. If the reviewer flags it, lift the fleet poll into `StationOperator` and pass the value down.

- [ ] **Step 7: Routes, nav, overview.** `App.tsx`: add `<Route path="/stations/:stationId" element={<StationDisplay />} />` **outside** the `Layout` route (kiosk has no nav or login button), and `<Route path="stations/:stationId/operator" element={<StationOperator />} />` inside it. `Layout.tsx`: nav entry `{ to: "/stations/station-1/operator", label: "Trạm", icon: Building2, end: false }`. `Overview.tsx`: a line per station with links "Màn hình trạm" (`/stations/station-1`) and "Vận hành" (`/stations/station-1/operator`). Stylesheet:

```css
.kiosk { max-width: 1200px; margin: 0 auto; padding: 24px; font-size: 1.25rem; }
.station-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; }
.station-card { border: 1px solid var(--border); border-radius: 12px; padding: 16px; display: flex; flex-direction: column; gap: 12px; }
.station-card header { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
.station-card.status-limited { border-left: 8px solid var(--warn); }
.station-card.status-charging { border-left: 8px solid var(--ok); }
.station-card.status-offline { opacity: 0.6; }
.soc strong { font-size: 2.5rem; }
[role="progressbar"] { height: 14px; border-radius: 7px; background: var(--border); overflow: hidden; }
.soc-fill { height: 100%; background: var(--ok); transition: width 1s; }
.station-kv { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; margin: 0; }
```

Use the stylesheet's real colour variables if these names differ.

- [ ] **Step 8: Web checks** — `cd web && npm run build && npm test -- --run && npm run check:api` → all pass, including the 3 new tests.

- [ ] **Step 9: Commit**

```bash
git add web/src chargeops/insights.py chargeops/api.py tests/test_insights.py web/openapi.json web/src/api/schema.d.ts
git commit -m "feat(web): per-station monitor with a driver view (battery %, power, temperature) and an operator view"
```

---
### Task 6: Realistic environment: diurnal per-station ambient and sensor noise

**Files:**
- Create: `chargeops/realism.py`
- Modify: `chargeops/fleet.py` (`physics` signature), `chargeops/simulator.py` (`Simulator.__init__`, `samples`), `chargeops/config.py`, `compose.yaml`, `.env.example`
- Modify: `chargeops/storage.py` (manifest text for `ambient_temperature_c`)
- Test: `tests/test_realism.py`, `tests/test_fleet.py` (call sites of `physics`)

**Interfaces:**
- Consumes: `fleet.physics(plan, elapsed, sim)` (today), `domain.LOCATION`, `domain.AMBIENT_C`.
- Produces:
  - `STATION_AMBIENT: dict[str, tuple[float, float]]` (min °C at 05:00 ICT, max °C at 14:00 ICT)
  - `ambient_c(station_id: str, at: datetime) -> float`
  - `noisy(value: float, sigma: float, rng: random.Random) -> float`
  - `sample_rng(run_id: str, connector_id: str, step: int) -> random.Random`
  - `fleet.physics(plan, elapsed, sim, ambient_c=AMBIENT_C)` (new optional argument, default keeps old behaviour)
  - `Simulator(client, log=None, realism=False)`; setting `sim_realism: bool = False`

- [ ] **Step 1: Write the failing tests** `tests/test_realism.py`

```python
import random
from datetime import UTC, datetime

from chargeops.domain import AMBIENT_C, LOCATION
from chargeops.fleet import fleet_plan, physics
from chargeops.realism import STATION_AMBIENT, ambient_c, noisy, sample_rng


def at_ict(hour):
    return datetime(2026, 10, 25, (hour - 7) % 24, 0, tzinfo=UTC)


def test_ambient_peaks_at_14_and_bottoms_at_05_ict():
    low, high = STATION_AMBIENT["station-1"]
    assert ambient_c("station-1", at_ict(14)) == high
    assert ambient_c("station-1", at_ict(5)) == low
    assert low < ambient_c("station-1", at_ict(10)) < high


def test_station_2_is_warmer():
    assert ambient_c("station-2", at_ict(14)) > ambient_c("station-1", at_ict(14))


def test_ambient_changes_slowly_within_a_demo_run():
    start = at_ict(10)
    later = start.replace(minute=30)
    assert abs(ambient_c("station-1", later) - ambient_c("station-1", start)) < 2.0


def test_noise_is_seeded_and_small():
    values = [noisy(40.0, 0.2, sample_rng("r", "connector-1", i)) for i in range(500)]
    assert values == [noisy(40.0, 0.2, sample_rng("r", "connector-1", i)) for i in range(500)]
    assert abs(sum(values) / len(values) - 40.0) < 0.05
    assert max(abs(v - 40.0) for v in values) < 1.0


def test_physics_default_ambient_is_unchanged():
    plan = fleet_plan(4, "healthy")["connector-1"]
    assert physics(plan, 60, {}) == physics(plan, 60, {}, ambient_c=AMBIENT_C)


def test_physics_steady_temperature_follows_ambient():
    plan = fleet_plan(4, "healthy")["connector-1"]
    cool = physics(plan, 60, {}, ambient_c=27.0)[-1]
    hot = physics(plan, 60, {}, ambient_c=35.0)[-1]
    assert round(hot - cool, 6) == 8.0


def test_every_station_has_a_profile():
    assert {station for station, _ in LOCATION.values()} == set(STATION_AMBIENT)


def test_noisy_never_negative_for_currents():
    rng = random.Random(1)
    assert all(noisy(0.0, 0.5, rng, floor=0.0) >= 0.0 for _ in range(100))
```

- [ ] **Step 2: Run to verify failure**

Run: `"$UV" run pytest tests/test_realism.py -q -p no:cacheprovider --basetemp .pytest-tmp`
Expected: FAIL with `ModuleNotFoundError: No module named 'chargeops.realism'`.

- [ ] **Step 3: Implement** `chargeops/realism.py`

```python
"""Live-simulator realism (synthetic, documented in the provenance manifest): Ho Chi Minh City diurnal ambient
per station and seeded sensor noise. The detector baseline already reads `ambient_temperature_c` from each
sample, so a moving ambient is handled without changing the detector."""

import hashlib
import math
import random
from datetime import timedelta

ICT = timedelta(hours=7)
# (min at 05:00 ICT, max at 14:00 ICT), °C. station-2 is a sun-exposed rooftop site, ~1.5 °C warmer.
STATION_AMBIENT = {"station-1": (26.0, 34.0), "station-2": (27.5, 35.5)}
LOW_HOUR, HIGH_HOUR = 5.0, 14.0


def ambient_c(station_id, at):
    """Piecewise cosine: rises 05:00→14:00 ICT, falls 14:00→05:00 the next day."""
    low, high = STATION_AMBIENT[station_id]
    local = at + ICT
    hour = local.hour + local.minute / 60 + local.second / 3600
    if LOW_HOUR <= hour <= HIGH_HOUR:
        phase = (hour - LOW_HOUR) / (HIGH_HOUR - LOW_HOUR)  # 0 → 1 rising
        fraction = (1 - math.cos(math.pi * phase)) / 2
    else:
        span = 24 - (HIGH_HOUR - LOW_HOUR)
        phase = ((hour - HIGH_HOUR) % 24) / span  # 0 → 1 falling
        fraction = (1 + math.cos(math.pi * phase)) / 2
    return low + (high - low) * fraction


def sample_rng(run_id, connector_id, step):
    """Independent, reproducible stream per (run, connector, 5-second step); restarts give the same noise."""
    digest = hashlib.sha256(f"{run_id}:{connector_id}:{step}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def noisy(value, sigma, rng, floor=None):
    out = value + rng.gauss(0.0, sigma)
    return max(out, floor) if floor is not None else out
```

- [ ] **Step 4: Let `physics` take the ambient.** In `chargeops/fleet.py`, change the signature and the last line of `physics`:

```python
def physics(plan, elapsed, sim, ambient_c=AMBIENT_C):
```

```python
    return demand, current, limit, voltage, ambient_c + THERMAL_K * current**2 * (1 + heat)
```

Add one sentence to its docstring: "`ambient_c` is the station's ambient at this step; the default keeps the constant 25 °C demo ambient."

- [ ] **Step 5: Use realism in the simulator.** In `chargeops/simulator.py`:

Imports:

```python
from chargeops.domain import AMBIENT_C, CONNECTORS, LOCATION, lag, now
from chargeops.realism import ambient_c, noisy, sample_rng
```

Constructor:

```python
    def __init__(self, client, log=None, realism=False):
        self.client, self.log = client, log or logger("simulator")
        self.realism = realism
        self.pending = None  # unsent batch, retried unchanged (same sample_ids) until the API answers
        self.thermal = {}
```

In `samples()`, the cold-start default `(AMBIENT_C, measured)` becomes `(self.ambient(cid, measured), measured)`. Inside the per-connector loop replace the physics call and the sample dict with:

```python
            ambient = self.ambient(cid, measured)
            demand, current, limit, voltage, steady = physics(plans[cid], elapsed, sim, ambient)
            temperature, at = self.thermal[cid]
            temperature = lag(temperature, steady, (measured - at).total_seconds())
            self.thermal[cid] = (temperature, measured)
            rng = sample_rng(run["_id"], cid, step)
            reading = noisy(temperature, 0.2, rng) if self.realism else temperature
            measured_current = noisy(current, 0.005 * current, rng, floor=0.0) if self.realism else current
            out.append(
                {
                    "sample_id": str(uuid5(UUID(run["_id"]), f"{cid}:{step}")),
                    "run_id": run["_id"],
                    "connector_id": cid,
                    "measured_at": measured.isoformat(),
                    "current_a": measured_current,
                    "voltage_v": voltage,
                    "power_kw": voltage * measured_current / 1000,
                    "requested_current_a": demand,
                    "current_limit_a": limit,
                    "connector_temperature_c": reading,
                    "ambient_temperature_c": ambient,
                }
            )
```

The thermal state (`self.thermal`) keeps the true temperature; only the reported value is noisy.

Add the helper method:

```python
    def ambient(self, connector_id, measured):
        return ambient_c(LOCATION[connector_id][0], measured) if self.realism else AMBIENT_C
```

In `main()`: `simulator = Simulator(client, log, realism=config.sim_realism)`.

- [ ] **Step 6: Setting and Compose.** `chargeops/config.py`: add `sim_realism: bool = False`. `compose.yaml`, `simulator` service `environment`: add `SIM_REALISM: ${SIM_REALISM:-true}`. `.env.example`:

```
# Live simulator realism: diurnal per-station ambient (HCMC profile) and sensor noise. Tests run without it.
SIM_REALISM=true
```

- [ ] **Step 7: Manifest text.** In `chargeops/storage.py` `initialize()`, the `sources` document is `$setOnInsert`; bump it to a new manifest so existing databases get the new description. Change nothing in `domain.py`. Instead, add a second upsert right after the existing one:

```python
    db.sources.update_one(
        {"_id": "autosun-replay-v1"},
        {
            "$set": {
                "fields.ambient_temperature_c": "synthetic: HCMC diurnal profile per station (SIM_REALISM=true) or constant 25 °C",
                "fields.connector_temperature_c": "synthetic: thermal lag model with injected fault, Gaussian sensor noise σ 0.2 °C when SIM_REALISM=true",
            }
        },
    )
```

- [ ] **Step 8: Run tests**

Run: `"$UV" run pytest tests/test_realism.py tests/test_fleet.py -q -p no:cacheprovider --basetemp .pytest-tmp`
Expected: all pass (fleet tests construct `Simulator` without `realism`, so behaviour is unchanged).

- [ ] **Step 9: Add one fleet test with realism on**

```python
def test_realistic_healthy_run_opens_no_incident(store, monkeypatch):
    fleet = Harness(store, monkeypatch)
    fleet.simulator = Simulator(fleet.sim_client, realism=True)
    fleet.start("healthy", seed=3)
    fleet.run_until(600, worker=True)
    assert store.incidents.count_documents({}) == 0
    ambient = {s["ambient_temperature_c"] for s in store.telemetry.find({})}
    assert len(ambient) > 1  # ambient moves
```

Use the same harness helper names as the existing tests. If this test opens an incident, do **not** loosen the detector: report the residual values in the report and stop (noise σ or the ambient lag is wrong).

- [ ] **Step 10: Full suite, lint, evaluation lock, compose config** (same as Task 1 Step 8).

- [ ] **Step 11: Commit**

```bash
git add chargeops/realism.py chargeops/fleet.py chargeops/simulator.py chargeops/config.py chargeops/storage.py compose.yaml .env.example tests/test_realism.py tests/test_fleet.py
git commit -m "feat(sim): diurnal per-station ambient and seeded sensor noise behind SIM_REALISM"
```

---

### Task 7: Imperfect telemetry: seeded drops, duplicates and late samples

**Files:**
- Modify: `chargeops/realism.py` (add `Imperfections`), `chargeops/simulator.py` (`step`)
- Test: `tests/test_realism.py`, `tests/test_fleet.py`

**Interfaces:**
- Consumes: `sample_rng` from Task 6; `Simulator.pending` retry semantics (an unsent batch is retried unchanged).
- Produces:
  - `Imperfections(drop=0.01, duplicate=0.01, late=0.01)` with `apply(batch: list[dict], run_id: str, step: int) -> list[dict]`; held-back samples are released into the next batch.
  - `Simulator(..., realism=True)` applies it to every new batch.

- [ ] **Step 1: Failing tests** (append to `tests/test_realism.py`)

```python
from chargeops.realism import Imperfections


def batch(step):
    return [{"sample_id": f"{c}:{step}", "connector_id": c} for c in ("connector-1", "connector-2", "connector-3")]


def test_imperfections_are_seeded():
    a, b = Imperfections(drop=0.2, duplicate=0.2, late=0.2), Imperfections(drop=0.2, duplicate=0.2, late=0.2)
    assert [a.apply(batch(i), "r", i) for i in range(50)] == [b.apply(batch(i), "r", i) for i in range(50)]


def test_late_samples_arrive_in_the_next_batch():
    imp = Imperfections(drop=0.0, duplicate=0.0, late=1.0)
    assert imp.apply(batch(0), "r", 0) == []
    assert {s["sample_id"] for s in imp.apply(batch(1), "r", 1)} >= {"connector-1:0", "connector-2:0", "connector-3:0"}


def test_duplicates_repeat_the_same_sample_id():
    imp = Imperfections(drop=0.0, duplicate=1.0, late=0.0)
    out = imp.apply(batch(0), "r", 0)
    assert len(out) == 6 and len({s["sample_id"] for s in out}) == 3


def test_rates_are_roughly_respected():
    imp = Imperfections()
    sent = sum(len(imp.apply(batch(i), "r", i)) for i in range(2000))
    assert 5900 <= sent <= 6100  # 1% drop, 1% duplicate, late ones are delivered later
```

- [ ] **Step 2: Run to verify failure**

Run: `"$UV" run pytest tests/test_realism.py -q -p no:cacheprovider --basetemp .pytest-tmp`
Expected: FAIL, `ImportError: cannot import name 'Imperfections'`.

- [ ] **Step 3: Implement** (append to `chargeops/realism.py`)

```python
class Imperfections:
    """Field-like telemetry faults, reproducible per (run, step): a sample is dropped, sent twice (same sample_id,
    exercising the (run_id, sample_id) dedupe) or held back and sent with the next batch (out of order)."""

    def __init__(self, drop=0.01, duplicate=0.01, late=0.01):
        self.drop, self.duplicate, self.late = drop, duplicate, late
        self.held = []

    def apply(self, batch, run_id, step):
        out, self.held = list(self.held), []
        for sample in batch:
            roll = sample_rng(run_id, f"imperfect:{sample['connector_id']}", step).random()
            if roll < self.drop:
                continue
            if roll < self.drop + self.late:
                self.held.append(sample)
                continue
            out.append(sample)
            if roll < self.drop + self.late + self.duplicate:
                out.append(dict(sample))
        return out
```

- [ ] **Step 4: Wire into `Simulator.step`.** In `__init__` add `self.imperfect = Imperfections() if realism else None` and `self.last_step = None`. In `samples()`, right after `step = int(elapsed // STEP_S)`, add `self.last_step = step` (the same step index the sample IDs use). In `step()`, replace

```python
            self.pending = {"run_id": run["_id"], "samples": self.samples(run, measured or now())}
```

with

```python
            samples = self.samples(run, measured or now())
            if self.imperfect is not None:
                samples = self.imperfect.apply(samples, run["_id"], self.last_step)
            self.pending = {"run_id": run["_id"], "samples": samples}
```

A new run must not inherit held-back samples of the previous run: when `self.thermal` is reset for a new `run_id` in `samples()`, also set `self.imperfect.held = []` if `self.imperfect` is not `None`.

and guard the post so an empty batch is skipped (the API requires `min_length=1`):

```python
        if not self.pending["samples"]:
            self.pending = None
            return
```

placed directly before `result = self.client.post("/api/v1/telemetry/batch", ...)`.

- [ ] **Step 5: Fleet test with realism on** (append to `tests/test_fleet.py`)

```python
def test_imperfect_telemetry_keeps_one_incident_on_the_faulty_connector(store, monkeypatch):
    fleet = Harness(store, monkeypatch)
    fleet.simulator = Simulator(fleet.sim_client, realism=True)
    fleet.start("thermal_fault", seed=4)
    planned = next(c for c, p in fleet_plan(4, "thermal_fault").items() if p.fault_onset_s is not None)
    fleet.run_until(fleet_plan(4, "thermal_fault")[planned].fault_onset_s + 300, worker=True)
    opened = list(store.incidents.find({}, {"connector_id": 1}))
    assert [i["connector_id"] for i in opened] == [planned]
    ids = [s["sample_id"] for s in store.telemetry.find({})]
    assert len(ids) > len(set(ids))  # duplicates were stored and must be deduped by the pipeline
```

- [ ] **Step 6: Full suite, lint, evaluation lock** (same as Task 1 Step 8).

- [ ] **Step 7: Commit**

```bash
git add chargeops/realism.py chargeops/simulator.py tests/test_realism.py tests/test_fleet.py
git commit -m "feat(sim): seeded drop/duplicate/late telemetry behind SIM_REALISM"
```

---

### Task 8: Live verification and evidence (user runs Docker; agent prepares and records)

**Files:**
- Create: `scripts/verify_live.py`
- Modify: `docs/OBSERVABILITY.md` (move verified items out of "Chưa kiểm chứng thật"), `tasks.md` (tick only items with evidence), `docs/MINIMAL_LOOP.md` (new dated section)

**Interfaces:**
- Consumes: running Compose stack at `http://localhost:8080`; `artifacts/datadog-resources.json` written by `setup_datadog.py`.
- Produces: `artifacts/live-<UTC timestamp>.json` (git-ignored) with checks and pass/fail.

- [ ] **Step 1: Write `scripts/verify_live.py`** (read-only HTTP checks; never prints secrets)

```python
"""Read-only live checks against the running stack (UI/API on :8080). Prints PASS/FAIL per check and writes
artifacts/live-<UTC>.json. Does not start runs or send commands."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

BASE = "http://localhost:8080"
CHECKS = [
    ("ui_index", "/", lambda r: r.status_code == 200 and "<div id=\"root\"" in r.text),
    ("api_ready", "/api/v1/ready", lambda r: r.status_code == 200 and r.json()["status"] == "ready"),
    ("openapi", "/openapi.json", lambda r: r.status_code == 200 and "/api/v1/insights/incidents" in r.text),
    ("connectors_six", "/api/v1/connectors", lambda r: r.status_code == 200 and len(r.json()["items"]) in (0, 6)),
    ("evaluations", "/api/v1/evaluations", lambda r: r.status_code == 200 and r.json()["items"]),
    ("insights", "/api/v1/insights/incidents", lambda r: r.status_code == 200 and "by_state" in r.json()),
    ("spa_fallback", "/monitoring", lambda r: r.status_code == 200 and "<div id=\"root\"" in r.text),
]


def main():
    results = {}
    with httpx.Client(base_url=BASE, timeout=10) as client:
        for name, path, ok in CHECKS:
            try:
                response = client.get(path)
                results[name] = bool(ok(response))
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                results[name] = False
                print(f"{name}: {type(exc).__name__}")
    for name, passed in results.items():
        print(f"{'PASS' if passed else 'FAIL'} {name}")
    out = Path("artifacts") / f"live-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"base": BASE, "results": results}, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
```

If the built `index.html` mounts on a different element id than `root`, change both string checks to that id (read `web/index.html`).

- [ ] **Step 2: Hand the user this checklist** (they run it; Docker is on their machine):
  1. In `.env`: `DD_SITE=ap1.datadoghq.com`, a fresh `DD_API_KEY`/`DD_APP_KEY`, `DD_TRACE_ENABLED=true`, `DD_METRICS_ENABLED=true`, `SIM_REALISM=true`.
  2. `docker compose --profile observability up --build -d`, then `python scripts/verify_live.py` → all PASS.
  3. `docker compose --profile observability exec datadog agent status` → `API Key valid`, site ap1.
  4. `docker compose logs tunnel` → URL; `python scripts/setup_datadog.py --webhook-url https://…/api/v1/webhooks/datadog`; put `DD_MONITOR_ID` in `.env`; `docker compose --profile observability up -d worker`.
  5. In the UI: create + start a `datadog` / `thermal_fault` run (uses 1 of the remaining trial runs).
  6. In Datadog within ~5 min: dashboard groups show data for the run; composite monitor triggers on exactly one connector; the event overlay shows `opened`; the composite message links open the dashboard filtered to that connector and the logs view; a log line's trace link opens an APM trace; `trace.fastapi.request.hits` and `trace.worker.tick.duration` have data.
  7. Complete acknowledge → giảm tải → bảo trì → kiểm tra phục hồi in the UI. Keep `/stations/<station>` open on a second screen: the faulty connector shows "Đang sạc chậm để bảo vệ đầu sạc", its battery % keeps rising (slower) and returns to normal speed after recovery; the operator view shows the funnel and MTTR after resolve. Also run `docker compose exec api python -m chargeops.check_mongo` and record the `$setWindowFields ok` line.
  8. Screenshot each Datadog dashboard group, the monitor status page and one APM trace (no keys visible).

- [ ] **Step 3: Record evidence.** Add a dated section "Kiểm chứng thật 6 connector — <date>" to `docs/MINIMAL_LOOP.md` with run ID, incident ID, connector, monitor trigger time, webhook delivery, incident open/resolve times, which dashboard widgets showed data, and anything that did not work. Move verified items in `docs/OBSERVABILITY.md` out of "Chưa kiểm chứng thật". Tick `tasks.md` items only where this evidence exists.

- [ ] **Step 4: Commit and push**

```bash
git add scripts/verify_live.py docs/MINIMAL_LOOP.md docs/OBSERVABILITY.md tasks.md
git commit -m "docs: record 6-connector live Datadog verification and observability evidence"
git push origin feat/tasks-build
```

---

## Self-review notes

- Spec coverage: more observability panels → Tasks 1, 2, 5 (incident signals and events, extended role dashboards, extra monitors; per-station operator view); driver-facing station monitor with battery % → Tasks 4–5 (real Autosun SoC); MongoDB analytics → Task 3; closer to reality → Tasks 4, 6, 7; competition proof → Task 8. Security and tech debt intentionally excluded.
- Fingerprint safety: no task edits `domain.py`, `lifecycle.py`, `model.py` or `evaluation/*`; every task re-runs the v3.1 evaluation lock.
- Names used across tasks: `emit_heartbeat`, `emit_run_signals` (Task 1); `chargeops.incident.state_age_s`, `chargeops.worker.tick` (Tasks 1→2); `trend_pipeline`, `fleet_pipeline`, `IncidentSummary`, `TrendOut` (Tasks 3→5); `SOC_PROFILES`, `soc_percent`, `StationOut`, `public_status` (Tasks 4→5); `ambient_c`, `sample_rng`, `Imperfections` (Tasks 6→7).
