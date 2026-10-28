import { useState } from "react";
import { api, ApiError } from "../api/client";
import { usePolling } from "../api/hooks";
import type { Evaluation, EvaluationItem, EvaluationStats } from "../api/types";
import { Async, ErrorBox, Loading } from "../components/Async";
import { fmtTime } from "../lib/format";

const ARMS = [
  { key: "A", label: "A: ngưỡng cố định + quy trình" },
  { key: "B", label: "B: residual, không can thiệp" },
  { key: "C", label: "C: ChargeOps" },
] as const;

const DETECTORS = [
  { key: "fixed", label: "Ngưỡng cố định" },
  { key: "residual", label: "Residual theo tải" },
] as const;

const TARGET_LABEL: Record<string, string> = {
  c_recall_before_trip_ge_18_of_20: "C phát hiện ≥18/20 lỗi trước khi B trip",
  c_healthy_runs_with_incident_le_1: "C có ≤1 sự cố giả trong 20 lượt khỏe mạnh",
  duplicate_commands_zero: "Không có lệnh trùng (đúng theo cách dựng operator kịch bản, không chứng minh idempotency)",
};

const n = (v: number | null | undefined, digits = 0) =>
  v === null || v === undefined || !Number.isFinite(v) ? "—" : v.toFixed(digits);

/** median [min–max] (n), simulated seconds; n=0 stays "—". */
export function fmtStats(s: EvaluationStats): string {
  if (!s.n) return "— (n=0)";
  return `${n(s.median)} [${n(s.min)}–${n(s.max)}] (n=${s.n})`;
}

function Disclaimer() {
  return (
    <p className="banner" data-testid="sim-disclaimer">
      <strong>Chỉ là mô phỏng.</strong> Kết quả chạy offline trên đồng hồ mô phỏng với seed cố định; ngưỡng, nhiệt
      độ trip và thời gian thao tác của operator là giả định mô phỏng, không phải giới hạn an toàn thiết bị, không
      phải kết quả chạy thật và không suy rộng cho trạm thật.
    </p>
  );
}

