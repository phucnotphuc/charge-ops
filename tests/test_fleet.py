"""WP-W2B: two stations × three connectors, seeded faults, per-connector worker and command routing."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import UUID, uuid5

import pytest
from fastapi.testclient import TestClient

from chargeops.api import app
from chargeops.domain import CONNECTORS, LOCATION, MANIFEST, window_evidence
from chargeops.fleet import fleet_plan, physics, steady_load
from chargeops.model import ORIGINAL_LIMIT_A, PROFILES
from chargeops.simulator import Simulator
from chargeops.worker import process_event, tick

SIMULATOR = {"X-Simulator-Secret": "s" * 32}
OPERATOR = {"X-Operator-Secret": "o" * 32}
WEBHOOK = {"X-Webhook-Secret": "w" * 32}
RUN = "00000000-0000-4000-8000-0000000000f1"
T0 = datetime(2026, 10, 21, 3, 0, tzinfo=UTC)  # on a 15-second boundary


def test_retry_old_fault_event_preserves_new_injection(store):
    from chargeops.api import consume_event

    newer = T0 + timedelta(seconds=100)
    store.runs.insert_one({"_id": RUN, "sim": {"connector-1": {"fault_injected_at": newer}}})
    store.run_events.insert_one({
        "_id": "old", "type": "inject_fault", "run_id": RUN,
        "connector_id": "connector-1", "consumed_at": T0,
    })
    assert consume_event("old", store) == {"status": "duplicate"}
    assert store.runs.find_one({"_id": RUN})["sim"]["connector-1"]["fault_injected_at"] == newer


class Harness:
    """Simulator, API and worker on one simulated clock; the simulator talks to the API over HTTP only."""

    def __init__(self, store, monkeypatch):
        self.store, self.clock = store, [T0]
        monkeypatch.setattr("chargeops.api.now", lambda: self.clock[0])
        monkeypatch.setattr("chargeops.worker.now", lambda: self.clock[0])
        self.client = TestClient(app)
        self.sim_client = TestClient(app, headers=SIMULATOR)
        self.simulator = Simulator(self.sim_client)
        self.stats = Mock()
        self.t = 0

    def start(self, scenario, seed, mode="local", profile=None):
        self.store.runs.insert_one(
            {
                "_id": RUN,
                "status": "active",
                "mode": mode,
                "scenario": scenario,
                "seed": seed,
                "profile_id": profile,
                "created_at": T0,
                "started_at": T0,
                "active_slot": "single",
                "stop_requested": False,
            }
        )

    def run_until(self, seconds, worker=True):
        while self.t <= seconds:
            self.clock[0] = T0 + timedelta(seconds=self.t)
            self.simulator.step(self.clock[0])
            if worker:
                tick(self.store, self.stats, Mock())
            self.t += 5

    def run_while(self, busy, limit):
        """Advance step by step while `busy()` holds, up to `limit` seconds."""
        while busy() and self.t <= limit:
            self.run_until(self.t)

    def command(self, incident_id, kind):
        response = self.client.post(
            f"/api/v1/incidents/{incident_id}/commands",
            json={"type": kind},
            headers={**OPERATOR, "Idempotency-Key": f"{incident_id}-{kind}"},
        )
        assert response.status_code == 202
        return response.json()["command_id"]

    def incidents(self):
        return list(self.store.incidents.find())


@pytest.fixture
def fleet(store, monkeypatch):
    return Harness(store, monkeypatch)


def test_two_stations_three_connectors_each():
    assert CONNECTORS == tuple(f"connector-{i}" for i in range(1, 7))
    assert [LOCATION[c] for c in CONNECTORS] == [("station-1", "charger-1")] * 3 + [
        ("station-2", "charger-2")
    ] * 3


def test_plan_is_deterministic_per_seed_with_distinct_profiles():
    plan = fleet_plan(7, "thermal_fault")
    assert plan == fleet_plan(7, "thermal_fault")
    assert len({p.profile_id for p in plan.values()}) == 6
    faulty = [c for c, p in plan.items() if p.fault_onset_s is not None]
    assert len(faulty) == 1
    fault = plan[faulty[0]]
    assert 120 <= fault.fault_onset_s <= 300 and fault.fault_onset_s % 5 == 0
    assert 9.5 <= fault.fault_severity_c <= 11.0
    assert all(p.scenario == "healthy" for c, p in plan.items() if c != faulty[0])
    seeds = {s: fleet_plan(s, "thermal_fault") for s in range(40)}
    assert len({next(c for c, p in v.items() if p.fault_onset_s) for v in seeds.values()}) == 6
    assert len({tuple(p.profile_id for p in v.values()) for v in seeds.values()}) == 40


def test_plan_profiles_independent_of_scenario_and_override_is_kept():
    healthy, fault = fleet_plan(11, "healthy"), fleet_plan(11, "thermal_fault")
    assert [p.profile_id for p in healthy.values()] == [p.profile_id for p in fault.values()]
    assert all(p.fault_onset_s is None for p in healthy.values())
    chosen = sorted({p.profile_id for p in fault.values()})[0]
    overridden = fleet_plan(11, "thermal_fault", chosen)
    faulty = next(c for c, p in overridden.items() if p.fault_onset_s is not None)
    assert overridden[faulty].profile_id == chosen
    assert len({p.profile_id for p in overridden.values()}) == 6


def test_pinned_profile_without_steady_load_is_rejected(fleet):
    plan = fleet_plan(11, "thermal_fault")
    onset = next(p.fault_onset_s for p in plan.values() if p.fault_onset_s is not None)
    tapering = next(p for p in sorted(PROFILES) if not steady_load(PROFILES[p], onset))
    with pytest.raises(ValueError, match="no steady load"):
        fleet_plan(11, "thermal_fault", tapering)
    fleet_plan(11, "healthy", tapering)  # only the faulty connector needs a steady load
    body = {"scenario": "thermal_fault", "seed": 11, "mode": "local", "profile": tapering}
    response = fleet.client.post("/api/v1/demo-runs", json=body, headers=OPERATOR)
    assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"


def test_load_shapes_reuse_the_evaluation_model():
    from chargeops.model import PROFILES, replay, scenario_demand

    for scenario in ("load_increase", "demand_drop"):
        changed = 0
        for p in fleet_plan(3, scenario).values():
            assert p.change_s is not None and p.scenario == scenario
            late = p.change_s + 120
            base = replay(PROFILES[p.profile_id], late)[0]
            shaped = scenario_demand(base, late, scenario, p.change_s, p.factor, p.drop, p.cap_a)
            assert physics(p, late, {})[0] == shaped
            changed += shaped != base
        assert changed >= 3


def test_six_samples_per_step_in_one_batch_with_ids_and_provenance(fleet, store):
    fleet.start("healthy", 5)
    posted = []
    original = fleet.sim_client.post

    def spy(url, **kwargs):
        if url.endswith("/telemetry/batch"):
            posted.append(kwargs["json"]["samples"])
        return original(url, **kwargs)

    fleet.sim_client.post = spy
    fleet.run_until(10, worker=False)
    assert [len(batch) for batch in posted] == [6, 6, 6]
    step = posted[1]
    assert [s["connector_id"] for s in step] == list(CONNECTORS)
    assert [s["sample_id"] for s in step] == [str(uuid5(UUID(RUN), f"{c}:1")) for c in CONNECTORS]
    stored = list(store.telemetry.find({"sample_id": step[3]["sample_id"]}))
    assert len(stored) == 1
    assert stored[0]["meta"] == {
        "run_id": RUN,
        "connector_id": "connector-4",
        "station_id": "station-2",
        "charger_id": "charger-2",
    }
    assert stored[0]["provenance"] == {"composition": "hybrid", "manifest_id": MANIFEST}
    # Each connector replays its own profile.
    assert len({s["requested_current_a"] for s in step}) == 6


def test_batch_retry_reuses_sample_ids(fleet, store):
    fleet.start("healthy", 5)
    original = fleet.sim_client.post
    calls = []

    def flaky(url, **kwargs):
        if url.endswith("/telemetry/batch"):
            calls.append([s["sample_id"] for s in kwargs["json"]["samples"]])
            if len(calls) == 1:
                import httpx

                raise httpx.ConnectError("down")
        return original(url, **kwargs)

    fleet.sim_client.post = flaky
    import httpx

    with pytest.raises(httpx.ConnectError):
        fleet.simulator.step(T0)
    fleet.simulator.step(T0 + timedelta(seconds=5))
    assert calls[0] == calls[1]
    assert store.telemetry.count_documents({}) == 6


def test_windows_are_per_connector(fleet, store):
    fleet.start("healthy", 5)
    fleet.run_until(80)
    windows = list(store.windows.find({"end": T0 + timedelta(seconds=75)}))
    assert sorted(w["connector_id"] for w in windows) == list(CONNECTORS)
    for w in windows:
        assert w["_id"] == f"{RUN}:{w['connector_id']}:{w['end'].isoformat()}"
        assert w["quality"] and w["count"] == 12
        assert {s["connector_id"] for s in w["samples"]} == {w["connector_id"]}
        assert abs(w["residual_mean"]) < 1e-9


def test_fault_on_connector_4_opens_one_incident_and_commands_apply_only_there(fleet, store):
    fleet.start("healthy", 5)
    fleet.run_until(60)
    response = fleet.client.post(
        f"/api/v1/demo-runs/{RUN}/inject-fault", json={"connector_id": "connector-4"}, headers=OPERATOR
    )
    assert response.status_code == 202
    fleet.run_while(lambda: not fleet.incidents(), 420)
    incidents = fleet.incidents()
    assert [(i["connector_id"], i["state"]) for i in incidents] == [("connector-4", "open")]
    assert incidents[0]["evidence"]["connector_id"] == "connector-4"
    incident = incidents[0]
    fleet.command(incident["_id"], "acknowledge")
    fleet.run_until(fleet.t)
    fleet.command(incident["_id"], "reduce_load")
    fleet.run_while(lambda: store.commands.find_one({"type": "reduce_load"})["status"] != "applied", 600)
    fleet.run_until(fleet.t + 5)  # one sample after the ACK
    incident = store.incidents.find_one()
    assert incident["state"] == "mitigating"
    command = store.commands.find_one({"type": "reduce_load"})
    assert command["connector_id"] == "connector-4" and command["status"] == "applied"
    run = store.runs.find_one()
    assert set(run["sim"]) == {"connector-4"}
    assert run["sim"]["connector-4"]["current_limit_a"] == incident["target_current_a"]
    latest = {
        s["connector_id"]: s["current_limit_a"]
        for s in store.telemetry.find({"measured_at": T0 + timedelta(seconds=fleet.t - 5)})
    }
    assert latest.pop("connector-4") == incident["target_current_a"] < ORIGINAL_LIMIT_A
    assert set(latest.values()) == {ORIGINAL_LIMIT_A}
    # The rest of the loop on connector-4's own windows: mitigated -> maintenance -> retest -> resolved.
    fleet.run_while(lambda: store.incidents.find_one()["state"] != "mitigated", 900)
    fleet.command(incident["_id"], "maintenance")
    fleet.run_while(lambda: not store.incidents.find_one().get("maintenance_applied_at"), 1000)
    fleet.command(incident["_id"], "verify_recovery")
    fleet.run_while(lambda: store.incidents.find_one()["state"] != "resolved", 1500)
    incident = store.incidents.find_one()
    assert incident["state"] == "resolved"
    windows = {
        store.windows.find_one({"_id": e["window_id"]})["connector_id"]
        for e in incident["timeline"]
        if "window_id" in e
    }
    assert windows == {"connector-4"}
    assert store.runs.find_one()["sim"]["connector-4"]["current_limit_a"] == ORIGINAL_LIMIT_A
    assert store.incidents.count_documents({}) == 1


def test_inject_fault_event_consumed_exactly_once_across_restart(fleet, store):
    fleet.start("healthy", 5)
    fleet.run_until(20, worker=False)
    fleet.client.post(
        f"/api/v1/demo-runs/{RUN}/inject-fault", json={"connector_id": "connector-2"}, headers=OPERATOR
    )
    fleet.run_until(25, worker=False)
    event = store.run_events.find_one()
    consumed = event["consumed_at"]
    assert consumed == T0 + timedelta(seconds=25)
    injected = store.runs.find_one()["sim"]["connector-2"]["fault_injected_at"]
    assert injected == consumed
    fleet.simulator = Simulator(fleet.sim_client)  # restart: no local memory of the event
    fleet.run_until(60, worker=False)
    assert fleet.sim_client.get("/api/v1/simulator/events").json() == []
    assert store.run_events.find_one()["consumed_at"] == consumed
    assert store.runs.find_one()["sim"]["connector-2"]["fault_injected_at"] == injected
    assert "fault_cleared" not in store.runs.find_one()["sim"]["connector-2"]  # seeded marker untouched
    # A retried consume (lost response) is a duplicate and changes nothing.
    again = fleet.sim_client.post(f"/api/v1/simulator/events/{event['_id']}/consume")
    assert again.json() == {"status": "duplicate"}
    assert store.runs.find_one()["sim"]["connector-2"]["fault_injected_at"] == injected


def test_simulator_restart_resumes_each_connector_temperature(fleet, store):
    fleet.start("healthy", 5)
    fleet.run_until(100, worker=False)
    before = {
        s["connector_id"]: s for s in store.telemetry.find({"measured_at": T0 + timedelta(seconds=100)})
    }
    assert "last_samples" not in fleet.sim_client.get("/api/v1/simulator/run").json()  # only on resume
    resumed = fleet.sim_client.get("/api/v1/simulator/run", params={"resume": "true"}).json()
    assert set(resumed["last_samples"]) == set(CONNECTORS)
    fleet.simulator = Simulator(fleet.sim_client)
    fleet.run_until(105, worker=False)
    after = {s["connector_id"]: s for s in store.telemetry.find({"measured_at": T0 + timedelta(seconds=105)})}
    for cid in CONNECTORS:  # continues from the stored sample, not from ambient
        assert abs(after[cid]["connector_temperature_c"] - before[cid]["connector_temperature_c"]) < 3


@pytest.mark.parametrize("scenario", ["healthy", "load_increase", "demand_drop"])
def test_healthy_scenarios_open_no_incident(fleet, store, scenario):
    fleet.start(scenario, 9)
    plan = fleet_plan(9, scenario)
    if scenario != "healthy":
        assert all(p.change_s is not None for p in plan.values())
    fleet.run_until(240)
    assert store.windows.count_documents({"quality": True}) >= 6 * 10
    assert fleet.incidents() == []


def test_seeded_thermal_fault_opens_incident_on_planned_connector_only(fleet, store):
    seed = 4
    plan = fleet_plan(seed, "thermal_fault")
    faulty = next(c for c, p in plan.items() if p.fault_onset_s is not None)
    fleet.start("thermal_fault", seed)
    fleet.run_while(lambda: not fleet.incidents(), plan[faulty].fault_onset_s + 300)
    fleet.run_until(fleet.t + 30)  # no second incident elsewhere shortly after
    assert [i["connector_id"] for i in fleet.incidents()] == [faulty]


def test_webhook_for_connector_5_routes_to_connector_5(fleet, store):
    fleet.start("healthy", 5, mode="datadog")
    at = T0 + timedelta(minutes=5)
    for cid, residual in (("connector-1", 12.0), ("connector-5", 12.0)):
        samples = [
            {
                "connector_id": cid,
                "measured_at": at - timedelta(seconds=60 - 5 * i),
                "residual": residual if cid == "connector-5" else 1.0,
                "current_a": 100.0,
            }
            for i in range(12)
        ]
        evidence = window_evidence(samples, at)
        evidence.update(_id=f"{RUN}:{cid}:{at.isoformat()}", run_id=RUN, connector_id=cid)
        store.windows.insert_one(evidence)
    body = {
        "event_id": "dd-5",
        "monitor_id": "123",
        "run_id": RUN,
        "connector_id": "connector-5",
        "occurred_at": at.isoformat(),
        "status": "alert",
    }
    assert fleet.client.post("/api/v1/webhooks/datadog", json=body, headers=WEBHOOK).status_code == 202
    outcome = process_event(store, store.inbox.find_one(), at + timedelta(seconds=2), "123")
    assert outcome == "incident_opened"
    incident = store.incidents.find_one()
    assert incident["connector_id"] == "connector-5"
    assert incident["evidence"]["connector_id"] == "connector-5"
    # The hot window of connector-5 is never evidence for connector-1.
    body.update(event_id="dd-1", connector_id="connector-1")
    fleet.client.post("/api/v1/webhooks/datadog", json=body, headers=WEBHOOK)
    outcome = process_event(store, store.inbox.find_one({"_id": "dd-1"}), at + timedelta(seconds=3), "123")
    assert outcome == "insufficient_alert_evidence"
    assert store.incidents.count_documents({}) == 1


def test_datadog_metrics_tagged_per_connector_and_station(fleet, store):
    fleet.start("healthy", 5, mode="datadog")
    fleet.run_until(80)
    tags = {tuple(call.kwargs["tags"]) for call in fleet.stats.gauge.call_args_list}
    assert {(t[1], t[2]) for t in tags} == {
        (f"connector_id:{c}", f"station_id:{LOCATION[c][0]}") for c in CONNECTORS
    }
    assert all(t[0] == f"run_id:{RUN}" for t in tags)
    names = {call.args[0] for call in fleet.stats.gauge.call_args_list}
    assert "chargeops.incident.open" in names


def test_local_mode_emits_no_incident_signals(fleet, store):
    fleet.start("healthy", 3, mode="local")
    fleet.run_until(120, worker=True)
    names = {call.args[0] for call in fleet.stats.gauge.call_args_list}
    assert "chargeops.incident.open" not in names
    fleet.stats.event.assert_not_called()


def test_inject_fault_accepts_every_connector_of_the_run(fleet, store):
    fleet.start("healthy", 5)
    for cid in CONNECTORS:
        response = fleet.client.post(
            f"/api/v1/demo-runs/{RUN}/inject-fault", json={"connector_id": cid}, headers=OPERATOR
        )
        assert response.status_code == 202
    bad = fleet.client.post(
        f"/api/v1/demo-runs/{RUN}/inject-fault", json={"connector_id": "connector-7"}, headers=OPERATOR
    )
    assert bad.status_code == 422


def test_demo_run_accepts_all_four_scenarios(fleet):
    for scenario in ("healthy", "load_increase", "demand_drop", "thermal_fault"):
        response = fleet.client.post(
            "/api/v1/demo-runs", json={"scenario": scenario, "seed": 3, "mode": "local"}, headers=OPERATOR
        )
        assert response.status_code == 201 and response.json()["scenario"] == scenario


def test_connectors_lists_the_whole_fleet(fleet):
    fleet.start("healthy", 5)
    fleet.run_until(5, worker=False)
    items = fleet.client.get("/api/v1/connectors").json()["items"]
    assert [(c["connector_id"], c["station_id"], c["charger_id"]) for c in items] == [
        (c, *LOCATION[c]) for c in CONNECTORS
    ]


def test_injection_never_revives_a_cleared_seeded_fault():
    from dataclasses import replace

    plan = next(p for p in fleet_plan(4, "thermal_fault").values() if p.fault_onset_s is not None)
    onset = plan.fault_onset_s
    healthy = replace(plan, fault_onset_s=None, fault_severity_c=None)
    at = onset + 400

    def heat(p, **sim):
        return physics(p, at, sim)[4]

    baseline, injected_only = heat(healthy), heat(healthy, fault_injected_s=onset + 200)
    assert heat(plan) > baseline and injected_only > baseline
    cleared = {"fault_cleared": True, "fault_cleared_s": onset + 100}
    assert heat(plan, **cleared) == baseline
    # Injected after maintenance: only the injected fault heats; the seeded one stays cleared.
    assert heat(plan, **cleared, fault_injected_s=onset + 200) == injected_only
    # Maintenance after the injection clears the injected fault too.
    assert heat(healthy, **cleared, fault_injected_s=onset + 50) == baseline


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/api/v1/simulator/events"), ("post", "/api/v1/simulator/events/any/consume")],
)
def test_simulator_event_endpoints_need_the_simulator_secret(fleet, method, path):
    assert getattr(fleet.client, method)(path).status_code == 401
    wrong = {"X-Simulator-Secret": "x" * 32}
    assert getattr(fleet.client, method)(path, headers=wrong).status_code == 401


def test_check_mongo_core_path_runs_on_the_fleet_scheme(store):
    from chargeops.check_mongo import check_loop

    check_loop(store)  # connector-1
    store.runs.delete_many({})  # frees the single active slot for a second smoke run
    check_loop(store, "connector-6")
    assert store.incidents.count_documents({"connector_id": {"$in": ["connector-1", "connector-6"]}}) == 2


def test_fleet_window_rows_equals_per_connector_window_rows(fleet, store):
    """The worker's single fleet aggregation must give each connector exactly what window_rows gives it,
    including the first-arrival dedup of a poisoned duplicate and a sample_id shared across connectors."""
    from chargeops.storage import fleet_window_rows, window_rows

    fleet.start("thermal_fault", seed=11)
    fleet.run_until(180, worker=False)
    end = T0 + timedelta(seconds=180)
    original = store.telemetry.find_one(
        {"meta.connector_id": "connector-2", "measured_at": end - timedelta(seconds=20)}
    )
    poisoned = {k: v for k, v in original.items() if k != "_id"}
    poisoned.update(connector_temperature_c=999.0, received_at=original["received_at"] + timedelta(seconds=30))
    store.telemetry.insert_one(poisoned)  # a later replay of the same sample_id must not win
    shared = {k: v for k, v in original.items() if k != "_id"}
    shared.update(connector_id="connector-5", meta={**original["meta"], "connector_id": "connector-5"})
    store.telemetry.insert_one(shared)  # same sample_id on another connector is a different sample

    fleet_rows = fleet_window_rows(store, RUN, end)
    assert set(fleet_rows) == set(CONNECTORS)
    for cid in CONNECTORS:
        assert fleet_rows[cid] == window_rows(store, RUN, end, cid), cid
    temps = [r["connector_temperature_c"] for r in fleet_rows["connector-2"]]
    assert 999.0 not in temps and len(temps) == 12
    assert sum(r["sample_id"] == original["sample_id"] for r in fleet_rows["connector-5"]) == 1
