// Backend shapes derive from schema.d.ts (npm run gen:api, from web/openapi.json).
// Types marked HAND-WRITTEN cover endpoints without a response model; docs/CONTRACT.md §1-2 is their source.
// Missing values are null, never 0.
import type { components } from "./schema";

type Schemas = components["schemas"];

export type Role = "viewer" | "operator";
export type RunMode = "local" | "datadog";
export type Scenario = Schemas["DemoRunCreate"]["scenario"];

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

export type Me = Schemas["UserOut"];

export interface Provenance {
  composition: "synthetic" | "public_replay" | "hybrid";
  manifest_id: string;
}

/** SampleOut plus optional fields the contract allows but the API does not yet return. */
export type TelemetrySample = Omit<Schemas["SampleOut"], "provenance"> & {
  provenance?: Provenance | null;
  session_id?: string | null;
  connector_status?: "available" | "charging" | "faulted" | "offline" | null;
  session_status?: "idle" | "active" | "completed" | "interrupted" | null;
};

/** GET /connectors and GET /connectors/{id}: latest telemetry, server-computed freshness (null: no telemetry yet), `stale` verdict (>30 s or none), active incident, mode. */
export type Connector = Omit<Schemas["ConnectorOut"], "latest" | "active_incident" | "run_id"> & {
  /** Always present in responses; null when no run has produced telemetry. */
  run_id: string | null;
  latest?: TelemetrySample | null;
  active_incident?: { id: string; state: IncidentState } | null;
};

/** GET /connectors/{id}/telemetry item: measured series plus expected temperature. */
export type TelemetryPoint = Schemas["TelemetryItem"];

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

/** HAND-WRITTEN (no response model): incident snapshot (worker-owned fields). Ids arrive as `id`; the current backend still sends `_id`. */
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
  /** Only residual_not_recovered unlocks maintenance while still mitigating. */
  reduction_check_reason?: "load_not_reduced" | "insufficient_data" | "residual_not_recovered" | null;
  maintenance_applied_at?: string | null;
  mitigated_at?: string | null;
  timeline: TimelineEvent[];
  commands?: Command[];
  command_ids?: string[];
}

/** HAND-WRITTEN (no response model): command document. */
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

/** HAND-WRITTEN (no response model): 202 body of POST /incidents/{id}/commands. */
export interface CommandAccepted {
  command_id: string;
  status: CommandStatus;
}

export type DemoRunOut = Schemas["DemoRunOut"];

/**
 * GET /demo-runs returns raw Mongo docs (`_id`, no response model): HAND-WRITTEN widening of DemoRunOut.
 * GET/POST /demo-runs/{id}... return DemoRunOut with `id`. Use idOf().
 */
export type DemoRun = Omit<DemoRunOut, "id" | "created_at"> & { id?: string; _id?: string; created_at?: string };

/** GET /evaluations: committed offline summaries (simulated clock, never live). */
export type EvaluationItem = Schemas["EvaluationItem"];
export type EvaluationList = Schemas["EvaluationList"];
/** GET /evaluations/{id}: A/B/C arms, detector-only view and internal targets. */
export type Evaluation = Schemas["EvaluationOut"];
export type EvaluationStats = Schemas["Stats"];

export type CreateRunBody = Schemas["DemoRunCreate"];

/** GET /stations/{id}: public, anonymous driver view. */
export type StationOut = Schemas["StationOut"];
export type StationConnector = Schemas["StationConnector"];
export type FleetOut = Schemas["FleetOut"];
export type TrendOut = Schemas["TrendOut"];
export type IncidentSummary = Schemas["IncidentSummary"];

export const idOf = (x: { id?: string; _id?: string }): string => x.id ?? x._id ?? "";
