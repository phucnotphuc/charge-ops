from datetime import timedelta

from fastapi.testclient import TestClient

from chargeops.api import app
from chargeops.station import public_status

SIM = {"X-Simulator-Secret": "s" * 32}
RUN = "00000000-0000-4000-8000-0000000000e1"


def connector(**extra):
    base = {
        "connector_id": "connector-1",
        "run_id": RUN,
        "stale": False,
        "active_incident": None,
        "latest": {
            "current_a": 120.0,
            "requested_current_a": 120.0,
            "current_limit_a": 250.0,
            "power_kw": 48.0,
            "connector_temperature_c": 41.0,
        },
    }
    return {**base, **extra}


def test_public_status_mapping():
    assert public_status(connector(stale=True), None) == "offline"
    assert public_status(connector(), {"soc_percent": 40}) == "charging"
    mitigating = connector(active_incident={"id": "i", "state": "mitigating"})
    assert public_status(mitigating, {"soc_percent": 40}) == "limited"
    verifying = connector(active_incident={"id": "i", "state": "verifying"})
    assert public_status(verifying, {"soc_percent": 40}) == "checking"
    idle = connector(latest={"current_a": 0.0, "requested_current_a": 0.0, "current_limit_a": 250.0})
    assert public_status(idle, None) == "idle"


def test_vehicle_ingest_needs_simulator_secret(store):
    body = {
        "vehicles": [
            {
                "run_id": RUN,
                "connector_id": "connector-1",
                "measured_at": "2026-10-25T03:00:00Z",
                "soc_percent": 41.5,
                "energy_kwh": 6.9,
                "minutes_to_full": 70.0,
                "capacity_source": "autosun",
            }
        ]
    }
    assert TestClient(app).post("/api/v1/simulator/vehicles", json=body).status_code == 401
    assert TestClient(app, headers=SIM).post("/api/v1/simulator/vehicles", json=body).status_code == 202
    assert store.vehicle_states.find_one({"_id": f"{RUN}:connector-1"})["soc_percent"] == 41.5


def test_station_display_hides_operational_fields(fleet):
    # Healthy run for 60 s through the simulator, so telemetry and vehicle states exist; read anonymously.
    fleet.start("healthy", seed=3)
    fleet.run_until(60, worker=True)
    body = TestClient(app).get("/api/v1/stations/station-2").json()
    assert [c["connector_id"] for c in body["connectors"]] == ["connector-4", "connector-5", "connector-6"]
    text = str(body)
    for forbidden in ("residual", "incident", "command", "threshold", "run_id"):
        assert forbidden not in text
    assert all(c["soc_percent"] is not None for c in body["connectors"])
    assert all(c["status"] == "charging" for c in body["connectors"])


def test_station_without_any_run_shows_offline_connectors(store):
    body = TestClient(app).get("/api/v1/stations/station-1").json()
    assert [(c["connector_id"], c["status"]) for c in body["connectors"]] == [
        ("connector-1", "offline"),
        ("connector-2", "offline"),
        ("connector-3", "offline"),
    ]


def test_unknown_station_is_422(store):
    assert TestClient(app).get("/api/v1/stations/station-9").status_code == 422


def test_public_status_limits_and_edge_cases():
    def incident(name):
        return {"active_incident": {"id": "i", "state": name}}

    vehicle = {"soc_percent": 40}
    assert public_status(connector(**incident("mitigated")), vehicle) == "limited"
    derated = {"current_a": 90.0, "requested_current_a": 120.0, "current_limit_a": 90.0}
    assert public_status(connector(latest=derated), vehicle) == "limited"  # no incident needed
    stalled = {"current_a": 0.5, "requested_current_a": 120.0, "current_limit_a": 90.0}
    assert public_status(connector(latest=stalled), vehicle) == "checking"
    assert public_status(connector(**incident("mitigating"), latest=stalled), vehicle) == "checking"
    assert public_status(connector(), None) == "idle"  # current flows but no vehicle state yet


def test_offline_connector_has_null_vehicle_fields(fleet):
    fleet.start("healthy", seed=3)
    fleet.run_until(60, worker=False)
    fresh = TestClient(app).get("/api/v1/stations/station-1").json()["connectors"][0]
    assert fresh["status"] == "charging" and fresh["soc_percent"] is not None
    fleet.clock[0] += timedelta(minutes=10)  # telemetry stops: the connector goes stale
    stale = TestClient(app).get("/api/v1/stations/station-1").json()["connectors"]
    assert {c["status"] for c in stale} == {"offline"}
    for c in stale:
        assert c["soc_percent"] is None and c["energy_kwh"] is None and c["minutes_to_full"] is None
