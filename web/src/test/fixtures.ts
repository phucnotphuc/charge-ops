// Test-only fixtures. Never imported by application code.
import type { Connector, Incident } from "../api/types";

export const incident = (over: Partial<Incident> = {}): Incident => ({
  id: "inc-1",
  run_id: "run-1",
  connector_id: "S1-C1-K1",
  kind: "thermal_anomaly",
  state: "acknowledged",
  state_since: "2026-10-20T03:00:00Z",
  source: "datadog",
  opened_at: "2026-10-20T02:58:00Z",
  evidence: { residual_mean: 9.4, end: "2026-10-20T02:57:00Z" },
  reference_current_a: 60,
  original_limit_a: 200,
  target_current_a: null,
  timeline: [
    { at: "2026-10-20T02:58:00Z", event: "opened", source: "datadog" },
    { at: "2026-10-20T03:00:00Z", event: "acknowledge" },
  ],
  commands: [],
  ...over,
});

export const connector = (over: Partial<Connector> = {}): Connector => ({
  connector_id: "S1-C1-K1",
  station_id: "S1",
  charger_id: "S1-C1",
  run_id: "run-1",
  mode: "local",
  freshness_seconds: 4,
  active_incident_id: null,
  latest: {
    sample_id: "s1",
    run_id: "run-1",
    station_id: "S1",
    charger_id: "S1-C1",
    connector_id: "S1-C1-K1",
    session_id: null,
    measured_at: "2026-10-20T03:00:00Z",
    received_at: "2026-10-20T03:00:01Z",
    voltage_v: 398.2,
    current_a: 61.5,
    power_kw: 24.3,
    connector_temperature_c: 47.1,
    ambient_temperature_c: 29.4,
    requested_current_a: 62,
    current_limit_a: 200,
    connector_status: "charging",
    session_status: "active",
    provenance: { composition: "hybrid", manifest_id: "m-1" },
  },
  ...over,
});
