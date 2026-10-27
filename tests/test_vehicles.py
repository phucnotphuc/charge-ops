import pytest

from chargeops.model import PROFILES
from chargeops.vehicles import DEFAULT_CAPACITY_KWH, SOC_PROFILES, SocProfile, minutes_to_full, soc_percent

P = SocProfile(soc_start_percent=30.0, soc_end_percent=80.0, energy_kwh=30.0, capacity_kwh=60.0)


def test_every_replayed_session_has_a_soc_row():
    assert set(SOC_PROFILES) == set(PROFILES)
    assert all(0 <= p.soc_start_percent <= 100 for p in SOC_PROFILES.values())
    assert all(p.soc_start_minute >= 0 for p in SOC_PROFILES.values())


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
