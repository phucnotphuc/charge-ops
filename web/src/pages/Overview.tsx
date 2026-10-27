import { Link } from "react-router-dom";
import { Clock, Plug, Siren } from "lucide-react";
import { api } from "../api/client";
import { usePolling } from "../api/hooks";
import type { Connector } from "../api/types";
import { Async } from "../components/Async";
import { ModeBadge } from "../components/Badges";
import { COMPOSITION_LABEL, STATE_LABEL, fmtAge, fmtNum, fmtTime } from "../lib/format";

const STATUS_LABEL: Record<string, string> = {
  available: "Sẵn sàng",
  charging: "Đang sạc",
  faulted: "Lỗi",
  offline: "Ngoại tuyến",
};

export function Overview() {
  const polled = usePolling(() => api.connectors(), []);
  return (
    <section className="stack">
      <h1>Tổng quan</h1>
      <ul className="station-links">
        {["station-1", "station-2"].map((id) => (
          <li key={id}>
            Trạm {id.replace("station-", "")}: <Link to={`/stations/${id}`}>Màn hình trạm</Link> ·{" "}
            <Link to={`/stations/${id}/operator`}>Vận hành</Link>
          </li>
        ))}
      </ul>
      <Async
        polled={polled}
        isEmpty={(d) => d.items.length === 0}
        empty="Chưa có connector nào. Hãy tạo và khởi động một run ở màn Điều khiển demo."
      >
        {(page) => (
          <div className="grid cards">
            {page.items.map((c) => (
              <ConnectorCard key={c.connector_id} connector={c} />
            ))}
          </div>
        )}
      </Async>
    </section>
  );
}

export function ConnectorCard({ connector: c }: { connector: Connector }) {
  const age = c.freshness_s;
  const stale = c.stale;
  const s = c.latest;
  return (
    <article className={`card connector ${stale ? "stale" : ""}`} data-testid={`connector-${c.connector_id}`}>
      <header>
        <h2>
          <Plug size={16} aria-hidden /> <Link to={`/connectors/${encodeURIComponent(c.connector_id)}`}>{c.connector_id}</Link>
        </h2>
        <span className="muted small">
          Trạm {c.station_id ?? "—"} · Trụ {c.charger_id ?? "—"}
        </span>
      </header>
      <div className="badges">
        <ModeBadge mode={c.mode} />
        <span className="badge neutral">
          Trạng thái: {s?.connector_status ? (STATUS_LABEL[s.connector_status] ?? s.connector_status) : "—"}
        </span>
      </div>
      {s ? (
        <dl className="metrics">
          <div>
            <dt>Điện áp</dt>
            <dd>{fmtNum(s.voltage_v, "V")}</dd>
          </div>
          <div>
            <dt>Dòng</dt>
            <dd>{fmtNum(s.current_a, "A")}</dd>
          </div>
          <div>
            <dt>Công suất</dt>
            <dd>{fmtNum(s.power_kw, "kW")}</dd>
          </div>
          <div>
            <dt>Nhiệt connector</dt>
            <dd>{fmtNum(s.connector_temperature_c, "°C")}</dd>
          </div>
          <div>
            <dt>Nhiệt môi trường</dt>
            <dd>{fmtNum(s.ambient_temperature_c, "°C")}</dd>
          </div>
          <div>
            <dt>Giới hạn dòng</dt>
            <dd>{fmtNum(s.current_limit_a, "A")}</dd>
          </div>
        </dl>
      ) : (
        <p className="muted">Chưa có telemetry.</p>
      )}
      <p className={`fresh ${stale ? "stale-text" : ""}`} data-testid="freshness">
        <Clock size={14} aria-hidden /> {stale ? "Dữ liệu cũ" : "Dữ liệu mới"}: {fmtAge(age)}
        {s && <span className="muted small"> · đo lúc {fmtTime(s.measured_at)}</span>}
      </p>
      {s && (
        <p className="muted small" data-testid="provenance">
          Nguồn dữ liệu:{" "}
          {s.provenance?.composition ? (COMPOSITION_LABEL[s.provenance.composition] ?? s.provenance.composition) : "—"}
          {" · manifest "}
          {s.provenance?.manifest_id ?? "—"}
        </p>
      )}
      {c.active_incident ? (
        <p>
          <Siren size={14} aria-hidden /> <Link to={`/incidents/${c.active_incident.id}`}>Có sự cố đang xử lý</Link>{" "}
          <span className={`badge state-${c.active_incident.state}`}>
            {STATE_LABEL[c.active_incident.state] ?? c.active_incident.state}
          </span>
        </p>
      ) : (
        <p className="muted small">Không có sự cố đang mở.</p>
      )}
    </article>
  );
}
