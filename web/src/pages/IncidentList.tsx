import { Link } from "react-router-dom";
import { api } from "../api/client";
import { usePolling } from "../api/hooks";
import { idOf } from "../api/types";
import { Async } from "../components/Async";
import { SourceBadge, StateBadge } from "../components/Badges";
import { fmtTime } from "../lib/format";

export function IncidentList() {
  const polled = usePolling(() => api.incidents(), []);
  return (
    <section className="stack">
      <h1>Sự cố</h1>
      <Async polled={polled} isEmpty={(d) => d.items.length === 0} empty="Chưa có sự cố nào. Hệ thống đang bình thường.">
        {(page) => (
          <div className="card">
            <table className="table">
              <thead>
                <tr>
                  <th>Trạng thái</th>
                  <th>Connector</th>
                  <th>Nguồn</th>
                  <th>Mở lúc</th>
                  <th>Cập nhật trạng thái</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {page.items.map((i) => (
                  <tr key={idOf(i)}>
                    <td>
                      <StateBadge state={i.state} />
                    </td>
                    <td>{i.connector_id}</td>
                    <td>
                      <SourceBadge source={i.source} />
                    </td>
                    <td>{fmtTime(i.opened_at)}</td>
                    <td>{fmtTime(i.state_since)}</td>
                    <td>
                      <Link to={`/incidents/${idOf(i)}`}>Chi tiết</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Async>
    </section>
  );
}
