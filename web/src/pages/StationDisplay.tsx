import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import { usePolling } from "../api/hooks";
import { Async } from "../components/Async";
import { ConnectorCard } from "../components/ConnectorCard";
import { fmtClock, fmtTime } from "../lib/format";

const NAMES: Record<string, string> = { "station-1": "Trạm sạc số 1", "station-2": "Trạm sạc số 2" };

function Clock() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 15000);
    return () => clearInterval(timer);
  }, []);
  return <time className="kiosk-clock">{fmtClock(now)}</time>;
}

export function StationDisplay() {
  const { stationId = "station-1" } = useParams();
  const polled = usePolling(() => api.station(stationId), [stationId]);
  return (
    <main className="kiosk">
      <div className="kiosk-head">
        <h1>{NAMES[stationId] ?? stationId}</h1>
        <Clock />
      </div>
      <Async polled={polled}>
        {(s) => (
          <>
            <div className="station-grid">
              {s.connectors.map((c) => (
                <ConnectorCard key={c.connector_id} c={c} />
              ))}
            </div>
            <p className="muted small">
              Cập nhật lúc {fmtTime(s.connectors[0]?.measured_at)} · Dữ liệu demo: tải từ phiên sạc thật (Autosun), nhiệt
              độ đầu sạc mô phỏng.
            </p>
          </>
        )}
      </Async>
    </main>
  );
}
