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
    soc_start_minute: int = 0  # first minute with a BMS SoC reading; delivered energy counts from here


def load_soc_profiles():
    path = Path(__file__).parent / "profiles" / "autosun_v3_soc.csv"
    with path.open(encoding="utf-8") as f:
        return {
            row["transaction_id"]: SocProfile(
                float(row["soc_start_percent"]),
                float(row["soc_end_percent"]),
                float(row["energy_kwh"]),
                float(row["capacity_kwh"]) if row["capacity_kwh"] else None,
                int(row["soc_start_minute"]),
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
