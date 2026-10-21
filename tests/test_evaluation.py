"""Offline A/B/C evaluation: protocol invariants, product-rule parity, lock enforcement."""

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from chargeops import domain, lifecycle, worker
from chargeops.evaluation import protocol, rules, runner, summary
from chargeops.evaluation.__main__ import choose_threshold, load_lock, main
from chargeops.model import EPOCH, PROFILES, Fault

CONFIG = {"a_threshold_c": 75.0, "c_alert_c": rules.ALERT_C}
FAULT_SEED = protocol.SEEDS["tuning"]["fault"][0]


def fault_exo(severity=None, demand=None, ramp=120.0):
    exo = protocol.exogenous(FAULT_SEED, "thermal_fault")
    if demand is not None:
        exo = replace(exo, demand_a=(demand,) * len(exo.demand_a))
    if severity is not None:  # onset 300 s (step 60)
        exo = replace(exo, fault=Fault(300, severity, ramp, exo.demand_a[60]))
    return exo


def outcome(exo, config=CONFIG):
    results = runner.run_seed(exo, config)
    records = {arm: runner.record(r) for arm, r in results.items()}
    return results, records, summary.seed_outcome(records, runner.detector_windows(results["B"], config))


def test_seed_splits_are_disjoint_and_sized():
    tuning = {s for seeds in protocol.SEEDS["tuning"].values() for s in seeds}
    evaluation = {s for seeds in protocol.SEEDS["evaluation"].values() for s in seeds}
    assert not tuning & evaluation and len(tuning) == len(evaluation) == 40
    kinds = [kind for _, kind, _ in protocol.scenarios("evaluation")]
    assert kinds.count("fault") == kinds.count("healthy") == 20
    assert {s for _, _, s in protocol.scenarios("evaluation")} == {
        "thermal_fault",
        "healthy",
        "load_increase",
        "demand_drop",
    }


def test_rules_pinned_to_product_constants():
    assert rules.target_current is domain.target_current
    assert domain.target_current(200, 60) == 30  # 200 A limit, 60 A load -> 30 A
    assert rules.RECOVERED_C == lifecycle.RECOVERED_C and rules.RETEST_S == lifecycle.RETEST_S
    assert rules.CURRENT_TOLERANCE == lifecycle.CURRENT_TOLERANCE and rules.LOAD_FLOOR == lifecycle.LOAD_FLOOR
    assert rules.REDUCTION_CHECK_S == lifecycle.REDUCTION_CHECK_S
    assert (
        rules.ALERT_C == domain.ALERT_RESIDUAL_C == 8.0 and worker.ALERT_RESIDUAL_C is domain.ALERT_RESIDUAL_C
    )
    assert rules.RECOVERED_C == lifecycle.RECOVERED_C == domain.RECOVERY_RESIDUAL_C == 3.0
    assert (
        rules.charging is lifecycle.charging and rules.maintenance_unlocked is lifecycle.maintenance_unlocked
    )
    assert protocol.SIM_APPLY_S < lifecycle.COMMAND_TIMEOUT_S  # no command ever times out in evaluation


def test_reduce_load_freezes_absolute_target_from_reference_window():
    life = rules.Lifecycle()
    end = EPOCH + timedelta(seconds=300)
    samples = [
        {"current_a": 60.0, "current_limit_a": 200.0, "requested_current_a": 60.0, "residual": 10.0}
        for _ in range(12)
    ]
    window = {"start": end - timedelta(seconds=60), "end": end, "quality": True, "residual_mean": 10.0}
    window["samples"] = samples
    life.alert(window, end, end, "residual")
    incident = life.unresolved()
    life.command(incident, "acknowledge", end, [window])
    command = life.command(incident, "reduce_load", end, [window])
    assert command["sim"] == {"current_limit_a": 30.0} and incident["target_current_a"] == 30.0
    assert incident["state"] == "mitigating"


