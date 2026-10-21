import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api/client";
import { usePolling } from "../api/hooks";
import type { TelemetryPoint } from "../api/types";
import { Async } from "../components/Async";
import { STATE_LABEL, TZ, downsample } from "../lib/format";

export const MAX_POINTS = 1500;

const WINDOWS = [
  { label: "15 phút", ms: 15 * 60_000 },
  { label: "1 giờ", ms: 60 * 60_000 },
  { label: "6 giờ", ms: 6 * 60 * 60_000 },
  { label: "24 giờ", ms: 24 * 60 * 60_000 },
];

const tick = new Intl.DateTimeFormat("vi-VN", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hour12: false });
const full = new Intl.DateTimeFormat("vi-VN", {
  timeZone: TZ,
  day: "2-digit",
  month: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

export function ConnectorPage() {
  const { id = "" } = useParams();
  const [windowMs, setWindowMs] = useState(WINDOWS[1].ms);
  const polled = usePolling(async () => {
    const connector = await api.connector(id);
    const to = new Date();
    const from = new Date(to.getTime() - windowMs);
    const telemetry = await api.connectorTelemetry(
      id,
      connector.run_id,
      from.toISOString(),
      to.toISOString(),
    );
    return { connector, points: downsample(telemetry.items, MAX_POINTS), total: telemetry.items.length, truncated: telemetry.truncated };
  }, [id, windowMs]);

  return (
    <section className="stack">
      <p>
        <Link to="/">← Tổng quan</Link>
      </p>
      <div className="page-head">
        <h1>Connector {id}</h1>
        <label className="inline">
          Khoảng thời gian
          <select value={windowMs} onChange={(e) => setWindowMs(Number(e.target.value))}>
            {WINDOWS.map((w) => (
              <option key={w.ms} value={w.ms}>
                {w.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <Async
        polled={polled}
        isEmpty={(d) => d.points.length === 0}
        empty="Không có telemetry trong khoảng thời gian đã chọn."
      >
        {({ connector, points, total, truncated }) => (
          <div className="stack">
            {connector.active_incident && (
              <p>
                <Link to={`/incidents/${connector.active_incident.id}`}>Có sự cố đang xử lý, xem chi tiết</Link>{" "}
                <span className={`badge state-${connector.active_incident.state}`}>
                  {STATE_LABEL[connector.active_incident.state] ?? connector.active_incident.state}
                </span>
              </p>
            )}
            {connector.stale && (
              <p className="banner" data-testid="stale-note">
                Dữ liệu của connector này đang cũ (quá 30 giây hoặc chưa có telemetry).
              </p>
            )}
            {(truncated || total > points.length) && (
              <p className="muted small" data-testid="series-note">
                {truncated ? "Chuỗi dài hơn giới hạn tải, chỉ hiển thị phần đầu. " : ""}
                Hiển thị {points.length}/{total} điểm.
              </p>
            )}
            <ChartCard title="Nhiệt độ connector: đo và kỳ vọng (°C)" points={points}>
              <Line type="monotone" dataKey="connector_temperature_c" name="Nhiệt đo (°C)" stroke="#c2410c" dot={false} connectNulls={false} isAnimationActive={false} />
              <Line type="monotone" dataKey="expected_temperature_c" name="Nhiệt kỳ vọng (°C)" stroke="#2563eb" strokeDasharray="5 3" dot={false} connectNulls={false} isAnimationActive={false} />
            </ChartCard>
            <ChartCard title="Residual nhiệt (°C)" points={points}>
              <Line type="monotone" dataKey="residual" name="Residual (°C)" stroke="#7c3aed" dot={false} connectNulls={false} isAnimationActive={false} />
              <ReferenceLine y={8} stroke="#b91c1c" strokeDasharray="4 4" label="Ngưỡng cảnh báo 8 °C" />
              <ReferenceLine y={3} stroke="#15803d" strokeDasharray="4 4" label="Ngưỡng phục hồi 3 °C" />
            </ChartCard>
            <ChartCard title="Dòng và giới hạn dòng (A)" points={points}>
              <Line type="stepAfter" dataKey="current_a" name="Dòng đo (A)" stroke="#0f766e" dot={false} connectNulls={false} isAnimationActive={false} />
              <Line type="stepAfter" dataKey="current_limit_a" name="Giới hạn dòng (A)" stroke="#6b7280" strokeDasharray="5 3" dot={false} connectNulls={false} isAnimationActive={false} />
            </ChartCard>
          </div>
        )}
      </Async>
    </section>
  );
}

function ChartCard({
  title,
  points,
  children,
}: {
  title: string;
  points: TelemetryPoint[];
  children: ReactNode;
}) {
  const data = points.map((p) => ({ ...p, t: new Date(p.measured_at).getTime() }));
  return (
    <div className="card">
      <h2>{title}</h2>
      <div className="chart">
        <ResponsiveContainer width="100%" height={240}>
          <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
            <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={(v: number) => tick.format(new Date(v))} />
            <YAxis width={44} />
            <Tooltip labelFormatter={(v) => full.format(new Date(Number(v)))} />
            <Legend />
            {children}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
