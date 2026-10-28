# ChargeOps — Post-Competition Observability & Realism Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After the competition, keep ChargeOps observable without a Datadog trial (self-hosted Prometheus + Grafana with the same panels and alerts), and move the simulation toward a real charging site: real weather, real plug-in/plug-out sessions, a shared station power cap, a real-time thermal mode, and an OCPP 1.6J path to real or simulated chargers.

**Architecture:** A `MetricsSink` choice (`datadog` | `prometheus` | `none`) lets the worker emit the same signals to either backend; Grafana dashboards and Prometheus alert rules are generated from Python builders, like the Datadog ones. Realism features are added as new modules first; changes to the evaluation-fingerprinted core (`domain.py`, `lifecycle.py`, `model.py`) are allowed **only** in Tasks 4–5 and must ship with a new evaluation protocol version, a re-tune on tuning seeds, and a lock committed before any evaluation run. OCPP comes last, behind an adapter that maps telemetry and derating onto the existing HTTP contract.

**Tech Stack:** Python 3.12, FastAPI, PyMongo, MongoDB 8, `prometheus_client`, Prometheus 2.53, Grafana 11.1 (file provisioning), `ocpp` (mobilityhouse) 1.0 with `websockets`, React/Vite (unchanged), pytest + mongomock.

## Global Constraints

- **Start date:** not before **23/11/2026**. The competition repo must stay untouched until the judges' access ends on 22/11/2026 (thể lệ 7.4). Work on a new branch `post/observability-realism` from `main` after `feat/tasks-build` is merged.
- Zero paid services; everything self-hosted in Compose, images pinned to exact tags (`prom/prometheus:v2.53.0`, `grafana/grafana:11.1.0`).
- Datadog code stays working (`METRICS_BACKEND=datadog`); nothing in this plan removes it.
- Metric label set is the same as for Datadog: `run_id`, `connector_id`, `station_id`, plus `state` on the incident-age gauge. No `incident_id`/`session_id` labels.
- Changes to `chargeops/domain.py`, `chargeops/lifecycle.py`, `chargeops/model.py` or `chargeops/evaluation/*` require: a new `PROTOCOL_VERSION` (`b1-eval-v4`, `v5`…), `python -m chargeops.evaluation tune` on the existing tuning seeds, committing the lock **before** `run`, keeping all earlier locks and results, and reporting real numbers next to v3.1.
- Never present simulated results as field performance. Real-world data (weather, OCPP meter values) keeps its provenance in the `sources` manifest.
- Docs in Vietnamese; code and commits in English; commit trailer with the model that wrote the commit.
- Tooling: same `UV` path and pytest/ruff/web commands as the competition plan (`docs/superpowers/plans/2026-10-25-competition-observability-realism.md`, Global Constraints).
- Out of scope: security hardening, refactors for their own sake, paid cloud.

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `chargeops/metrics.py` | Create | `PrometheusStats` (DogStatsd-compatible `increment/gauge/event`), `NullStats`, `make_stats(settings)` |
| `chargeops/grafana_dashboard.py` | Create | Grafana dashboard JSON builder mirroring the Datadog v2 groups |
| `chargeops/prometheus_rules.py` | Create | Prometheus alert rules builder (thermal, quality, stale, heartbeat, stuck) |
| `scripts/export_grafana.py` | Create | Writes `infra/grafana/dashboards/chargeops.json` and `infra/prometheus/rules.yml` |
| `infra/prometheus/prometheus.yml`, `infra/grafana/provisioning/**` | Create | Scrape config, datasource and dashboard provisioning |
| `chargeops/weather.py` | Create | Load a committed Open-Meteo hourly archive CSV; interpolate ambient per station |
| `data/weather/hcmc-2025-open-meteo.csv` + `data/manifests/open-meteo-hcmc-2025.json` | Create | Real hourly temperature data with attribution (CC BY 4.0) |
| `chargeops/sessions.py` | Create | Plug-in / plug-out schedule per connector from Autosun session gaps |
| `chargeops/station_power.py` | Create | Station power cap and proportional allocation across active connectors |
| `chargeops/adapters/ocpp_gateway.py` | Create | OCPP 1.6J central system: `MeterValues` → `/telemetry/batch`, derate → `SetChargingProfile` |
| `chargeops/worker.py`, `chargeops/config.py`, `chargeops/simulator.py`, `chargeops/fleet.py` | Modify | Backend selection, weather, sessions, power cap |
| `compose.yaml` | Modify | `oss-observability` and `ocpp` profiles |
| `docs/OBSERVABILITY.md`, `docs/CONTRACT.md`, `docs/DATA_CARD.md`, `docs/ARCHITECTURE.md` | Modify | Keep docs true |

---

### Task 1: Prometheus metrics backend

**Files:**
- Create: `chargeops/metrics.py`
- Modify: `chargeops/worker.py` (`main`), `chargeops/config.py`, `pyproject.toml` + `uv.lock`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: the DogStatsd call shapes used by `worker.py` and `observability.py`: `increment(name, value=1, tags=[...])`, `gauge(name, value, tags=[...])`, `event(title, text, alert_type=..., aggregation_key=..., source_type_name=..., tags=[...])`.
- Produces: `PrometheusStats(registry=None)`, `NullStats()`, `make_stats(config) -> object`, setting `metrics_backend: Literal["datadog","prometheus","none"] = "datadog"`, setting `metrics_port: int = 9108`. Prometheus names: `chargeops.current_a` → `chargeops_current_a`; counters get `_total`.

- [ ] **Step 1: Failing tests** `tests/test_metrics.py`

