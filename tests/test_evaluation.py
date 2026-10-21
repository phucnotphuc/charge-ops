"""Offline A/B/C evaluation: protocol invariants, product-rule parity, lock enforcement."""

import inspect
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
    # The worker hard-codes its alert threshold; fail loudly if it drifts from the evaluation copy.
    assert 'evidence["residual_mean"] > 8' in inspect.getsource(worker.tick)
    assert rules.ALERT_C == 8.0
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


def test_protocol_doc_matches_code():
    from pathlib import Path

    text = (Path(__file__).parent.parent / "docs" / "EVALUATION_PROTOCOL.md").read_text(encoding="utf-8")
    assert f"protocol_version: `{protocol.PROTOCOL_VERSION}`" in text
    for value in ["100 °C", "60 °C", "300 s", "30 s", "600 s", "1000–1019", "2000–2019", "2020–2039"]:
        assert value in text
