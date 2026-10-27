import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import { usePolling } from "../api/hooks";
import type { StationOut } from "../api/types";
import { ConnectorCard } from "../components/ConnectorCard";
import { STATION_NAMES, fmtClock, fmtTime } from "../lib/format";

function Clock() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 15000);
    return () => clearInterval(timer);
  }, []);
  return <time className="kiosk-clock">{fmtClock(now)}</time>;
}

function newest(s: StationOut): string | null {
  const times = s.connectors.map((c) => c.measured_at).filter((t): t is string => !!t);
  return times.length ? times.reduce((a, b) => (new Date(b) > new Date(a) ? b : a)) : null;
}

/** Kiosk never shows error details: it keeps the last good data and a calm banner while polls fail. */
export function StationDisplay() {
  const { stationId = "station-1" } = useParams();
  const polled = usePolling(() => api.station(stationId), [stationId]);
  const last = useRef<{ id: string; data: StationOut } | null>(null);
  if (polled.data) last.current = { id: stationId, data: polled.data };
  const shown = last.current?.id === stationId ? last.current.data : null;
  const failing = polled.error !== null;
  return (
    <main className="kiosk">
      <div className="kiosk-head">
        <h1>{STATION_NAMES[stationId] ?? stationId}</h1>
        <Clock />
      </div>
      {failing && (
        <p className="kiosk-banner" role="status">
          Tạm mất kết nối, đang thử lại…
        </p>
      )}
      {shown && (
        <>
          <div className="station-grid">
            {shown.connectors.map((c) => (
              <ConnectorCard key={c.connector_id} c={c} />
            ))}
          </div>
          <p className="muted small">
            Cập nhật lúc {fmtTime(newest(shown))} · Dữ liệu demo: tải từ phiên sạc thật (Autosun), nhiệt độ đầu sạc mô
            phỏng.
          </p>
        </>
      )}
    </main>
  );
}
