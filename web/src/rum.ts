import { datadogRum } from "@datadog/browser-rum";

type Env = Record<string, string | boolean | undefined>;

let enabled = false;
const seenAt = new Map<string, number>();
let lastSeen: string | null = null;

const str = (v: unknown): string => (typeof v === "string" ? v.trim() : "");

/** Starts Datadog Browser RUM only when the build-time application id and public client token are set. */
export function initRum(env: Env = import.meta.env): boolean {
  if (enabled) return true;
  const applicationId = str(env.VITE_DD_RUM_APPLICATION_ID);
  const clientToken = str(env.VITE_DD_RUM_CLIENT_TOKEN);
  if (!applicationId || !clientToken) return false;
  datadogRum.init({
    applicationId,
    clientToken,
    site: str(env.VITE_DD_SITE) || "datadoghq.com",
    service: "chargeops-web",
    env: str(env.VITE_DD_ENV) || "hackathon",
    version: str(env.VITE_DD_VERSION) || "0.1.0",
    sessionSampleRate: 100,
    sessionReplaySampleRate: 0,
    trackUserInteractions: true,
    defaultPrivacyLevel: "mask-user-input",
    allowedTracingUrls: [{ match: `${window.location.origin}/api/`, propagatorTypes: ["datadog", "tracecontext"] }],
  });
  enabled = true;
  return true;
}

/** Remembers when the operator first saw an incident (first call per id wins). */
export function markIncidentSeen(incidentId: string): void {
  if (!enabled) return;
  if (!seenAt.has(incidentId)) seenAt.set(incidentId, Date.now());
  lastSeen = incidentId;
}

/** Records an operator command as a RUM action and, once per incident, the seen-to-command timing. */
export function trackCommand(type: string, context: Record<string, unknown>): void {
  if (!enabled) return;
  const ctx: Record<string, unknown> = { type, ...context };
  const id = typeof context.incident_id === "string" ? context.incident_id : lastSeen;
  const started = id ? seenAt.get(id) : undefined;
  if (id && started !== undefined) {
    ctx.incident_seen_to_command_ms = Date.now() - started;
    datadogRum.addTiming("incident_seen_to_command_ms");
    seenAt.delete(id);
  }
  datadogRum.addAction("command_sent", ctx);
}