def test_healthy_runs_cannot_trip_by_construction():
    """Hottest pre-registered healthy steady state stays below the trip temperature."""
    hottest_ambient = protocol.RANGES["ambient_c"][1] + protocol.RANGES["drift_c"][1]
    k = domain.THERMAL_K * protocol.RANGES["k_scale"][1]
    peak = max(max(a for a, _ in p) for p in PROFILES.values())
    peak = max(peak, protocol.RANGES["load_cap_a"])
    assert hottest_ambient + k * peak**2 < protocol.TRIP_C - 5


def test_actions_do_not_change_exogenous_series_across_arms():
    exo = fault_exo()
    results, records, _ = outcome(exo)
    assert records["C"]["commands"]  # C actually intervened
    for key in ["requested_current_a", "voltage_v", "ambient_temperature_c", "measured_at"]:
        series = {arm: [r[key] for r in res["rows"]] for arm, res in results.items()}
        assert series["A"] == series["B"] == series["C"]
    noise = {
        arm: [
            r["connector_temperature_c"] - x["temperature_c"]
            for r, x in zip(res["rows"], res["truths"], strict=True)
        ]
        for arm, res in results.items()
    }
    assert noise["B"] == pytest.approx(noise["C"]) and noise["A"] == pytest.approx(noise["B"])
    assert exo == fault_exo()  # runs never mutate the pre-drawn series


def test_b_never_issues_commands():
    for seed, _, scenario in protocol.scenarios("tuning")[::7]:
        result = runner.run_arm(protocol.exogenous(seed, scenario), "B", CONFIG)
        assert result["life"].commands == []
    _, records, out = outcome(fault_exo())
    assert records["B"]["incidents"] and records["B"]["commands"] == []
    assert out["arms"]["B"]["effective_mitigation_s"] is None


def test_a_and_c_share_operator_schedule():
    _, records, _ = outcome(fault_exo(), {"a_threshold_c": 30.0, "c_alert_c": rules.ALERT_C})
    for arm in ["A", "C"]:
        opened = records[arm]["incidents"][0]["opened_s"]
        commands = {c["type"]: c for c in records[arm]["commands"] if c["cycle"] == 0}
        assert commands["acknowledge"]["created_s"] - opened == protocol.OPERATOR_DELAY_S
        assert commands["reduce_load"]["created_s"] - opened == 2 * protocol.OPERATOR_DELAY_S
        if "maintenance" in commands:
            assert commands["maintenance"]["created_s"] - opened >= protocol.MAINTENANCE_AFTER_OPEN_S
    operator = runner.Operator()
    assert operator.delay.total_seconds() == protocol.OPERATOR_DELAY_S
    assert operator.maintenance.total_seconds() == protocol.MAINTENANCE_AFTER_OPEN_S


def test_c_detects_like_b_and_full_loop_resolves_mild_fault():
    exo = fault_exo(severity=11.0)
    _, records, out = outcome(exo)
    assert records["B"]["incidents"][0]["opened_s"] == records["C"]["incidents"][0]["opened_s"]
    c = out["arms"]["C"]
    assert c["final_state"] == "resolved" and c["duplicate_commands"] == 0
    assert c["effective_mitigation_s"] <= c["mitigated_s"] <= c["maintenance_s"] <= c["resolved_s"]
    assert out["detectors"]["residual"]["first_s"] == c["first_s"]
    timeline = records["C"]["incidents"][0]["timeline"]  # runs.jsonl keeps at and reason
    assert timeline[0]["at"].endswith("Z") and timeline[0]["t"] == c["first_s"]
    assert all("reason" in e for e in timeline)