```python
from prometheus_client import CollectorRegistry, generate_latest

from chargeops.metrics import NullStats, PrometheusStats

TAGS = ["run_id:r1", "connector_id:connector-4", "station_id:station-2", "env:hackathon"]


def scrape(registry):
    return generate_latest(registry).decode()


def test_gauge_maps_tags_to_labels_and_drops_env():
    registry = CollectorRegistry()
    stats = PrometheusStats(registry)
    stats.gauge("chargeops.current_a", 120.0, tags=TAGS)
    text = scrape(registry)
    assert 'chargeops_current_a{connector_id="connector-4",run_id="r1",station_id="station-2"} 120.0' in text


def test_increment_is_a_counter():
    registry = CollectorRegistry()
    stats = PrometheusStats(registry)
    stats.increment("chargeops.samples", 3, tags=TAGS)
    stats.increment("chargeops.samples", 2, tags=TAGS)
    assert 'chargeops_samples_total{connector_id="connector-4",run_id="r1",station_id="station-2"} 5.0' in scrape(registry)


def test_state_label_is_kept_on_incident_age():
    registry = CollectorRegistry()
    PrometheusStats(registry).gauge("chargeops.incident.state_age_s", 90, tags=[*TAGS, "state:mitigating"])
    assert 'state="mitigating"' in scrape(registry)


def test_event_becomes_a_counter_by_event_name():
    registry = CollectorRegistry()
    PrometheusStats(registry).event("t", "x", tags=[*TAGS, "incident_id:i1", "event:opened", "source:chargeops"])
    text = scrape(registry)
    assert 'chargeops_incident_events_total{connector_id="connector-4",event="opened"' in text
    assert "incident_id" not in text  # never a metric label


def test_null_stats_accepts_everything():
    stats = NullStats()
    stats.gauge("a", 1, tags=[])
    stats.increment("b", tags=[])
    stats.event("t", "x", tags=[])
```

- [ ] **Step 2: Run to verify failure** — `"$UV" run pytest tests/test_metrics.py -q -p no:cacheprovider --basetemp .pytest-tmp` → `ModuleNotFoundError: No module named 'chargeops.metrics'` (or `prometheus_client`).

- [ ] **Step 3: Add the dependency** — `"$UV" add "prometheus-client>=0.20,<1"` (updates `pyproject.toml` and `uv.lock`).

- [ ] **Step 4: Implement** `chargeops/metrics.py`

```python
"""Metrics backends with the DogStatsd call shape the worker already uses, so the same signals reach Datadog,
Prometheus (scraped from the worker on METRICS_PORT) or nowhere."""

from prometheus_client import REGISTRY, Counter, Gauge, start_http_server

LABELS = ("run_id", "connector_id", "station_id", "state")
EVENT_LABELS = ("run_id", "connector_id", "station_id", "event")


def split(tags, allowed):
    pairs = dict(tag.split(":", 1) for tag in tags if ":" in tag)
    return {key: pairs[key] for key in allowed if key in pairs}


def prom_name(name):
    return name.replace(".", "_")


class PrometheusStats:
    def __init__(self, registry=None):
        self.registry = registry or REGISTRY
        self.metrics = {}

    def metric(self, kind, name, labels):
        key = (kind, name, tuple(labels))
        if key not in self.metrics:
            self.metrics[key] = kind(prom_name(name), name, labels, registry=self.registry)
        return self.metrics[key]

    def gauge(self, name, value, tags=()):
        labels = split(tags, LABELS)
        self.metric(Gauge, name, sorted(labels)).labels(**labels).set(value)

    def increment(self, name, value=1, tags=()):
        labels = split(tags, LABELS)
        self.metric(Counter, name, sorted(labels)).labels(**labels).inc(value)

    def event(self, title, text, tags=(), **_):
        labels = split(tags, EVENT_LABELS)
        self.metric(Counter, "chargeops.incident.events", sorted(labels)).labels(**labels).inc()


class NullStats:
    def gauge(self, *args, **kwargs):
        pass

    def increment(self, *args, **kwargs):
        pass

    def event(self, *args, **kwargs):
        pass


def make_stats(config):
    if config.metrics_backend == "prometheus":
        start_http_server(config.metrics_port)
        return PrometheusStats()
    if config.metrics_backend == "none":
        return NullStats()
    from datadog import DogStatsd

    return DogStatsd(host=config.dd_agent_host, port=8125)
```

Note: Prometheus requires one label set per metric name. All callers pass the same label keys per metric, so `sorted(labels)` is stable; if a caller ever mixes label sets for one name, `prometheus_client` raises, and the test suite will show it.

- [ ] **Step 5: Settings and worker.** `chargeops/config.py`:

```python
    metrics_backend: Literal["datadog", "prometheus", "none"] = "datadog"
    metrics_port: int = 9108
```

(add `from typing import Literal`). In `chargeops/worker.py` `main()` replace `stats = DogStatsd(host=settings().dd_agent_host, port=8125)` with `stats = make_stats(settings())` and import `make_stats` from `chargeops.metrics`. Datadog-mode-only emission rules stay as they are; for the Prometheus backend, treat `mode == "datadog"` runs and add a third run mode later only if needed (YAGNI).

- [ ] **Step 6: Run tests** — new tests pass; full suite and ruff pass.

- [ ] **Step 7: Commit** — `git commit -m "feat(metrics): Prometheus and null backends with the DogStatsd call shape"`

---

### Task 2: Grafana dashboard and Prometheus alert rules (self-hosted twin of Datadog v2)

**Files:**
- Create: `chargeops/grafana_dashboard.py`, `chargeops/prometheus_rules.py`, `scripts/export_grafana.py`
- Create: `infra/prometheus/prometheus.yml`, `infra/grafana/provisioning/datasources/prometheus.yml`, `infra/grafana/provisioning/dashboards/chargeops.yml`
- Generated + committed: `infra/grafana/dashboards/chargeops.json`, `infra/prometheus/rules.yml`
- Modify: `compose.yaml` (profile `oss-observability`), `docs/OBSERVABILITY.md`
- Test: `tests/test_grafana.py`

**Interfaces:**
- Consumes: Prometheus names from Task 1 (`chargeops_residual_positive_total`, `chargeops_residual_negative_total`, `chargeops_samples_total`, `chargeops_quality`, `chargeops_current_a`, …, `chargeops_worker_tick_total`, `chargeops_incident_open`, `chargeops_incident_state_age_s`).
- Produces: `dashboard() -> dict` (Grafana JSON model, `uid="chargeops"`), `rules() -> dict` (Prometheus rule file), `RESIDUAL_EXPR: str`.

- [ ] **Step 1: Failing tests** `tests/test_grafana.py`

