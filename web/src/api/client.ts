import type {
  ApiErrorBody,
  Command,
  CommandAccepted,
  CommandType,
  Connector,
  CreateRunBody,
  DemoRun,
  Evaluation,
  EvaluationList,
  FleetOut,
  Incident,
  IncidentSummary,
  Me,
  Page,
  StationOut,
  TelemetryPoint,
  TrendOut,
} from "./types";

const BASE = "/api/v1";
export const TELEMETRY_PAGE_LIMIT = 1000;
export const MAX_TELEMETRY_PAGES = 20;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId: string | null,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(BASE + path, {
      ...init,
      credentials: "include",
      headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers },
    });
  } catch {
    throw new ApiError(0, "network_error", "Không kết nối được tới máy chủ API.", null);
  }
  if (!response.ok) {
    let body: Partial<ApiErrorBody> | null = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    const error = body?.error;
    // The pre-contract backend returns {detail}/{error: string}; show it rather than hide it.
    const legacy = body as { detail?: unknown; error?: unknown } | null;
    const message =
      error && typeof error === "object"
        ? error.message
        : typeof legacy?.error === "string"
          ? legacy.error
          : typeof legacy?.detail === "string"
            ? legacy.detail
            : `Yêu cầu thất bại (HTTP ${response.status}).`;
    throw new ApiError(
      response.status,
      error && typeof error === "object" ? error.code : `http_${response.status}`,
      message,
      error && typeof error === "object" ? error.request_id : null,
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const post = <T>(path: string, body?: unknown, headers?: Record<string, string>) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body), headers });

export const api = {
  me: () => request<Me>("/auth/me"),
  login: (username: string, password: string) => post<Me>("/auth/login", { username, password }),
  logout: () => post<void>("/auth/logout"),

  connectors: () => request<Page<Connector>>("/connectors"),
  connector: (id: string) => request<Connector>(`/connectors/${encodeURIComponent(id)}`),
  telemetryPage: (id: string, runId: string | null, from: string, to: string, cursor: string | null) => {
    const query = new URLSearchParams({
      from,
      to,
      limit: String(TELEMETRY_PAGE_LIMIT),
      ...(runId ? { run_id: runId } : {}),
      ...(cursor ? { cursor } : {}),
    });
    return request<Page<TelemetryPoint>>(`/connectors/${encodeURIComponent(id)}/telemetry?${query}`);
  },
  /** Follows next_cursor up to MAX_TELEMETRY_PAGES; `truncated` is true when the cap cut the series short. */
  connectorTelemetry: async (id: string, runId: string | null, from: string, to: string) => {
    const items: TelemetryPoint[] = [];
    let cursor: string | null = null;
    let pages = 0;
    do {
      const page: Page<TelemetryPoint> = await api.telemetryPage(id, runId, from, to, cursor);
      items.push(...page.items);
      cursor = page.next_cursor;
      pages += 1;
    } while (cursor && pages < MAX_TELEMETRY_PAGES);
    return { items, truncated: cursor !== null };
  },

  incidents: () => request<Page<Incident>>("/incidents"),
  incident: (id: string) => request<Incident>(`/incidents/${encodeURIComponent(id)}`),
  command: (id: string) => request<Command>(`/commands/${encodeURIComponent(id)}`),
  /** One Idempotency-Key per click; the caller generates it. */
  sendCommand: (incidentId: string, type: CommandType, idempotencyKey: string) =>
    post<CommandAccepted>(`/incidents/${encodeURIComponent(incidentId)}/commands`, { type }, {
      "Idempotency-Key": idempotencyKey,
    }),

  runs: () => request<Page<DemoRun>>("/demo-runs"),
  createRun: (body: CreateRunBody) => post<DemoRun>("/demo-runs", body),
  startRun: (id: string) => post<DemoRun>(`/demo-runs/${encodeURIComponent(id)}/start`),
  stopRun: (id: string) => post<DemoRun>(`/demo-runs/${encodeURIComponent(id)}/stop`),
  injectFault: (id: string, connectorId: string) =>
    post<unknown>(`/demo-runs/${encodeURIComponent(id)}/inject-fault`, { connector_id: connectorId }),

  station: (id: string) => request<StationOut>(`/stations/${encodeURIComponent(id)}`),
  fleetInsights: (runId: string) => request<FleetOut>(`/insights/fleet?${new URLSearchParams({ run_id: runId })}`),
  connectorTrend: (id: string, runId: string) =>
    request<TrendOut>(`/connectors/${encodeURIComponent(id)}/trend?${new URLSearchParams({ run_id: runId })}`),
  incidentSummary: (runId: string | null, stationId?: string) => {
    const query = new URLSearchParams({ ...(runId ? { run_id: runId } : {}), ...(stationId ? { station_id: stationId } : {}) });
    return request<IncidentSummary>(`/insights/incidents${query.size ? `?${query}` : ""}`);
  },

  evaluations: () => request<EvaluationList>("/evaluations"),
  evaluation: (id: string) => request<Evaluation>(`/evaluations/${encodeURIComponent(id)}`),
};

export const newIdempotencyKey = (): string => crypto.randomUUID();