def test_lead_time_undefined_when_b_does_not_trip():
    _, records, out = outcome(fault_exo(severity=11.0))
    assert records["B"]["trips_s"] == [] and out["b_trip_s"] is None
    c = out["arms"]["C"]
    assert c["detected_before_trip"] and c["latency_s"] is not None and c["lead_time_s"] is None
    agg = summary.aggregate(
        [out], {"a_threshold_c": 75.0, "c_alert_c": 8.0, "c_recovered_c": 3.0, "locked_at": "x"}
    )
    assert agg["b_tripped_seeds"] == 0 and agg["arms"]["C"]["fault"]["recall_before_trip"] == 1
    assert agg["arms"]["C"]["fault"]["lead_time_s"] == {"n": 0, "median": None, "min": None, "max": None}


def test_lead_time_measured_against_b_trip():
    _, records, out = outcome(fault_exo(severity=50.0, demand=180.0, ramp=600.0))
    trip = records["B"]["trips_s"][0]
    c = out["arms"]["C"]
    assert out["b_trip_s"] == trip and c["detected_before_trip"]
    assert c["lead_time_s"] == trip - c["first_s"] > 0
    assert records["C"]["trips_s"] == []  # derating prevented the trip in this constructed case


def test_choose_threshold_lowest_within_budget():
    grid = [x / 2 for x in range(60, 201)]
    assert choose_threshold([50.0, 60.0, 70.2, 80.0], grid, 1) == 70.5
    assert choose_threshold([50.0, 60.0, 70.2, 80.0], grid, 0) == 80.0
    assert choose_threshold([120.0, 130.0], grid, 0) is None


def test_run_refuses_without_valid_lock(tmp_path):
    lock = tmp_path / "lock.json"
    with pytest.raises(SystemExit, match="no locked config"):
        main(
            [
                "run",
                "--protocol",
                protocol.PROTOCOL_VERSION,
                "--lock",
                str(lock),
                "--out",
                str(tmp_path / "o"),
            ]
        )
    assert not (tmp_path / "o").exists()
    good = {
        "protocol_version": protocol.PROTOCOL_VERSION,
        "fingerprint": protocol.fingerprint(),
        "tuning_seeds": protocol.SEEDS["tuning"],
        "evaluation_seeds": protocol.SEEDS["evaluation"],
    }
    lock.write_text(json.dumps(good), encoding="utf-8")
    assert load_lock(lock, protocol.PROTOCOL_VERSION)
    with pytest.raises(SystemExit, match="does not match"):
        load_lock(lock, "b1-eval-v0")
    for bad, message in [
        ({"fingerprint": "0" * 64}, "changed after"),
        ({"tuning_seeds": {"fault": [2000]}}, "overlap"),
    ]:
        lock.write_text(json.dumps({**good, **bad}), encoding="utf-8")
        with pytest.raises(SystemExit, match=message):
            load_lock(lock, protocol.PROTOCOL_VERSION)


def vn(x):
    return f"{x:g}".replace(".", ",")


