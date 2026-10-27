"""Pure thermal/load model: simulator parity, deterministic exogenous draws, trip/restart plant."""

from dataclasses import replace

import pytest

from chargeops import model, simulator
from chargeops.domain import THERMAL_TAU_S, lag
from chargeops.model import Connector, draw_exogenous, response

RANGES = {
    "ambient_c": (18.0, 30.0),
    "drift_c": (0.0, 1.5),
    "k_scale": (0.95, 1.05),
    "tau_scale": (0.9, 1.1),
    "change_s": (300, 1200),
    "load_factor": (1.2, 1.6),
    "load_cap_a": 200.0,
    "drop_factor": (0.2, 0.5),
    "onset_s": (300, 900),
    "severity_c": (10.0, 80.0),
    "ramp_s": (60.0, 600.0),
    "noise_c": 0.3,
}


def test_simulator_uses_model_physics_unchanged():
    assert simulator.PROFILES is model.PROFILES  # the live fleet's physics is chargeops.fleet.physics
    assert simulator.replay is model.replay and simulator.DEMO_PROFILE == model.DEMO_PROFILE
    profile = model.PROFILES[model.DEMO_PROFILE]
    # Golden values captured from the inline simulator physics before extraction.
    assert model.physics(0, "healthy", {}, profile) == (130.5, 130.5, 250.0, 303.0, 47.139325)
    assert model.physics(150, "thermal_fault", {}, profile) == (
        129.54500000000002,
        129.54500000000002,
        250.0,
        306.8,
        50.919037819000124,
    )
    assert model.physics(400, "thermal_fault", {"current_limit_a": 65}, profile) == (
        129.8,
        65,
        65,
        308.5,
        33.09281564830232,
    )
    assert model.physics(700, "thermal_fault", {"fault_cleared": True}, profile)[4] == 46.902452000000004
    assert model.physics(6000, "healthy", {}, profile) == (0.0, 0.0, 250.0, 337.4, 25.0)


def test_response_matches_product_lag_at_nominal_tau():
    for temperature, steady, dt in [(25.0, 60.0, 5), (80.0, 30.0, 5), (40.0, 40.0, 17)]:
        assert response(temperature, steady, dt, THERMAL_TAU_S) == lag(temperature, steady, dt)


@pytest.mark.parametrize("scenario", ["healthy", "load_increase", "demand_drop", "thermal_fault"])
def test_same_seed_same_exogenous_series(scenario):
    a = draw_exogenous(7, scenario, 1800, RANGES)
    b = draw_exogenous(7, scenario, 1800, RANGES)
    assert a == b and len(a.demand_a) == len(a.noise_c) == len(a.ambient_c) == 360
    assert draw_exogenous(8, scenario, 1800, RANGES) != a
    assert (a.fault is not None) == (scenario == "thermal_fault")


def test_scenarios_share_draws_and_only_change_demand_or_fault():
    healthy = draw_exogenous(11, "healthy", 1800, RANGES)
    for scenario in ["load_increase", "demand_drop", "thermal_fault"]:
        other = draw_exogenous(11, scenario, 1800, RANGES)
        assert other.profile_id == healthy.profile_id and other.noise_c == healthy.noise_c
        assert other.ambient_c == healthy.ambient_c and other.k == healthy.k
    change = draw_exogenous(11, "load_increase", 1800, RANGES)
    drop = draw_exogenous(11, "demand_drop", 1800, RANGES)
    assert all(x >= h for x, h in zip(change.demand_a, healthy.demand_a, strict=True))
    assert all(x <= h for x, h in zip(drop.demand_a, healthy.demand_a, strict=True))
    assert max(c - h for c, h in zip(change.demand_a, healthy.demand_a, strict=True)) > 0


def test_actions_do_not_shift_exogenous_series():
    exo = draw_exogenous(5, "thermal_fault", 1800, RANGES)
    plain, acted = Connector(exo, 100.0, 60.0, 300), Connector(exo, 100.0, 60.0, 300)
    for i in range(len(exo.demand_a)):
        if i == 100:
            acted.apply({"current_limit_a": 20.0})
        if i == 200:
            acted.apply({"fault_cleared": True})
        (s0, t0), (s1, t1) = plain.step(i), acted.step(i)
        for key in ["requested_current_a", "voltage_v", "ambient_temperature_c", "measured_at"]:
            assert s0[key] == s1[key]
        assert s0["connector_temperature_c"] - t0["temperature_c"] == pytest.approx(
            s1["connector_temperature_c"] - t1["temperature_c"]
        )
    assert exo == draw_exogenous(5, "thermal_fault", 1800, RANGES)


def test_trip_cuts_current_until_lockout_and_cooldown():
    exo = replace(draw_exogenous(3, "healthy", 1800, RANGES), demand_a=(200.0,) * 360)
    hottest = max(exo.ambient_c)
    plant = Connector(exo, trip_c=hottest + 20, restart_c=hottest + 2, lockout_s=300)
    steps = [plant.step(i) for i in range(200)]
    first = plant.trips[0]
    assert all(s["current_a"] == 0 and t["tripped"] for s, t in steps if first < t["t"] < first + 300)
    assert any(s["current_a"] > 0 for s, t in steps if t["t"] >= first + 300)
    assert "temperature_c" not in steps[0][0] and "fault_heat_c" not in steps[0][0]
    assert len(plant.trips) >= 2  # restarted into the same overload, tripped again