```python
from chargeops.grafana_dashboard import RESIDUAL_EXPR, dashboard
from chargeops.prometheus_rules import rules


def panels(board):
    for row in board["panels"]:
        yield row
        yield from row.get("panels", [])


def test_dashboard_has_variables_and_same_groups_as_datadog():
    board = dashboard()
    assert [v["name"] for v in board["templating"]["list"]] == ["run_id", "station_id", "connector_id"]
    rows = [p["title"] for p in board["panels"] if p["type"] == "row"]
    assert rows == ["Tổng quan đội trạm", "Nhiệt", "Tải và can thiệp", "Chất lượng dữ liệu", "Sự cố"]


def test_residual_is_sample_weighted_over_one_minute():
    assert RESIDUAL_EXPR == (
        "(increase(chargeops_residual_positive_total{run_id=~\"$run_id\",connector_id=~\"$connector_id\"}[1m])"
        " - increase(chargeops_residual_negative_total{run_id=~\"$run_id\",connector_id=~\"$connector_id\"}[1m]))"
        " / increase(chargeops_samples_total{run_id=~\"$run_id\",connector_id=~\"$connector_id\"}[1m])"
    )


def test_every_query_uses_variables():
    for panel in panels(dashboard()):
        for target in panel.get("targets", []):
            if "worker_tick" not in target["expr"]:
                assert "$run_id" in target["expr"]


def test_alert_rules():
    group = rules()["groups"][0]
    names = {r["alert"] for r in group["rules"]}
    assert names == {"ChargeOpsThermalResidual", "ChargeOpsTelemetryStale", "ChargeOpsWorkerDown", "ChargeOpsDeratingStuck"}
    thermal = next(r for r in group["rules"] if r["alert"] == "ChargeOpsThermalResidual")
    assert thermal["expr"].endswith("> 8") and "chargeops_quality" in thermal["expr"]
```

- [ ] **Step 2: Run to verify failure** (module not found).

- [ ] **Step 3: Implement** `chargeops/grafana_dashboard.py`

```python
"""Grafana JSON model mirroring the Datadog v2 dashboard groups, for the self-hosted `oss-observability` profile."""

SEL = 'run_id=~"$run_id",connector_id=~"$connector_id"'
RESIDUAL_EXPR = (
    f"(increase(chargeops_residual_positive_total{{{SEL}}}[1m])"
    f" - increase(chargeops_residual_negative_total{{{SEL}}}[1m]))"
    f" / increase(chargeops_samples_total{{{SEL}}}[1m])"
)
DS = {"type": "prometheus", "uid": "prometheus"}


def target(expr, legend="{{connector_id}}"):
    return {"datasource": DS, "expr": expr, "legendFormat": legend}


def ts(title, exprs, unit="", thresholds=None):
    panel = {
        "type": "timeseries",
        "title": title,
        "datasource": DS,
        "targets": [target(e) for e in exprs],
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
        "gridPos": {"h": 8, "w": 12, "x": 0, "y": 0},
    }
    if thresholds:
        panel["fieldConfig"]["defaults"]["thresholds"] = {
            "mode": "absolute",
            "steps": [{"color": "green", "value": None}, *thresholds],
        }
        panel["fieldConfig"]["defaults"]["custom"] = {"thresholdsStyle": {"mode": "dashed"}}
    return panel


def stat(title, expr, red_above):
    return {
        "type": "stat",
        "title": title,
        "datasource": DS,
        "targets": [target(expr, "")],
        "fieldConfig": {
            "defaults": {
                "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "red", "value": red_above}]}
            },
            "overrides": [],
        },
        "gridPos": {"h": 4, "w": 6, "x": 0, "y": 0},
    }


def row(title, items):
    return {"type": "row", "title": title, "collapsed": True, "panels": items, "gridPos": {"h": 1, "w": 24, "x": 0, "y": 0}}


def variable(name, metric, label):
    return {
        "name": name,
        "type": "query",
        "datasource": DS,
        "query": f"label_values({metric}, {label})",
        "includeAll": True,
        "multi": True,
        "current": {"text": "All", "value": "$__all"},
        "refresh": 2,
    }


def dashboard():
    return {
        "uid": "chargeops",
        "title": "ChargeOps — vận hành đội trạm sạc",
        "schemaVersion": 39,
        "time": {"from": "now-30m", "to": "now"},
        "refresh": "10s",
        "templating": {
            "list": [
                variable("run_id", "chargeops_quality", "run_id"),
                variable("station_id", "chargeops_quality", "station_id"),
                variable("connector_id", "chargeops_quality", "connector_id"),
            ]
        },
        "panels": [
            row(
                "Tổng quan đội trạm",
                [
                    stat("Sự cố đang mở", f"sum(chargeops_incident_open{{{SEL}}})", 1),
                    stat("Mẫu cũ nhất (s)", f"max(chargeops_freshness_s{{{SEL}}})", 30),
                    stat("Worker tick / 5 phút", "sum(increase(chargeops_worker_tick_total[5m]))", 1e9),
                ],
            ),
            row(
                "Nhiệt",
                [
                    ts("Residual nhiệt (°C)", [RESIDUAL_EXPR], "celsius", [{"color": "orange", "value": 3}, {"color": "red", "value": 8}]),
                    ts("Nhiệt đo vs kỳ vọng (°C)", [f"chargeops_connector_temperature_c{{{SEL}}}", f"chargeops_expected_temperature_c{{{SEL}}}"], "celsius"),
                    ts("Nhiệt môi trường (°C)", [f"avg by (station_id) (chargeops_ambient_temperature_c{{{SEL}}})"], "celsius"),
                ],
            ),
            row(
                "Tải và can thiệp",
                [
                    ts("Dòng / yêu cầu / giới hạn (A)", [f"chargeops_current_a{{{SEL}}}", f"chargeops_requested_current_a{{{SEL}}}", f"chargeops_current_limit_a{{{SEL}}}"], "amp"),
                    ts("Công suất theo trạm (kW)", [f"sum by (station_id) (chargeops_power_kw{{{SEL}}})"], "kwatt"),
                ],
            ),
            row(
                "Chất lượng dữ liệu",
                [
                    ts("Cửa sổ hợp lệ", [f"chargeops_quality{{{SEL}}}"]),
                    ts("Mẫu / phút", [f"increase(chargeops_samples_total{{{SEL}}}[1m])"]),
                ],
            ),
            row(
                "Sự cố",
                [
                    ts("Sự cố đang mở", [f"chargeops_incident_open{{{SEL}}}"]),
                    ts("Thời gian ở trạng thái (s)", [f"chargeops_incident_state_age_s{{{SEL}}}"], "s", [{"color": "red", "value": 600}]),
                    ts("Sự kiện sự cố / phút", [f"sum by (event) (increase(chargeops_incident_events_total{{{SEL}}}[1m]))"]),
                ],
            ),
        ],
    }
```