def test_protocol_doc_matches_code():
    from pathlib import Path

    text = (Path(__file__).parent.parent / "docs" / "EVALUATION_PROTOCOL.md").read_text(encoding="utf-8")
    p, r, seeds = protocol, protocol.RANGES, protocol.SEEDS
    expected = [
        f"protocol_version: `{p.PROTOCOL_VERSION}`",
        f"Mỗi lượt dài **{p.DURATION_S} s**",
        f"*thật* > **{p.TRIP_C:g} °C**",
        f"ít nhất **{p.LOCKOUT_S} s** kể từ trip *và* nhiệt thật ≤ **{p.RESTART_C:g} °C**",
        f"phản ứng sau **{p.OPERATOR_DELAY_S} s** mỗi bước",
        f"mở incident (chu kỳ hiện tại) + **{p.MAINTENANCE_AFTER_OPEN_S} s**",
        f"lượt poll tiếp theo (**{p.SIM_APPLY_S} s**",
        f"tối đa **{p.FALSE_ALERT_BUDGET} lượt khỏe có incident trên {len(seeds['tuning']['healthy'])} lượt khỏe tuning**",
        f"lưới {p.A_GRID[0]:.1f}–{p.A_GRID[-1]:.1f} °C bước {p.A_GRID[1] - p.A_GRID[0]:.1f} °C".replace(
            ".", ","
        ),
        f"Nền đều {vn(r['ambient_c'][0])}–{vn(r['ambient_c'][1])} °C",
        f"biên độ đều {vn(r['drift_c'][0])}–{vn(r['drift_c'][1])} °C",
        f"{vn(domain.THERMAL_K)} × đều({vn(r['k_scale'][0])}–{vn(r['k_scale'][1])})",
        f"{vn(domain.THERMAL_TAU_S)} s × đều({vn(r['tau_scale'][0])}–{vn(r['tau_scale'][1])})",
        f"Gauss σ {vn(r['noise_c'])} °C",
        f"thời điểm đều {r['change_s'][0]}–{r['change_s'][1]} s",
        f"min(nền × đều({vn(r['load_factor'][0])}–{vn(r['load_factor'][1])}), {vn(r['load_cap_a'])} A)",
        f"nền × đều({vn(r['drop_factor'][0])}–{vn(r['drop_factor'][1])})",
        f"Thời điểm bắt đầu: đều {r['onset_s'][0]}–{r['onset_s'][1]} s",
        f"đều {vn(r['severity_c'][0])}–{vn(r['severity_c'][1])} °C",
        f"Hằng số tăng: đều {vn(r['ramp_s'][0])}–{vn(r['ramp_s'][1])} s",
    ]
    for split, label in [("tuning", "Tuning"), ("evaluation", "Đánh giá")]:
        f, h = seeds[split]["fault"], seeds[split]["healthy"]
        expected.append(f"| {label} | {f[0]}–{f[-1]} | {h[0]}–{h[-1]} |")
    for scenario in ["healthy", "load_increase", "demand_drop"]:
        expected.append(f"{p.HEALTHY_MIX.count(scenario)} `{scenario}`")
    missing = [phrase for phrase in expected if phrase not in text]
    assert not missing


def test_detector_match_needs_rising_edge_after_onset():
    def w(end, on):
        return {"end_s": end, "fixed": on}

    active = [w(15, False), w(30, True), w(45, True), w(60, True)]
    assert summary.first_alert(active, "fixed", after=40) is None  # already alerting before onset
    again = [*active, w(75, False), w(90, True)]
    assert summary.first_alert(again, "fixed", after=40) == 90
    assert summary.first_alert(active, "fixed", after=0) == 30


def test_fingerprint_covers_sources_and_tune_never_overwrites(tmp_path, monkeypatch):
    assert set(protocol.definition()["sources_sha256"]) == set(protocol.SOURCES)
    (tmp_path / "a.py").write_bytes(b"x = 1\r\ny = 2\r\n")
    (tmp_path / "b.py").write_bytes(b"x = 1\ny = 2\n")
    monkeypatch.setattr(protocol, "PACKAGE", tmp_path)
    assert protocol.source_sha256("a.py") == protocol.source_sha256("b.py")  # CRLF and LF checkouts agree
    monkeypatch.undo()
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps({"protocol_version": protocol.PROTOCOL_VERSION}), encoding="utf-8")
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        main(["tune", "--lock", str(lock)])
    assert json.loads(lock.read_text(encoding="utf-8")) == {"protocol_version": protocol.PROTOCOL_VERSION}


# Parity: the same window/command sequence through lifecycle.py (mongomock) and rules.Lifecycle.
RUN = "00000000-0000-4000-8000-0000000000e1"
FIELDS = [
    "state",
    "state_since",
    "opened_at",
    "reference_current_a",
    "original_limit_a",
    "target_current_a",
    "load_reduction_verified_at",
    "reduction_check",
    "reduction_check_reason",
    "cycle",
    "mitigated_at",
    "maintenance_applied_at",
    "verifying_since",
    "retest_started_at",
    "retest_checked_until",
    "resolved_at",
]