/** A 404 (older backend) is an empty state, any other failure is an error. */
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
      <Disclaimer />
      <Async
        polled={polled}
        isEmpty={(d) => d.items.length === 0}
        empty="Chưa có kết quả đánh giá. Kết quả chỉ hiện khi lượt đánh giá offline đã chạy và được commit."
      >
        {(page) => {
          const current = selected ?? page.items[0]?.id ?? null;
          return (
            <div className="stack">
              <div className="card">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Protocol</th>
                      <th>Seed lỗi / khỏe</th>
                      <th>Lượt chạy</th>
                      <th>Khóa cấu hình lúc</th>
                    </tr>
                  </thead>
                  <tbody>
                    {page.items.map((ev: EvaluationItem) => (
                      <tr key={ev.id} className={current === ev.id ? "selected" : ""}>
                        <td>
                          <button type="button" className="link" onClick={() => setSelected(ev.id)}>
                            {ev.id}
                          </button>
                        </td>
                        <td>
                          {ev.fault_seeds} / {ev.healthy_seeds}
                        </td>
                        <td>{ev.runs}</td>
                        <td>{fmtTime(ev.locked.locked_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {current && <EvaluationDetail id={current} />}
            </div>
          );
        }}
      </Async>
    </section>
  );
}

function EvaluationDetail({ id }: { id: string }) {
  const polled = usePolling(() => api.evaluation(id), [id], 0);
  if (polled.error) return <ErrorBox error={polled.error} onRetry={polled.reload} />;
  if (!polled.data) return <Loading />;
  return <EvaluationView ev={polled.data} />;
}

export function EvaluationView({ ev }: { ev: Evaluation }) {
  const arm = (k: "A" | "B" | "C") => ev.arms[k];
  const det = (k: "fixed" | "residual") => ev.detectors[k];
  const armRows: [string, (k: "A" | "B" | "C") => string][] = [
    [`Phát hiện trước khi B trip (/${ev.fault_seeds})`, (k) => n(arm(k)?.fault.recall_before_trip)],
    [`… trong các seed B trip (/${ev.b_tripped_seeds})`, (k) => n(arm(k)?.fault.detected_before_trip_among_b_tripped)],
    ["Độ trễ phát hiện từ lúc lỗi (s)", (k) => (arm(k) ? fmtStats(arm(k)!.fault.latency_s) : "—")],
    ["Thời gian báo trước so với B trip (s)", (k) => (arm(k) ? fmtStats(arm(k)!.fault.lead_time_s) : "—")],
    ["Cảnh báo → giảm dòng có hiệu quả (s)", (k) => (arm(k) ? fmtStats(arm(k)!.fault.effective_mitigation_s) : "—")],
    ["Cảnh báo → mitigated (s)", (k) => (arm(k) ? fmtStats(arm(k)!.fault.mitigated_s) : "—")],
    [
      `Bảo trì / đã đóng (/${ev.fault_seeds})`,
      (k) => (arm(k) ? `${arm(k)!.fault.maintenance_applied} / ${arm(k)!.fault.resolved}` : "—"),
    ],
    ["Số lần trip (seed lỗi)", (k) => n(arm(k)?.fault.trips)],
    ["Phút gián đoạn (seed lỗi)", (k) => n(arm(k)?.fault.interruption_min, 1)],
    ["kWh chênh lệch so với B (seed lỗi)", (k) => n(arm(k)?.fault.kwh_delta_vs_b, 2)],
    [`Lượt khỏe có sự cố giả (/${ev.healthy_seeds})`, (k) => n(arm(k)?.healthy.runs_with_incident)],
    ["Cảnh báo giả / connector-giờ", (k) => n(arm(k)?.healthy.false_alerts_per_connector_hour, 2)],
    [
      "Lệnh / bị từ chối / trùng",
      (k) => (arm(k) ? `${arm(k)!.commands} / ${arm(k)!.rejected_commands} / ${arm(k)!.duplicate_commands}` : "—"),
    ],
  ];
  const detRows: [string, (k: "fixed" | "residual") => string][] = [
    [`Phát hiện trước khi B trip (/${ev.fault_seeds})`, (k) => n(det(k)?.recall_before_trip)],
    [`Phát hiện sau lúc lỗi (/${ev.fault_seeds})`, (k) => n(det(k)?.detected_any)],
    ["Độ trễ từ lúc lỗi (s)", (k) => (det(k) ? fmtStats(det(k)!.latency_s) : "—")],
    ["Seed lỗi cảnh báo trước lúc lỗi", (k) => n(det(k)?.pre_onset_false)],
    [`Lượt khỏe có cảnh báo (/${ev.healthy_seeds})`, (k) => n(det(k)?.healthy_runs_alerting)],
  ];

  return (
    <div className="card stack" data-testid="evaluation-detail">
      <h2>{ev.protocol_version}</h2>
      <p className="muted small">
        Fingerprint {ev.fingerprint.slice(0, 16)}… · ngưỡng A {ev.locked.a_threshold_c} °C · cảnh báo C{" "}
        {ev.locked.c_alert_c} °C / phục hồi {ev.locked.c_recovered_c} °C · {ev.fault_seeds} seed lỗi +{" "}
        {ev.healthy_seeds} seed khỏe × A/B/C · B trip ở {ev.b_tripped_seeds}/{ev.fault_seeds} seed lỗi. Thời gian là
        giây trên đồng hồ mô phỏng: trung vị [min–max] (n).
      </p>

      <h3>Vận hành (A/B/C)</h3>
      <table className="table" data-testid="arms">
        <thead>
          <tr>
            <th>Chỉ số</th>
            {ARMS.map((a) => (
              <th key={a.key}>{a.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {armRows.map(([label, get]) => (
            <tr key={label}>
              <th>{label}</th>
              {ARMS.map((a) => (
                <td key={a.key}>{get(a.key)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Chỉ bộ phát hiện (trên telemetry không can thiệp của B)</h3>
      <table className="table" data-testid="detectors">
        <thead>
          <tr>
            <th>Chỉ số</th>
            {DETECTORS.map((d) => (
              <th key={d.key}>{d.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {detRows.map(([label, get]) => (
            <tr key={label}>
              <th>{label}</th>
              {DETECTORS.map((d) => (
                <td key={d.key}>{get(d.key)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Mục tiêu nội bộ</h3>
      <ul data-testid="targets">
        {Object.entries(ev.targets).map(([k, met]) => (
          <li key={k}>
            {TARGET_LABEL[k] ?? k}: <strong>{met ? "đạt" : "chưa đạt"}</strong>
          </li>
        ))}
      </ul>
    </div>
  );
}
