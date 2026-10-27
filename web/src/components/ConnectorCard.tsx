import type { ReactNode } from "react";
import type { StationConnector } from "../api/types";
import { PUBLIC_STATUS_LABEL, fmtEta, fmtNum } from "../lib/format";

export function ConnectorCard({ c, extra }: { c: StationConnector; extra?: ReactNode }) {
  const soc = c.soc_percent;
  const pct = soc === null || soc === undefined ? 0 : Math.min(100, Math.max(0, soc));
  return (
    <article className={`station-card status-${c.status}`} data-testid={`card-${c.connector_id}`}>
      <header>
        <h2>{c.connector_id.replace("connector-", "Đầu sạc ")}</h2>
        <span className="status">{PUBLIC_STATUS_LABEL[c.status] ?? "—"}</span>
      </header>
      {soc !== null && soc !== undefined ? (
        <div className="soc">
          <strong>{Math.round(pct)}%</strong>
          <div role="progressbar" aria-label="Pin xe" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
            <div className="soc-fill" style={{ width: `${pct}%` }} />
          </div>
        </div>
      ) : (
        <div className="soc muted">Chưa có xe</div>
      )}
      <dl className="station-kv">
        <dt>Công suất</dt>
        <dd>{fmtNum(c.power_kw, "kW")}</dd>
        <dt>Dòng điện</dt>
        <dd>{fmtNum(c.current_a, "A", 0)}</dd>
        <dt>Nhiệt độ đầu sạc</dt>
        <dd>{fmtNum(c.connector_temperature_c, "°C")}</dd>
        <dt>Đã sạc</dt>
        <dd>{fmtNum(c.energy_kwh, "kWh")}</dd>
        <dt>Đầy sau</dt>
        <dd>{fmtEta(c.minutes_to_full)}</dd>
      </dl>
      {extra}
    </article>
  );
}
