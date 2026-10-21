import { useState, type FormEvent } from "react";
import { api, ApiError } from "../api/client";
import { useAuth } from "../api/auth";
import { usePolling } from "../api/hooks";
import { idOf, type DemoRun, type RunMode, type Scenario } from "../api/types";
import { Async, ErrorBox } from "../components/Async";
import { REASON_LABEL, RUN_STATUS_LABEL, SCENARIO_LABEL, fmtTime } from "../lib/format";

const SCENARIOS: Scenario[] = ["healthy", "thermal_fault"];

export function DemoControl() {
  const { role } = useAuth();
  const isOperator = role === "operator";
  const runs = usePolling(() => api.runs(), []);
  const connectors = usePolling(() => api.connectors(), []);
  const [scenario, setScenario] = useState<Scenario>("thermal_fault");
  const [seed, setSeed] = useState("42");
  const [mode, setMode] = useState<RunMode>("local");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [faultTarget, setFaultTarget] = useState("");

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      runs.reload();
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, "unknown", String(e), null));
    } finally {
      setBusy(false);
    }
  }

  function create(e: FormEvent) {
    e.preventDefault();
    const n = Number(seed);
    if (!Number.isInteger(n)) {
      setError(new ApiError(422, "invalid_seed", "Seed phải là số nguyên.", null));
      return;
    }
    void act(() => api.createRun({ scenario, seed: n, mode }));
  }

  const connectorIds = connectors.data?.items.map((c) => c.connector_id) ?? [];
  const target = faultTarget || connectorIds[0] || "";

  return (
    <section className="stack">
      <h1>Điều khiển demo</h1>
      {!isOperator && (
        <p className="banner" data-testid="readonly-note">
          Bạn đang ở chế độ chỉ xem. Đăng nhập operator để tạo, khởi động, dừng run hoặc chèn lỗi.
        </p>
      )}

      <form className="card form" onSubmit={create}>
        <h2>Tạo run mới</h2>
        <label>
          Kịch bản
          <select value={scenario} onChange={(e) => setScenario(e.target.value as Scenario)} disabled={!isOperator}>
            {SCENARIOS.map((s) => (
              <option key={s} value={s}>
                {SCENARIO_LABEL[s]}
              </option>
            ))}
          </select>
        </label>
        <label>
          Seed
          <input value={seed} onChange={(e) => setSeed(e.target.value)} inputMode="numeric" disabled={!isOperator} />
        </label>
        <label>
          Chế độ
          <select value={mode} onChange={(e) => setMode(e.target.value as RunMode)} disabled={!isOperator}>
            <option value="local">Local (không cần Datadog)</option>
            <option value="datadog">Datadog</option>
          </select>
        </label>
        <button type="submit" className="btn primary" disabled={!isOperator || busy}>
          Tạo run
        </button>
      </form>

      {error && <ErrorBox error={error} reasons={REASON_LABEL} />}

      <div className="card">
        <h2>Các run</h2>
        <Async polled={runs} isEmpty={(d) => d.items.length === 0} empty="Chưa có run nào. Tạo run để bắt đầu demo.">
          {(page) => (
            <table className="table">
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Kịch bản</th>
                  <th>Seed</th>
                  <th>Chế độ</th>
                  <th>Trạng thái</th>
                  <th>Tạo lúc</th>
                  <th>Thao tác</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((r) => (
                  <RunRow
                    key={idOf(r)}
                    run={r}
                    isOperator={isOperator}
                    busy={busy}
                    onStart={() => void act(() => api.startRun(idOf(r)))}
                    onStop={() => void act(() => api.stopRun(idOf(r)))}
                  />
                ))}
              </tbody>
            </table>
          )}
        </Async>
      </div>

      <div className="card form">
        <h2>Chèn lỗi nhiệt</h2>
        <p className="muted small">Chỉ áp dụng cho run đang chạy. Lỗi được ghi thành sự kiện điều khiển cho simulator.</p>
        {connectors.error ? (
          <ErrorBox error={connectors.error} onRetry={connectors.reload} />
        ) : (
          <>
            <label>
              Connector
              <select value={target} onChange={(e) => setFaultTarget(e.target.value)} disabled={!isOperator}>
                {connectorIds.map((id) => (
                  <option key={id} value={id}>
                    {id}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="btn"
              disabled={!isOperator || busy || !target || !runs.data?.items.some((r) => r.status === "active")}
              onClick={() => {
                const active = runs.data?.items.find((r) => r.status === "active");
                if (active) void act(() => api.injectFault(idOf(active), target));
              }}
            >
              Chèn lỗi
            </button>
          </>
        )}
      </div>
    </section>
  );
}

function RunRow({
  run,
  isOperator,
  busy,
  onStart,
  onStop,
}: {
  run: DemoRun;
  isOperator: boolean;
  busy: boolean;
  onStart: () => void;
  onStop: () => void;
}) {
  const startable = run.status === "created";
  const stoppable = run.status === "active" || run.status === "pending" || run.status === "created";
  return (
    <tr>
      <td className="mono">{idOf(run).slice(0, 8)}</td>
      <td>{SCENARIO_LABEL[run.scenario] ?? run.scenario}</td>
      <td>{run.seed ?? "—"}</td>
      <td>{run.mode === "datadog" ? "Datadog" : "Local"}</td>
      <td>
        {RUN_STATUS_LABEL[run.status] ?? run.status}
        {run.reason && (
          <div className="muted small" data-testid="run-reason">
            {REASON_LABEL[run.reason] ?? run.reason}
          </div>
        )}
      </td>
      <td>{fmtTime(run.created_at)}</td>
      <td className="actions">
        <button type="button" className="btn" disabled={!isOperator || busy || !startable} onClick={onStart}>
          Khởi động
        </button>
        <button type="button" className="btn" disabled={!isOperator || busy || !stoppable} onClick={onStop}>
          Dừng
        </button>
      </td>
    </tr>
  );
}
