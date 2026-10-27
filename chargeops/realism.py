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
