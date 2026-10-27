import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client";
import { usePolling } from "../api/hooks";
import { Async } from "./Async";
import { STATE_LABEL, fmtTime } from "../lib/format";

const FUNNEL = ["open", "acknowledged", "mitigating", "mitigated", "verifying", "resolved"] as const;

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "—";
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)} phút ${s % 60} giây`;
}

export function Summary({ runId, stationId }: { runId: string | null; stationId?: string }) {
  const polled = usePolling(() => api.incidentSummary(runId, stationId), [runId, stationId]);
  return (
    <Async polled={polled}>
      {(s) => (
        <section className="card">
          <h2>Sự cố</h2>
          {s.total === 0 && <p className="muted">Chưa có sự cố trong run này.</p>}
          <div className="funnel">
            {FUNNEL.map((state) => (
              <div key={state} data-testid={`funnel-${state}`}>
                <span className="muted small">{STATE_LABEL[state] ?? state}</span>
                <strong>{s.by_state.find((x) => x.state === state)?.count ?? 0}</strong>
              </div>
            ))}
          </div>
          <dl className="kv">
            <dt>Thời gian tới xác nhận (MTTA)</dt>
            <dd>{fmtDuration(s.mtta_s)}</dd>
            <dt>Thời gian tới giảm tải có tác dụng</dt>
            <dd>{fmtDuration(s.mttm_s)}</dd>
            <dt>Thời gian tới phục hồi (MTTR)</dt>
            <dd>{fmtDuration(s.mttr_s)}</dd>
          </dl>
        </section>
      )}
    </Async>
  );
}

export function Trend({ connectorId, runId }: { connectorId: string; runId: string }) {
  const polled = usePolling(() => api.connectorTrend(connectorId, runId), [connectorId, runId]);
  return (
    <Async polled={polled} isEmpty={(t) => t.points.length === 0} empty="Chưa có cửa sổ hợp lệ.">
      {(t) => (
        <section className="card">
          <h2>Xu hướng residual · {connectorId}</h2>
          <p className="muted small">Tính bằng MongoDB $setWindowFields trên các cửa sổ 60 s hợp lệ.</p>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={t.points.map((p) => ({ ...p, at: fmtTime(p.end) }))}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="at" minTickGap={40} />
              <YAxis unit=" °C" />
              <Tooltip />
              <ReferenceLine y={8} stroke="var(--bad)" strokeDasharray="4 4" />
              <ReferenceLine y={3} stroke="var(--ok)" strokeDasharray="4 4" />
              <Line dataKey="residual_mean" name="Residual 60 s" dot={false} />
              <Line dataKey="residual_avg_5m" name="Trung bình 5 phút" dot={false} strokeDasharray="5 3" />
            </LineChart>
          </ResponsiveContainer>
        </section>
      )}
    </Async>
  );
}
