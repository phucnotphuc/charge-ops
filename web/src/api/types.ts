// TODO(integration): replace with openapi-typescript output from /openapi.json
// Hand-written from docs/CONTRACT.md §1-2. Missing values are null, never 0.

export type Role = "viewer" | "operator";
export type RunMode = "local" | "datadog";
export type Scenario = "healthy" | "thermal_fault";

export type IncidentState =
  | "open"
  | "acknowledged"
  | "mitigating"
  | "mitigated"
  | "verifying"
  | "resolved";

export type CommandType = "acknowledge" | "reduce_load" | "maintenance" | "verify_recovery";
export type CommandStatus = "pending" | "claimed" | "applied" | "failed" | "timed_out";

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface ApiErrorBody {
  error: { code: string; message: string; request_id: string };
}

export interface Me {
  username: string;
  role: Role;
}

export interface Provenance {
  composition: "synthetic" | "public_replay" | "hybrid";
  manifest_id: string;
}

export interface TelemetrySample {
  sample_id: string;
  run_id: string;
  station_id: string | null;
  charger_id: string | null;
  connector_id: string;
  session_id?: string | null;
  measured_at: string;
  received_at: string | null;
  voltage_v: number | null;
  current_a: number | null;
  power_kw: number | null;
  connector_temperature_c: number | null;
  ambient_temperature_c: number | null;
  requested_current_a: number | null;
  current_limit_a: number | null;
  connector_status?: "available" | "charging" | "faulted" | "offline" | null;
  session_status?: "idle" | "active" | "completed" | "interrupted" | null;
  provenance?: Provenance | null;
}

/** GET /connectors and GET /connectors/{id}: latest telemetry, server-computed freshness, active incident, mode. */
export interface Connector {
  connector_id: string;
  station_id: string | null;
  charger_id: string | null;
  run_id: string | null;
  run_status: string | null;
  mode: RunMode | null;
  latest: TelemetrySample | null;
  measured_at: string | null;
  /** Seconds since the newest sample, computed by the server (null: no telemetry yet). */
  freshness_s: number | null;
  /** Server verdict: freshness > 30 s, or no telemetry. */
  stale: boolean;
  active_incident: { id: string; state: IncidentState } | null;
}

/** GET /connectors/{id}/telemetry item: measured series plus expected temperature. */
export interface TelemetryPoint {
  sample_id?: string;
  measured_at: string;
  connector_temperature_c: number | null;
  expected_temperature_c: number | null;
  residual: number | null;
  current_a: number | null;
  current_limit_a: number | null;
  ambient_temperature_c: number | null;
}

export interface TimelineEvent {
  at: string;
  event: string;
  [detail: string]: unknown;
}

export interface EvidenceWindow {
  window_id?: string;
  end?: string;
  residual_mean?: number | null;
}

/** Incident snapshot (worker-owned fields). Ids arrive as `id`; the current backend still sends `_id`. */
export interface Incident {
  id?: string;
  _id?: string;
  run_id: string;
  connector_id: string;
  kind: string;
  state: IncidentState;
  state_since: string;
  source: "local" | "datadog";
  opened_at: string;
  resolved_at?: string | null;
  evidence?: {
    residual_mean?: number | null;
    start?: string;
    end?: string;
    count?: number;
    quality?: boolean;
  } | null;
  evidence_before?: EvidenceWindow | null;
  evidence_after?: EvidenceWindow | null;
  reference_current_a?: number | null;
  original_limit_a?: number | null;
  target_current_a?: number | null;
  load_reduction_verified_at?: string | null;
  reduction_check?: "load_reduction_ineffective" | "load_reduction_unverified" | null;
  maintenance_applied_at?: string | null;
  mitigated_at?: string | null;
  timeline: TimelineEvent[];
  commands?: Command[];
  command_ids?: string[];
}

export interface Command {
  id?: string;
  _id?: string;
  incident_id?: string;
  type: CommandType;
  status: CommandStatus;
  created_at?: string;
  applied_at?: string | null;
  timed_out_at?: string | null;
  result?: unknown;
  error?: string | null;
}

export interface CommandAccepted {
  command_id: string;
  status: CommandStatus;
}

// NOTE: GET /demo-runs returns raw Mongo docs (`_id`); GET/POST /demo-runs/{id}... return `id`. Use idOf().
export interface DemoRun {
  id?: string;
  _id?: string;
  status: "created" | "pending" | "active" | "stopped" | "rejected" | "cancelled" | string;
  mode: RunMode;
  scenario: Scenario | string;
  seed?: number | null;
  created_at?: string;
  started_at?: string | null;
  stopped_at?: string | null;
  reason?: string | null;
  stop_requested?: boolean;
}

export interface Evaluation {
  id?: string;
  _id?: string;
  created_at?: string;
  seed?: number | null;
  baseline_version?: string | null;
  config_version?: string | null;
  summary?: string | null;
  metrics?: Record<string, number | string | null> | null;
}

export interface CreateRunBody {
  scenario: Scenario;
  seed: number;
  mode: RunMode;
}

export const idOf = (x: { id?: string; _id?: string }): string => x.id ?? x._id ?? "";