class Twin:
    def __init__(self, store):
        self.db, self.life, self.windows, self.states = store, rules.Lifecycle(), [], []
        self.t = domain.now().replace(microsecond=0)  # MongoDB keeps milliseconds
        store.runs.insert_one({"_id": RUN, "status": "active", "mode": "local", "started_at": self.t})

    def at(self, seconds):
        return self.t + timedelta(seconds=seconds)

    def window(self, end, current=30.0, limit=30.0, residual=1.0, requested=60.0, quality=True):
        end = self.at(end)
        samples = [
            {
                "measured_at": end - timedelta(seconds=60 - 5 * i),
                "current_a": current,
                "current_limit_a": limit,
                "requested_current_a": requested,
                "residual": residual,
                "connector_temperature_c": 40.0 + residual,
            }
            for i in range(12)
        ]
        doc = {
            "_id": f"{RUN}:{end.isoformat()}",
            "run_id": RUN,
            "start": end - timedelta(seconds=60),
            "end": end,
            "quality": quality,
            "residual_mean": residual,
            "samples": samples,
        }
        self.db.windows.insert_one(json.loads(json.dumps(doc, default=str)) | doc)
        self.windows.append(doc)
        return self

    def alert(self, seconds):
        evidence, at = self.windows[-1], self.at(seconds)
        product = lifecycle.on_alert(
            self.db, {"_id": RUN}, evidence, "local", at, evidence["_id"], evidence["end"]
        )
        assert product == self.life.alert(evidence, at, evidence["end"], "local")
        return self.advance(seconds)

    def command(self, kind, seconds):
        at = self.at(seconds)
        incident = self.db.incidents.find_one({"unresolved": True})
        number = self.db.commands.count_documents({})
        self.db.commands.insert_one(
            {
                "_id": f"c{number}",
                "idempotency_key": f"k{number}",
                "incident_id": incident["_id"],
                "run_id": RUN,
                "type": kind,
                "status": "pending",
                "validated": False,
                "created_at": at,
            }
        )
        lifecycle.process_commands(self.db, at)
        mirror = self.life.command(self.life.unresolved(), kind, at, self.windows)
        product = self.db.commands.find_one({"_id": f"c{number}"})
        assert (product["status"], product.get("error"), product.get("sim")) == (
            mirror["status"],
            mirror.get("error"),
            mirror.get("sim"),
        )
        return self.advance(seconds)

    def apply(self, seconds):  # simulator applies every dispatched command
        at = self.at(seconds)
        for command in self.db.commands.find({"status": "pending", "dispatched_at": {"$ne": None}}):
            self.db.commands.update_one(
                {"_id": command["_id"]}, {"$set": {"status": "applied", "applied_at": at}}
            )
        for command in self.life.dispatched(at):
            command.update(status="applied", applied_at=at)
        return self.advance(seconds)

    def advance(self, seconds):
        at = self.at(seconds)
        lifecycle.advance_incidents(self.db, at)
        self.life.advance(at, self.windows)
        product = list(self.db.incidents.find().sort("opened_at", 1))
        assert len(product) == len(self.life.incidents)
        for p, m in zip(product, self.life.incidents, strict=True):
            assert {f: p.get(f) for f in FIELDS} == {f: m.get(f) for f in FIELDS}
            assert ("unresolved" in p) == m["unresolved"]
            assert [e["event"] for e in p["timeline"]] == [e["event"] for e in m["timeline"]]
        self.states.append(product[-1]["state"] if product else None)
        return self

    def final(self):
        return self.db.incidents.find().sort("opened_at", -1)[0]


def derated(twin, start=0):
    """Alert at a 60 A / 200 A reference window, acknowledge, reduce load to 30 A, simulator ACK."""
    twin.window(start + 60, current=60, limit=200, residual=10).alert(start + 62)
    return twin.command("acknowledge", start + 90).command("reduce_load", start + 120).apply(start + 125)


def retest_passes(twin, first_end, count=5):
    for end in range(first_end, first_end + 15 * count, 15):
        twin.window(end, current=60, limit=200, residual=1).advance(end + 1)
    return twin