`chargeops/prometheus_rules.py`

```python
"""Prometheus alert rules equivalent to the Datadog monitors (thermal composite, stale, heartbeat, stuck)."""

from chargeops.grafana_dashboard import RESIDUAL_EXPR

SEL_ALL = 'run_id=~".+",connector_id=~".+"'


def rules():
    residual = RESIDUAL_EXPR.replace('run_id=~"$run_id",connector_id=~"$connector_id"', SEL_ALL)
    return {
        "groups": [
            {
                "name": "chargeops",
                "rules": [
                    {
                        "alert": "ChargeOpsThermalResidual",
                        "expr": f"(min_over_time(chargeops_quality[1m]) > 0.5) and on (run_id, connector_id) ({residual}) > 8",
                        "for": "0m",
                        "labels": {"severity": "critical"},
                        "annotations": {"summary": "Residual nhiệt > 8 °C trên {{ $labels.connector_id }}"},
                    },
                    {
                        "alert": "ChargeOpsTelemetryStale",
                        "expr": "sum by (run_id, connector_id) (increase(chargeops_samples_total[2m])) < 12",
                        "for": "0m",
                        "labels": {"severity": "warning"},
                        "annotations": {"summary": "{{ $labels.connector_id }} gửi < 12 mẫu trong 2 phút"},
                    },
                    {
                        "alert": "ChargeOpsWorkerDown",
                        "expr": "sum(increase(chargeops_worker_tick_total[2m])) < 1 or absent(chargeops_worker_tick_total)",
                        "for": "1m",
                        "labels": {"severity": "critical"},
                        "annotations": {"summary": "Worker không tick trong 2 phút"},
                    },
                    {
                        "alert": "ChargeOpsDeratingStuck",
                        "expr": 'max by (run_id, connector_id) (chargeops_incident_state_age_s{state="mitigating"}) > 600',
                        "for": "0m",
                        "labels": {"severity": "warning"},
                        "annotations": {"summary": "{{ $labels.connector_id }} giảm tải quá 10 phút"},
                    },
                ],
            }
        ]
    }
```

`scripts/export_grafana.py`

```python
"""Write the generated Grafana dashboard and Prometheus rules (committed; regenerate after changing builders)."""

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chargeops.grafana_dashboard import dashboard  # noqa: E402
from chargeops.prometheus_rules import rules  # noqa: E402

(ROOT / "infra/grafana/dashboards").mkdir(parents=True, exist_ok=True)
(ROOT / "infra/grafana/dashboards/chargeops.json").write_text(json.dumps(dashboard(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
(ROOT / "infra/prometheus/rules.yml").write_text(yaml.safe_dump(rules(), sort_keys=False, allow_unicode=True), encoding="utf-8")
print("wrote infra/grafana/dashboards/chargeops.json and infra/prometheus/rules.yml")
```

(`"$UV" add --dev "pyyaml>=6,<7"` if PyYAML is not already available.)

- [ ] **Step 4: Provisioning files**

`infra/prometheus/prometheus.yml`

```yaml
global:
  scrape_interval: 5s
  evaluation_interval: 15s
rule_files: [/etc/prometheus/rules.yml]
scrape_configs:
  - job_name: chargeops-worker
    static_configs: [{targets: ["worker:9108"]}]
```

`infra/grafana/provisioning/datasources/prometheus.yml`

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    uid: prometheus
    type: prometheus
    url: http://prometheus:9090
    isDefault: true
```

`infra/grafana/provisioning/dashboards/chargeops.yml`

```yaml
apiVersion: 1
providers:
  - name: chargeops
    type: file
    options: {path: /var/lib/grafana/dashboards}
```

`compose.yaml` additions:

```yaml
  prometheus:
    image: prom/prometheus:v2.53.0
    profiles: [oss-observability]
    volumes:
      - ./infra/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./infra/prometheus/rules.yml:/etc/prometheus/rules.yml:ro
  grafana:
    image: grafana/grafana:11.1.0
    profiles: [oss-observability]
    ports: ["127.0.0.1:3000:3000"]
    environment:
      GF_AUTH_ANONYMOUS_ENABLED: "true"
      GF_AUTH_ANONYMOUS_ORG_ROLE: Viewer
    volumes:
      - ./infra/grafana/provisioning:/etc/grafana/provisioning:ro
      - ./infra/grafana/dashboards:/var/lib/grafana/dashboards:ro
    depends_on: [prometheus]
```

Worker env for this profile is set by the user in `.env`: `METRICS_BACKEND=prometheus`. Add `METRICS_BACKEND: ${METRICS_BACKEND:-datadog}` to the shared `x-env` anchor.

- [ ] **Step 5: Generate, test, validate**

```bash
"$UV" run python scripts/export_grafana.py
"$UV" run pytest tests/test_grafana.py -q -p no:cacheprovider --basetemp .pytest-tmp
docker run --rm -v "$PWD/infra/prometheus:/p" --entrypoint promtool prom/prometheus:v2.53.0 check rules /p/rules.yml
SIMULATOR_SECRET=x WEBHOOK_SECRET=x OPERATOR_SECRET=x docker compose --profile oss-observability config --quiet
```

Expected: tests pass; `promtool` prints `SUCCESS: 4 rules found`; compose config exits 0.

- [ ] **Step 6: Docs.** In `docs/OBSERVABILITY.md` add "Chạy không cần Datadog": `.env` `METRICS_BACKEND=prometheus`, `docker compose --profile oss-observability up -d`, Grafana at http://localhost:3000, Prometheus alerts at `/alerts`; and that alert routing to the ChargeOps webhook is not wired (Datadog-only), so the local detector remains the incident source in this mode.

- [ ] **Step 7: Commit** — `git commit -m "feat(oss-observability): Grafana dashboard and Prometheus rules generated from builders"`

---

### Task 3: Real weather for ambient (Open-Meteo archive)

**Files:**
- Create: `chargeops/weather.py`, `scripts/fetch_weather.py`, `data/weather/hcmc-2025-open-meteo.csv`, `data/manifests/open-meteo-hcmc-2025.json`
- Modify: `chargeops/simulator.py` (`Simulator.ambient`), `chargeops/config.py` (`sim_ambient: Literal["constant","diurnal","weather"]`), `docs/DATA_CARD.md`
- Test: `tests/test_weather.py`

**Interfaces:**
- Consumes: `realism.ambient_c` (competition Task 5) as the `diurnal` option; `domain.LOCATION`.
- Produces: `load_weather(path) -> list[tuple[datetime, float]]`, `weather_ambient(series, at: datetime, offset_c: float = 0.0) -> float`, `STATION_OFFSET_C = {"station-1": 0.0, "station-2": 1.5}`, setting `sim_ambient`.

- [ ] **Step 1: Fetch the data once (network, outside tests)** with `scripts/fetch_weather.py`:

```python
"""Download one year of hourly 2 m temperature for Ho Chi Minh City from the Open-Meteo archive API (CC BY 4.0)."""

