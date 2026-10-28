# ChargeOps — Datadog-native operations (B3–B8) design

Date: 28/10/2026. Deadline 31/10/2026 23:59 ICT. Base branch: `feat/station-observability` (`0772e3b`).

## Goal

Make Datadog the place where an incident is **detected, investigated, acted on and closed**, with MongoDB as the system of record behind it. Detection stays where it is (residual monitor → webhook → worker); this adds what happens after detection inside Datadog.

## Scope (user-approved items, implementation order)

1. **B4 — Datadog Incident per ChargeOps incident.** When a ChargeOps incident opens, a Datadog Incident is created; every ChargeOps timeline step (acknowledge, reduce_load, load_reduction_verified, maintenance, mitigated, verifying, retest_reset, recurrence, resolved, run_stopped) is mirrored onto it; on `resolved` the Datadog Incident is resolved. The implementer must confirm against the current Datadog API docs which v2 endpoint carries timeline entries on this account; if there is no writable timeline endpoint, mirror steps as incident *todos*/attachments plus an updated description, and say so in the docs.
2. **B5 — Investigation Notebook per incident.** Created with the Datadog Incident; graphs (residual with 8/3 °C markers, measured vs expected temperature, current vs limit, incident events) scoped to the run and connector and to the incident's time span; a log query cell; a markdown cell with evidence from MongoDB. Linked from the Datadog Incident and from the ChargeOps incident page.
3. **B7 — Postmortem.** On `resolved` (or `run_stopped` while unresolved), a MongoDB `$merge` pipeline writes one `incident_reports` document (durations per stage, reference/target current, peak residual, energy delivered during derating vs. same-window baseline, number of retest resets). The Notebook gets a "Postmortem" markdown cell from it, and the Datadog Incident gets the summary.
4. **B6 — SLOs.** Two metric-based SLOs on new worker metrics: *"Giảm tải có tác dụng trong 5 phút"* (good = incidents whose `load_reduction_verified_at − opened_at ≤ 300 s`, total = incidents that reached reduce_load) and *"Phút sạc không gián đoạn"* (per 60-second quality window: total = windows whose latest sample has `requested_current_a > 0`; good = those whose latest sample also has `current_a > 0`). Shown on the engineering dashboard with error budget.
5. **B3 — Browser RUM.** `@datadog/browser-rum` in the web app; custom actions for every operator command and the time from "incident first seen in UI" to "command sent"; RUM ↔ APM trace linking for `/api/*` calls. Disabled unless RUM env values are set at build time.
6. **B8 — Act from Datadog.** A Datadog App (App Builder) on the engineering dashboard listing open ChargeOps incidents with buttons *Xác nhận / Giảm tải / Bảo trì / Kiểm tra phục hồi*. Every click goes through the same command API and worker gates; a human still decides.

Out of scope: auto-remediation without a human, Slack/PagerDuty, Datadog-side detection changes.

## Architecture

### New process `datadog-sync` (B4, B5, B7, B6 metrics)

- New module `chargeops/datadog_sync.py`, run as a new Compose service in the `observability` profile (`python -m chargeops.datadog_sync`), same image as the API.
- **Outbox, not inline calls:** the worker stays the only writer of incident state and never calls the Datadog REST API. `datadog-sync` watches `incidents` (Change Stream, falls back to 5 s polling like the worker) and keeps a per-incident cursor in a new collection `datadog_sync` (`{_id: incident_id, dd_incident_id, notebook_id, synced_events: int, postmortem_done: bool, last_error, attempts}`).
- Only incidents of runs with `mode == "datadog"` are synced (keeps local mode offline and the trial budget meaningful).
- Datadog REST access through `chargeops/datadog_client.py` (thin `httpx` wrapper: `DD_API_KEY`, `DD_APP_KEY`, `DD_SITE`; retries with backoff on 429/5xx; never logs headers or bodies containing secrets).
- Failure isolation: a Datadog API error records `last_error`/`attempts` on the sync document and retries later; it never touches the incident document and never blocks the worker.
- The ChargeOps incident API response gains read-only links `datadog_incident_url`, `datadog_notebook_url` (joined from `datadog_sync`), shown on the incident page.

### Postmortem data (B7)

- `chargeops/postmortem.py`: `postmortem_pipeline(incident_id)` → `$match` incident → `$lookup` windows and commands → `$project`/`$set` durations with `$dateDiff`, peak residual with `$max` over windows, energy via `$lookup` telemetry for the derating window → `$merge` into `incident_reports` (`whenMatched: "replace"`). Pure builder + a real-Mongo check in `check_mongo`.
- Energy numbers are simulated and labelled as such in the report and the Notebook.

### SLO metrics (B6)

