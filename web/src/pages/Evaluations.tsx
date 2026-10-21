import { useState } from "react";
import { api, ApiError } from "../api/client";
import { usePolling } from "../api/hooks";
import { idOf, type Evaluation } from "../api/types";
import { Async, ErrorBox, Loading } from "../components/Async";
import { fmtTime } from "../lib/format";

/** GET /evaluations may not exist yet: a 404 is an empty state, any other failure is an error. */
export function Evaluations() {
  const polled = usePolling(async () => {
    try {
      return await api.evaluations();
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return { items: [], next_cursor: null };
      throw e;
    }
  }, []);
  const [selected, setSelected] = useState<string | null>(null);

  return (
    <section className="stack">
      <h1>Đánh giá</h1>
      <Async
        polled={polled}
        isEmpty={(d) => d.items.length === 0}
        empty="Chưa có kết quả đánh giá. Kết quả chỉ hiện khi lượt đánh giá thật đã chạy."
      >
        {(page) => (
          <div className="grid two">
            <div className="card">
              <table className="table">
                <thead>
                  <tr>
                    <th>Đánh giá</th>
                    <th>Seed</th>
                    <th>Baseline</th>
                    <th>Tạo lúc</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((ev) => (
                    <tr key={idOf(ev)} className={selected === idOf(ev) ? "selected" : ""}>
                      <td>
                        <button type="button" className="link" onClick={() => setSelected(idOf(ev))}>
                          {idOf(ev).slice(0, 8)}
                        </button>
                      </td>
                      <td>{ev.seed ?? "—"}</td>
                      <td>{ev.baseline_version ?? "—"}</td>
                      <td>{fmtTime(ev.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {selected ? <EvaluationDetail id={selected} /> : <div className="card muted">Chọn một đánh giá để xem chi tiết.</div>}
          </div>
        )}
      </Async>
    </section>
  );
}

function EvaluationDetail({ id }: { id: string }) {
  const polled = usePolling(() => api.evaluation(id), [id], 0);
  if (polled.error) return <ErrorBox error={polled.error} onRetry={polled.reload} />;
  if (!polled.data) return <Loading />;
  const ev: Evaluation = polled.data;
  return (
    <div className="card">
      <h2>Chi tiết {idOf(ev).slice(0, 8)}</h2>
      <p className="muted small">
        Seed {ev.seed ?? "—"} · baseline {ev.baseline_version ?? "—"} · cấu hình {ev.config_version ?? "—"}
      </p>
      {ev.summary && <p>{ev.summary}</p>}
      {ev.metrics && Object.keys(ev.metrics).length > 0 ? (
        <table className="kv">
          <tbody>
            {Object.entries(ev.metrics).map(([k, v]) => (
              <tr key={k}>
                <th>{k}</th>
                <td>{v ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="muted">Không có chỉ số.</p>
      )}
    </div>
  );
}