import csv
import hashlib
import json
from pathlib import Path

import httpx

URL = "https://archive-api.open-meteo.com/v1/archive"
PARAMS = {"latitude": 10.82, "longitude": 106.63, "start_date": "2025-01-01", "end_date": "2025-12-31", "hourly": "temperature_2m", "timezone": "UTC"}
OUT = Path("data/weather/hcmc-2025-open-meteo.csv")

body = httpx.get(URL, params=PARAMS, timeout=60).raise_for_status().json()
OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["time_utc", "temperature_2m_c"])
    writer.writerows(zip(body["hourly"]["time"], body["hourly"]["temperature_2m"], strict=True))
manifest = {
    "id": "open-meteo-hcmc-2025",
    "source": "Open-Meteo Historical Weather API (ERA5-based)",
    "url": URL,
    "params": PARAMS,
    "license": "CC BY 4.0 — attribution: Open-Meteo.com",
    "sha256": hashlib.sha256(OUT.read_bytes()).hexdigest(),
    "rows": len(body["hourly"]["time"]),
}
Path("data/manifests/open-meteo-hcmc-2025.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f"wrote {OUT} ({manifest['rows']} rows)")
```

Run: `"$UV" run python scripts/fetch_weather.py` → `wrote … (8760 rows)`. Commit the CSV (~200 KB) and manifest. Before committing, open https://open-meteo.com/en/license and confirm the CC BY 4.0 terms still apply; record the date checked in `docs/DATA_CARD.md`.

- [ ] **Step 2: Failing tests** `tests/test_weather.py`

```python
from datetime import UTC, datetime

import pytest

from chargeops.weather import load_weather, weather_ambient

SERIES = [(datetime(2025, 3, 1, h, tzinfo=UTC), 25.0 + h) for h in range(3)]


def test_linear_interpolation_between_hours():
    assert weather_ambient(SERIES, datetime(2025, 3, 1, 0, 30, tzinfo=UTC)) == pytest.approx(25.5)


def test_same_time_of_year_is_reused_for_other_years():
    assert weather_ambient(SERIES, datetime(2026, 3, 1, 1, 0, tzinfo=UTC)) == pytest.approx(26.0)


def test_station_offset():
    assert weather_ambient(SERIES, datetime(2025, 3, 1, 1, tzinfo=UTC), offset_c=1.5) == pytest.approx(27.5)


def test_committed_file_loads_one_year():
    series = load_weather("data/weather/hcmc-2025-open-meteo.csv")
    assert 8700 <= len(series) <= 8800
    assert all(15.0 < t < 42.0 for _, t in series)
```

- [ ] **Step 3: Implement** `chargeops/weather.py`

```python
"""Ambient from a real hourly archive (Open-Meteo, CC BY 4.0), interpolated linearly; dates outside the archive
year reuse the same month/day/hour of the archive year."""

import bisect
import csv
from datetime import UTC, datetime
from functools import lru_cache

STATION_OFFSET_C = {"station-1": 0.0, "station-2": 1.5}  # rooftop site, documented assumption


@lru_cache(maxsize=4)
def load_weather(path):
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [(datetime.fromisoformat(r["time_utc"]).replace(tzinfo=UTC), float(r["temperature_2m_c"])) for r in rows]


def weather_ambient(series, at, offset_c=0.0):
    year = series[0][0].year
    probe = at.astimezone(UTC).replace(year=year)
    times = [t for t, _ in series]
    i = bisect.bisect_right(times, probe)
    if i == 0:
        return series[0][1] + offset_c
    if i == len(series):
        return series[-1][1] + offset_c
    (t0, v0), (t1, v1) = series[i - 1], series[i]
    fraction = (probe - t0).total_seconds() / (t1 - t0).total_seconds()
    return v0 + (v1 - v0) * fraction + offset_c
```

(29 February: `replace(year=2025)` raises; map it to 28 February before the replace. Add a test for `datetime(2028, 2, 29, 12, tzinfo=UTC)`.)

- [ ] **Step 4: Use it in the simulator.** Replace the `sim_realism` ambient choice with `sim_ambient`:

```python
    def ambient(self, connector_id, measured):
        station = LOCATION[connector_id][0]
        if self.ambient_mode == "weather":
            return weather_ambient(load_weather(WEATHER_FILE), measured, STATION_OFFSET_C[station])
        if self.ambient_mode == "diurnal":
            return ambient_c(station, measured)
        return AMBIENT_C
```

with `WEATHER_FILE = "data/weather/hcmc-2025-open-meteo.csv"`, `Simulator(..., ambient_mode="constant")`, and `main()` passing `config.sim_ambient`. Keep `SIM_REALISM` for noise/imperfections only. Make sure the Dockerfile copies `data/weather/`.

- [ ] **Step 5: Provenance.** Update the `sources` manifest text for `ambient_temperature_c` to `public: Open-Meteo HCMC 2025 hourly 2 m temperature, linearly interpolated, + station offset (synthetic)` when `SIM_AMBIENT=weather`, and add the dataset to `docs/DATA_CARD.md` and `docs/DISCLOSURE.md`.

- [ ] **Step 6: Tests, lint, commit** — `git commit -m "feat(sim): real Open-Meteo ambient for Ho Chi Minh City"`

---

### Task 4: Shared station power cap (protocol v4)

**Files:**
- Create: `chargeops/station_power.py`
- Modify: `chargeops/fleet.py` (`physics` receives the allocated limit), `chargeops/simulator.py` (allocate per station each step), `chargeops/model.py` + `chargeops/evaluation/runner.py` (same rule offline), `chargeops/evaluation/protocol.py` (`PROTOCOL_VERSION = "b1-eval-v4"`), `docs/CONTRACT.md`, `docs/EVALUATION_PROTOCOL.md`
- Test: `tests/test_station_power.py`, `tests/test_evaluation.py`

**Interfaces:**
- Produces: `STATION_CAP_KW = {"station-1": 240.0, "station-2": 180.0}`; `allocate(cap_kw: float, demands: dict[str, tuple[float, float]], limits: dict[str, float]) -> dict[str, float]` returning per-connector current limits (A), where `demands[c] = (requested_a, voltage_v)`.
- Rule: if the sum of requested power ≤ cap, each connector keeps `min(requested, own limit)`. Otherwise power is shared proportionally to requested power, never above a connector's own (operator) limit; current freed by a derated connector goes to the others (water-filling).

- [ ] **Step 1: Failing tests** `tests/test_station_power.py`

```python
import pytest

from chargeops.station_power import allocate


def test_under_cap_everyone_gets_demand():
    out = allocate(240.0, {"a": (100.0, 400.0), "b": (100.0, 400.0)}, {"a": 250.0, "b": 250.0})
    assert out == {"a": 100.0, "b": 100.0}


def test_over_cap_is_proportional():
    out = allocate(80.0, {"a": (150.0, 400.0), "b": (50.0, 400.0)}, {"a": 250.0, "b": 250.0})
    assert sum(out[c] * 400.0 / 1000 for c in out) == pytest.approx(80.0)
    assert out["a"] / out["b"] == pytest.approx(3.0)


def test_derated_connector_frees_power_for_others():
    out = allocate(80.0, {"a": (150.0, 400.0), "b": (50.0, 400.0)}, {"a": 30.0, "b": 250.0})
    assert out["a"] == 30.0
    assert out["b"] == pytest.approx(50.0)  # b's full demand now fits


def test_idle_connector_takes_nothing():
    assert allocate(80.0, {"a": (0.0, 0.0), "b": (300.0, 400.0)}, {"a": 250.0, "b": 250.0})["a"] == 0.0
```

- [ ] **Step 2: Implement** `chargeops/station_power.py`

```python
"""Station-level power cap shared by its connectors (proportional water-filling, per-connector limit respected)."""

STATION_CAP_KW = {"station-1": 240.0, "station-2": 180.0}


def allocate(cap_kw, demands, limits):
    want = {c: min(req, limits[c]) for c, (req, _) in demands.items()}
    power = {c: want[c] * demands[c][1] / 1000 for c in demands}
    if sum(power.values()) <= cap_kw:
        return want
    out, left, open_ = {}, cap_kw, {c for c in demands if power[c] > 0}
    for c in set(demands) - open_:
        out[c] = 0.0
    while open_:
        share_total = sum(power[c] for c in open_)
        done = {c for c in open_ if power[c] / share_total * left >= power[c]}
        if not done:
            for c in open_:
                kw = power[c] / share_total * left
                out[c] = kw * 1000 / demands[c][1]
            return out
        for c in done:
            out[c] = want[c]
            left -= power[c]
        open_ -= done
    return out
```

- [ ] **Step 3: Use it live and offline.** In `Simulator.samples()`, before the per-connector loop: compute each connector's demand and own limit, group by station, call `allocate(STATION_CAP_KW[station], ...)`, and pass the allocated limit into `physics` as the effective `current_limit_a` for that step (report the *operator* limit as `current_limit_a` and add the station-allocated value as a new sample field `station_limit_a` only after updating `Sample`/`SampleOut` in `domain.py` — this is the reason the task needs protocol v4). Mirror the same rule in the evaluation runner for the A/B/C arms.

- [ ] **Step 4: Contract and protocol.** CONTRACT §1 gets `station_limit_a`; §3 states that "giảm dòng có tác dụng" is judged against the operator target, and that current can also fall because of the station cap (recorded, not counted as the command's effect). Bump to `b1-eval-v4`, run `tune` on the same tuning seeds, commit the lock, then `run`; report v4 next to v3.1 in `docs/EVALUATION_PROTOCOL.md`.

- [ ] **Step 5: Observability.** Add `chargeops.station_power_kw` and `chargeops.station_cap_kw` gauges (tag `station_id`) in `observability.py`, a "Công suất trạm vs giới hạn" panel to both the Datadog and Grafana builders, and a test for each builder.

- [ ] **Step 6: Tests, lint, evaluation, commit** — `git commit -m "feat: shared station power cap with proportional allocation (protocol b1-eval-v4)"`

---

### Task 5: Real plug-in / plug-out sessions and a real-time thermal mode (protocol v5)

**Files:**
- Create: `chargeops/sessions.py`
- Modify: `chargeops/fleet.py`, `chargeops/simulator.py` (`session_id`, `connector_status`, `session_status`), `chargeops/domain.py` (`THERMAL_TAU_S` per run: `thermal_tau_s` field with default 60), `chargeops/lifecycle.py` (session end during `mitigating`/`verifying`), `chargeops/evaluation/*` (v5), `docs/CONTRACT.md`
- Test: `tests/test_sessions.py`, `tests/test_lifecycle.py`

**Interfaces:**
- Produces: `schedule(profile_ids: list[str], seed: int, horizon_s: int) -> list[Session]` with `Session(connector_id, session_id, start_s, end_s, profile_id)`; idle gaps drawn from 2–20 minutes; each session replays one Autosun transaction for its real length (`end_time - begin_time` in the source data).
- Lifecycle rule (new, CONTRACT §3): if the vehicle unplugs while an incident is `mitigating` or `verifying`, record `session_ended`, keep the incident unresolved, pause the retest timer, and resume when a new session starts on that connector with enough load; `resolved` still requires a full 120 s retest at ≥90 % of the reference current.
- Real-time mode: run option `thermal_tau_s ∈ {60, 2220}` (2220 s ≈ 37 min, the uncompressed value from the data card); the detector baseline reads the run's value.

- [ ] **Step 1: Failing tests** `tests/test_sessions.py`

```python
from chargeops.sessions import schedule


def test_sessions_do_not_overlap_and_leave_idle_gaps():
    plan = schedule(["p1", "p2", "p3", "p4", "p5", "p6"], seed=7, horizon_s=4 * 3600)
    by_connector = {}
    for s in plan:
        by_connector.setdefault(s.connector_id, []).append(s)
    for sessions in by_connector.values():
        for a, b in zip(sessions, sessions[1:]):
            assert 120 <= b.start_s - a.end_s <= 1200


def test_schedule_is_seeded():
    assert schedule(["p1"] * 6, 3, 3600) == schedule(["p1"] * 6, 3, 3600)
```

and in `tests/test_lifecycle.py` a test: incident `verifying`, samples go to 0 A with `session_status="completed"` → state stays `verifying`, timeline gains `session_ended`, retest timer does not reset to failure; a new session with load ≥90 % reference resumes the timer; resolve only after 120 s of qualifying samples in total.

- [ ] **Step 2: Implement** `chargeops/sessions.py`

```python
"""Plug-in / plug-out schedule per connector: real Autosun session lengths separated by seeded idle gaps."""

import random
from dataclasses import dataclass

from chargeops.domain import CONNECTORS
from chargeops.model import PROFILES

IDLE_S = (120, 1200)


@dataclass(frozen=True)
class Session:
    connector_id: str
    session_id: str
    start_s: int
    end_s: int
    profile_id: str


def length_s(profile_id):
    return len(PROFILES[profile_id]) * 60  # profiles are 1-minute rows


def schedule(profile_ids, seed, horizon_s):
    rng = random.Random(seed)
    out = []
    for cid in CONNECTORS:
        t, n = rng.randrange(0, IDLE_S[1], 5), 0
        while t < horizon_s:
            profile = rng.choice(profile_ids)
            end = min(t + length_s(profile), horizon_s)
            out.append(Session(cid, f"{cid}-s{n}", t, end, profile))
            n += 1
            t = end + rng.randrange(IDLE_S[0], IDLE_S[1] + 1, 5)
    return out
```

(If `PROFILES[p]` is not a list of 1-minute rows, read `chargeops/model.py:load_profiles` and compute the real length from it.)

- [ ] **Step 3: Simulator and lifecycle.** Simulator: each step, find the connector's active session (if none: `current_a=0`, `connector_status="available"`, `session_status="idle"`, `session_id=None`); replay from `elapsed - start_s`. Lifecycle: implement the rule above in `check_mitigation` and `check_retest`, mirrored in `evaluation/rules.py`, with parity tests extended to the unplug path.

- [ ] **Step 4: Real-time thermal.** Add `thermal_tau_s` to the run document (API `DemoRunCreate`, default 60), pass it through `with_residuals`/`lag` (new optional argument, default `THERMAL_TAU_S`), and to the simulator physics. Document that `2220` makes a demo slow (≈ 3 h for a full loop) and is meant for soak tests.

- [ ] **Step 5: Observability.** Gauges `chargeops.connector_busy` (1/0) and `chargeops.sessions_started` counter; dashboard panel "Mức sử dụng connector (%)" = `avg(chargeops.connector_busy) * 100` by station, in both builders.

- [ ] **Step 6: Protocol v5, tests, commit** — bump to `b1-eval-v5`, re-tune on tuning seeds, lock before run, report next to v3.1/v4. `git commit -m "feat: real plug-in sessions and real-time thermal mode (protocol b1-eval-v5)"`

---

### Task 6: OCPP 1.6J gateway (simulated chargers first)

**Files:**
- Create: `chargeops/adapters/__init__.py`, `chargeops/adapters/ocpp_gateway.py`, `scripts/ocpp_charge_point.py` (test charge point)
- Modify: `compose.yaml` (profile `ocpp`), `pyproject.toml` + `uv.lock` (`ocpp>=1,<2`, `websockets>=12,<14`), `docs/ARCHITECTURE.md` (§2 status: `TelemetrySource`/`ChargerCommandSink` now have an OCPP implementation)
- Test: `tests/test_ocpp_gateway.py`

**Interfaces:**
- Consumes: `POST /api/v1/telemetry/batch` (simulator secret) and `GET /api/v1/simulator/commands` + `POST /api/v1/simulator/commands/{id}/result` — the gateway plays the simulator's role for real chargers, so the API and worker do not change.
- Produces: `meter_values_to_sample(charge_point_id: str, connector_id: int, meter_value: list[dict], run_id: str) -> dict | None`; `limit_to_profile(target_current_a: float, transaction_id: int | None) -> dict` (OCPP `SetChargingProfile` payload, `chargingRateUnit: "A"`); `CONNECTOR_MAP: dict[tuple[str, int], str]` mapping `(charge point id, OCPP connector id)` to ChargeOps connector ids.

- [ ] **Step 1: Failing tests** `tests/test_ocpp_gateway.py`

```python
from chargeops.adapters.ocpp_gateway import limit_to_profile, meter_values_to_sample

METER = [
    {
        "timestamp": "2026-12-01T03:00:05Z",
        "sampledValue": [
            {"value": "120.5", "measurand": "Current.Import", "unit": "A"},
            {"value": "398", "measurand": "Voltage", "unit": "V"},
            {"value": "47.9", "measurand": "Power.Active.Import", "unit": "kW"},
            {"value": "41.2", "measurand": "Temperature", "unit": "Celsius", "location": "Outlet"},
            {"value": "250", "measurand": "Current.Offered", "unit": "A"},
        ],
    }
]


def test_meter_values_map_to_contract_sample():
    sample = meter_values_to_sample("CP-1", 1, METER, run_id="00000000-0000-4000-8000-0000000000d1")
    assert sample["connector_id"] == "connector-1"
    assert sample["current_a"] == 120.5 and sample["voltage_v"] == 398.0 and sample["power_kw"] == 47.9
    assert sample["connector_temperature_c"] == 41.2
    assert sample["current_limit_a"] == 250.0
    assert sample["measured_at"] == "2026-12-01T03:00:05Z"


def test_missing_temperature_is_not_a_sample():
    meter = [{"timestamp": METER[0]["timestamp"], "sampledValue": METER[0]["sampledValue"][:3]}]
    assert meter_values_to_sample("CP-1", 1, meter, run_id="r") is None


def test_limit_becomes_absolute_ampere_profile():
    profile = limit_to_profile(30.0, transaction_id=42)
    assert profile["connectorId"] == 0 or profile["csChargingProfiles"]["chargingProfilePurpose"] == "TxProfile"
    period = profile["csChargingProfiles"]["chargingSchedule"]["chargingSchedulePeriod"][0]
    assert period == {"startPeriod": 0, "limit": 30.0}
    assert profile["csChargingProfiles"]["chargingSchedule"]["chargingRateUnit"] == "A"
```

Ambient is not an OCPP measurand: the gateway fills `ambient_temperature_c` from `chargeops.weather` (Task 3) for the station and marks it `public`/`derived` in the manifest, and fills `requested_current_a` from `Current.Offered` only when the charger reports `Current.Import` below it; otherwise `requested_current_a = current_a` (documented as an approximation — OCPP 1.6 has no EV-requested current).

- [ ] **Step 2: Implement** `chargeops/adapters/ocpp_gateway.py` (pure mapping functions first; the websocket server is a thin layer on top)

```python
"""OCPP 1.6J central-system adapter: MeterValues → ChargeOps telemetry samples, ChargeOps derating command →
SetChargingProfile (TxProfile, amperes). The gateway authenticates to the API with the simulator secret and plays
the simulator's role, so API, worker and state machine are unchanged."""

from uuid import NAMESPACE_URL, uuid5

CONNECTOR_MAP = {("CP-1", c): f"connector-{c}" for c in (1, 2, 3)} | {("CP-2", c): f"connector-{c + 3}" for c in (1, 2, 3)}
MEASURANDS = {
    "Current.Import": "current_a",
    "Voltage": "voltage_v",
    "Power.Active.Import": "power_kw",
    "Temperature": "connector_temperature_c",
    "Current.Offered": "current_limit_a",
}


def meter_values_to_sample(charge_point_id, connector_id, meter_value, run_id):
    latest = meter_value[-1]
    values = {}
    for sv in latest["sampledValue"]:
        field = MEASURANDS.get(sv.get("measurand", "Energy.Active.Import.Register"))
        if field:
            value = float(sv["value"])
            if field == "power_kw" and sv.get("unit") == "W":
                value /= 1000
            values[field] = value
    if "connector_temperature_c" not in values or "current_a" not in values:
        return None
    cid = CONNECTOR_MAP[(charge_point_id, connector_id)]
    return {
        "sample_id": str(uuid5(NAMESPACE_URL, f"{run_id}:{cid}:{latest['timestamp']}")),
        "run_id": run_id,
        "connector_id": cid,
        "measured_at": latest["timestamp"],
        "current_a": values["current_a"],
        "voltage_v": values.get("voltage_v", 0.0),
        "power_kw": values.get("power_kw", values.get("voltage_v", 0.0) * values["current_a"] / 1000),
        "requested_current_a": values["current_a"],
        "current_limit_a": values.get("current_limit_a", values["current_a"]),
        "connector_temperature_c": values["connector_temperature_c"],
    }


def limit_to_profile(target_current_a, transaction_id):
    return {
        "connectorId": 0,
        "csChargingProfiles": {
            "chargingProfileId": 1,
            "transactionId": transaction_id,
            "stackLevel": 1,
            "chargingProfilePurpose": "TxProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "A",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": target_current_a}],
            },
        },
    }
```

Then add the server: an `ocpp.v16.ChargePoint` subclass with `@on(Action.boot_notification)`, `@on(Action.meter_values)` (maps with `meter_values_to_sample`, adds ambient from `weather`, posts to `/telemetry/batch`) and a background task that polls `/simulator/commands` every 5 s, sends `SetChargingProfile` for `reduce_load` (limit = command `target_current_a`) and `ClearChargingProfile` for `verify_recovery`, and posts `applied`/`failed` from the OCPP response status. The charge point's real `connectorId` is used in place of `0` when the command targets one connector (fix `connectorId` from `CONNECTOR_MAP` reverse lookup and add a test for it).

- [ ] **Step 3: Test charge point.** `scripts/ocpp_charge_point.py` connects with the `ocpp` client, sends `BootNotification` and `MeterValues` every 5 s from the existing fleet physics (so the same thermal model drives it), and honours `SetChargingProfile` by capping its current. An end-to-end test (marked `@pytest.mark.ocpp`, run on demand) starts gateway + charge point on localhost and asserts a `reduce_load` command ends `applied` with the charge point current at the target within 10 s.

- [ ] **Step 4: Compose profile `ocpp`** with the gateway on `0.0.0.0:9000` inside the network and `127.0.0.1:9000` on the host; the simulator service is disabled in this profile (the gateway is the telemetry source).

- [ ] **Step 5: Observability.** OCPP message counters `chargeops.ocpp.messages{action}` and `chargeops.ocpp.command_latency_s` gauge; "OCPP" row in both dashboard builders.

- [ ] **Step 6: Tests, lint, commit** — `git commit -m "feat(ocpp): 1.6J gateway mapping MeterValues and SetChargingProfile onto the existing contract"`

---

## Order and milestones

| Milestone | Tasks | Done when |
|---|---|---|
| M1 — observable without Datadog | 1, 2 | Grafana shows a full thermal_fault loop; Prometheus `ChargeOpsThermalResidual` fires once for the faulty connector |
| M2 — real environment | 3 | A run's ambient matches the Open-Meteo hour for that date ± station offset; provenance updated |
| M3 — real site behaviour | 4, 5 | v4 and v5 evaluation reported honestly next to v3.1; dashboards show station cap and utilisation |
| M4 — real protocol | 6 | A simulated OCPP charge point completes acknowledge → derate (`SetChargingProfile`) → maintenance → verify → resolved |

## Self-review notes

- Spec coverage: more observability panels → Tasks 1–2 (Prometheus/Grafana twin) plus panels added in Tasks 4–6; closer to reality → Tasks 3 (real weather), 4 (station power cap), 5 (real sessions, real-time thermal), 6 (OCPP). Security and tech debt excluded.
- Fingerprint discipline: only Tasks 4 and 5 touch fingerprinted files, each with its own protocol version and lock-before-run.
- Name consistency: `PrometheusStats/NullStats/make_stats` (1→2), `RESIDUAL_EXPR` (2), `weather_ambient/load_weather/STATION_OFFSET_C` (3→6), `allocate/STATION_CAP_KW` (4), `schedule/Session` (5), `meter_values_to_sample/limit_to_profile/CONNECTOR_MAP` (6).