- `observability.py` gains `emit_incident_outcome(stats, incident)` (once per incident on resolve/stop, guarded by the sync cursor) → counters `chargeops.slo.mitigation.total` / `.good`; and per window `chargeops.slo.charging_minutes.total` / `.good` (tags `run_id`, `connector_id`, `station_id`, `env`).
- `scripts/setup_datadog.py` creates both SLOs (metric-based, 7-day and 30-day targets 95 %) and an SLO widget on the engineering dashboard; state keys `slo_mitigation`, `slo_charging`.

### RUM (B3)

- Build-time env `VITE_DD_RUM_APPLICATION_ID`, `VITE_DD_RUM_CLIENT_TOKEN`, `VITE_DD_SITE`, `VITE_DD_ENV`, `VITE_DD_VERSION` passed as Docker build args to the `web` image; empty → RUM not initialised (tests and local runs unaffected).
- `allowedTracingUrls` = same origin `/api/`; the API's ddtrace already accepts Datadog propagation headers.
- Custom actions: `command_sent` (type, connector, incident state), `incident_viewed`; timing `incident_seen_to_command_ms`.
- Privacy: `defaultPrivacyLevel: "mask-user-input"`; no session replay (cost/consent) unless the user turns it on.
- `setup_datadog.py` documents (does not create) the RUM application; creation is a manual step in the Datadog UI because the client token must be copied into `.env`.

### Act from Datadog (B8)

- **Separate action surface, not the operator API through the tunnel.** New route `POST /api/v1/integrations/datadog/actions` with body `{incident_id, command}` (`acknowledge | reduce_load | maintenance | verify_recovery`), header `X-Action-Secret` (new secret `DATADOG_ACTION_SECRET`, ≥ 24 chars, stored only in `.env` and in the Datadog App's HTTP connection), and a required `Idempotency-Key`.
- It creates commands exactly like the operator route (same validation, same worker gates), records `actor: "datadog-app"` on the command and a timeline detail, and returns 202 with the command id. No other operator capability (demo runs, inject-fault, login) is reachable through it.
- `GET /api/v1/integrations/datadog/incidents` (same secret) returns open incidents for the App table: id, connector, station, state, allowed commands (computed with the same gate rules as the UI), links.
- `infra/Caddyfile` (tunnel gateway) adds exactly these two paths next to the webhook path; everything else stays 404. Body size limit 4 KB.
- The App definition is code: `scripts/datadog_app.json` (App Builder JSON) + `setup_datadog.py --with-app` creates/updates it via the App Builder API, creating the HTTP connection to the tunnel URL with the action secret as a header. If the App Builder API is unavailable on the account, the script prints manual import steps.

## Data flow (datadog mode)

monitor → webhook → API inbox → worker opens incident (Mongo) → `datadog-sync` sees it → Datadog Incident + Notebook (ids saved in `datadog_sync`) → operator acts in ChargeOps UI **or** Datadog App → command → worker gates/applies → timeline grows → `datadog-sync` mirrors steps → resolved → postmortem `$merge` → Notebook/Incident updated → SLO counters emitted.

## Error handling

- Datadog API down/limited: sync retries with backoff; UI shows "Đang đồng bộ Datadog…" instead of links; no effect on detection or commands.
- Duplicate creation guard: `dd_incident_id` written immediately after create; on restart, if a create succeeded but the id was not saved, search incidents by a `chargeops_incident_id` customer-impact/field tag before creating again.
- Action route: wrong secret 401, unknown incident 404, gate violation 409 with the same reason codes as the operator route, missing Idempotency-Key 422.

## Testing

- Unit: client with `httpx.MockTransport` (no network), sync state machine on mongomock (create once, mirror each event once, resolve once, postmortem once, retry after failure), postmortem pipeline shape + real-Mongo check, SLO metric emission, action route auth/idempotency/gates, gateway Caddyfile path list, RUM init disabled without env and actions sent when enabled (vitest).
- Live (user, with the backup Datadog account): one datadog-mode thermal_fault run end to end, checking Incident, timeline, Notebook, postmortem, SLO values, RUM session and one action clicked from the Datadog App.

## Constraints

- Never edit `chargeops/domain.py`, `chargeops/lifecycle.py`, `chargeops/model.py`, `chargeops/evaluation/*`, `chargeops/profiles/autosun_v3.csv`; the v3.1 evaluation lock must stay valid.
- Only the worker writes incident state; `datadog-sync` writes only `datadog_sync` and `incident_reports`.
- No secret in logs, frontend bundle (RUM client token is public by design), commits or screenshots.
- Implementers use model Sonnet (user preference).
- Stop and hand over to the user whenever a step needs a live Datadog account or Docker.
