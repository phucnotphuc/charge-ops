import type { IncidentState, RunMode } from "../api/types";
import { STATE_LABEL } from "../lib/format";

export const STATE_ORDER: IncidentState[] = [
  "open",
  "acknowledged",
  "mitigating",
  "mitigated",
  "verifying",
  "resolved",
];

export function StateBadge({ state }: { state: string }) {
  return (
    <span className={`badge state-${state}`} data-testid="state-badge">
      {STATE_LABEL[state] ?? state}
    </span>
  );
}

export function SourceBadge({ source }: { source: string }) {
  return <span className="badge neutral">{source === "datadog" ? "Nguồn: Datadog" : "Nguồn: Local"}</span>;
}

export function ModeBadge({ mode }: { mode: RunMode | null | undefined }) {
  if (!mode) return <span className="badge neutral">Chưa có run</span>;
  return <span className="badge neutral">Chế độ: {mode === "datadog" ? "Datadog" : "Local"}</span>;
}

/** The six-state path with the current state marked; not a menu, only orientation. */
export function StateStepper({ state }: { state: string }) {
  const current = STATE_ORDER.indexOf(state as IncidentState);
  return (
    <ol className="stepper" aria-label="Tiến trình xử lý sự cố">
      {STATE_ORDER.map((s, i) => (
        <li
          key={s}
          className={i < current ? "done" : i === current ? "current" : ""}
          aria-current={i === current ? "step" : undefined}
        >
          <span className="dot" aria-hidden />
          <span>{STATE_LABEL[s]}</span>
        </li>
      ))}
    </ol>
  );
}
