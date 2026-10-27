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
