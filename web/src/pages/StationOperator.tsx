import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { usePolling } from "../api/hooks";
import { Async } from "../components/Async";
import { ConnectorCard } from "../components/ConnectorCard";
import { Summary, Trend } from "../components/Insights";
import { STATE_LABEL, STATION_NAMES, fmtNum } from "../lib/format";

export function StationOperator() {
  const { stationId = "station-1" } = useParams();
  const station = usePolling(() => api.station(stationId), [stationId]);
  const connectors = usePolling(() => api.connectors(), []);
  const [picked, setPicked] = useState<string | null>(null);
  const items = connectors.data?.items ?? [];
  const runId = items.find((c) => c.run_id)?.run_id ?? null;
  const fleet = usePolling(() => (runId ? api.fleetInsights(runId) : Promise.resolve(null)), [runId]);
  const incidents = Object.fromEntries(items.map((c) => [c.connector_id, c.active_incident]));
  return (
    <div className="page">
      <h1>Vận hành · {STATION_NAMES[stationId] ?? stationId}</h1>
      <p>
        <Link to={`/stations/${stationId}`}>Mở màn hình trạm cho người dùng</Link>
      </p>
      <Async polled={station}>
        {(s) => {
          const focus = picked ?? s.connectors[0]?.connector_id ?? null;
          return (
            <>
              <div className="station-grid">
                {s.connectors.map((c) => {
                  const incident = incidents[c.connector_id];
                  const w = fleet.data?.windows.find((x) => x.connector_id === c.connector_id);
                  return (
                    <ConnectorCard
                      key={c.connector_id}
                      c={c}
                      extra={
                        <>
                          <p>Residual nhiệt: {fmtNum(w && w.quality ? w.residual_mean : null, "°C")}</p>
                          {incident && (
                            <Link to={`/incidents/${incident.id}`}>{STATE_LABEL[incident.state] ?? incident.state}</Link>
                          )}
                          <button type="button" className="btn ghost" onClick={() => setPicked(c.connector_id)}>
                            Xem xu hướng
                          </button>
                        </>
                      }
                    />
                  );
                })}
              </div>
              {runId && focus && <Trend connectorId={focus} runId={runId} />}
              <Summary runId={runId} stationId={stationId} />
            </>
          );
        }}
      </Async>
    </div>
  );
}