def test_parity_full_loop_with_retest_reset(store):
    twin = derated(Twin(store))
    twin.window(195, residual=5).advance(196).window(210, residual=2).advance(211)
    twin.command("maintenance", 240).apply(245).command("verify_recovery", 275).apply(280)
    twin.window(345, current=60, limit=200, residual=1).advance(346)
    twin.window(360, current=60, limit=200, residual=4).advance(361)  # hot: retest resets
    retest_passes(twin, 375)
    assert twin.final()["state"] == "resolved"
    assert [e["reason"] for e in twin.final()["timeline"] if e["event"] == "retest_reset"] == ["hot"]
    assert ["open", "acknowledged", "mitigating", "mitigated", "verifying", "resolved"] == list(
        dict.fromkeys(twin.states)
    )


def hot_then_maintenance(twin, verified_s):
    """Still hot: unlock exactly REDUCTION_CHECK_S after verification, then maintenance to resolution."""
    hot = verified_s + lifecycle.REDUCTION_CHECK_S
    twin.window(hot - 5, residual=5).advance(hot - 1).command("maintenance", hot - 1)  # rejected: too early
    twin.advance(hot)
    assert twin.final()["reduction_check_reason"] == "residual_not_recovered"
    twin.command("maintenance", hot + 5).apply(hot + 10)
    assert twin.final()["state"] == "mitigating" and twin.final()["maintenance_applied_at"]
    twin.window(hot + 75, residual=1).advance(hot + 76).command("verify_recovery", hot + 80).apply(hot + 85)
    retest_passes(twin, hot + 150)
    assert twin.final()["state"] == "resolved"
    return twin


def test_parity_ineffective_derating_unlocks_maintenance(store):
    twin = derated(Twin(store))
    twin.window(195, residual=5).advance(196).command("maintenance", 200)  # rejected: not yet ineffective
    twin.window(240, residual=5).advance(245)  # 120 s after ACK: verified, so no check event yet
    assert twin.final().get("reduction_check") is None
    hot_then_maintenance(twin, 195)


def test_parity_load_not_reduced_then_verified_hot_unlocks(store):
    twin = derated(Twin(store))
    twin.window(195, current=60, limit=30, residual=5).advance(245)  # 120 s after ACK, load not reduced
    assert twin.final()["reduction_check_reason"] == "load_not_reduced"
    twin.command("maintenance", 250)  # rejected: load_not_reduced never unlocks
    twin.window(270, residual=5).advance(271)
    assert twin.final()["load_reduction_verified_at"] == twin.at(270)
    hot_then_maintenance(twin, 270)


def test_parity_unverified_then_verified_unlocks(store):
    twin = derated(Twin(store))
    twin.window(195, residual=5, quality=False).advance(245)
    assert twin.final()["reduction_check"] == "load_reduction_unverified"
    twin.command("maintenance", 250)  # rejected
    twin.window(270, residual=5).advance(271)
    hot_then_maintenance(twin, 270)


def test_parity_tripped_window_does_not_verify(store):
    twin = derated(Twin(store))
    twin.window(195, current=0.0, residual=1).advance(196)
    assert twin.final()["state"] == "mitigating" and not twin.final().get("load_reduction_verified_at")
    twin.window(210, residual=1).advance(211)
    assert twin.final()["state"] == "mitigated"


def test_parity_recurrence_during_retest(store):
    twin = derated(Twin(store))
    twin.window(195, residual=2).advance(196)
    twin.command("maintenance", 200).apply(205).command("verify_recovery", 210).apply(215)
    twin.window(285, current=60, limit=200, residual=10).alert(286)  # hot again under retest
    assert twin.final()["state"] == "open" and twin.life.incidents[0]["cycle"] == 1
    twin.command("acknowledge", 300).command("reduce_load", 310).apply(315)
    assert twin.final()["state"] == "mitigating" and twin.final()["target_current_a"] == 30.0
