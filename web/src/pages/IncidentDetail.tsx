import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CheckCircle2, CircleDashed, ShieldAlert } from "lucide-react";
import { api, ApiError, newIdempotencyKey } from "../api/client";
import { useAuth } from "../api/auth";
import { usePolling } from "../api/hooks";
import { idOf, type Command, type CommandType, type Incident } from "../api/types";
import { Async, ErrorBox } from "../components/Async";
import { SourceBadge, StateBadge, StateStepper } from "../components/Badges";
import {
  COMMAND_LABEL,
  COMMAND_STATUS_LABEL,
  EVENT_LABEL,
  fmtNum,
  fmtTime,
} from "../lib/format";

// Mirrors lifecycle.handle_command so the UI never offers a command the backend rejects.
const COMMANDS: { type: CommandType; allowed: (i: Incident) => boolean; hint: string }[] = [
  { type: "acknowledge", allowed: (i) => i.state === "open", hint: "Xác nhận đã nhận biết sự cố" },
  {
    type: "reduce_load",
    allowed: (i) => (i.state === "acknowledged" || i.state === "mitigating") && !i.load_reduction_verified_at,
    hint: "Đặt giới hạn dòng mục tiêu",
  },
  {
    type: "maintenance",
    // Also from mitigating once derating was verified but the residual did not recover (CONTRACT §3).
    allowed: (i) =>
      i.state === "mitigated" || (i.state === "mitigating" && i.reduction_check_reason === "residual_not_recovered"),
    hint: "Loại nguyên nhân lỗi mô phỏng",
  },
  {
    type: "verify_recovery",
    // The worker rejects verify_recovery with maintenance_not_applied until maintenance has been applied.
    allowed: (i) => i.state === "mitigated" && !!i.maintenance_applied_at,
    hint: "Khôi phục giới hạn tải gốc và kiểm tra 120 giây",
  },
];

/** The gate for one command, from COMMANDS above (single source mirroring lifecycle.handle_command). */
export function commandAllowed(incident: Incident, type: CommandType): boolean {
  return COMMANDS.find((c) => c.type === type)?.allowed(incident) ?? false;
}

export const REDUCTION_REASON_LABEL: Record<string, string> = {
  residual_not_recovered: "Đã giảm dòng nhưng nhiệt chưa phục hồi sau 120 s",
  load_not_reduced: "Dòng chưa giảm",
  insufficient_data: "Thiếu dữ liệu để xác nhận",
};

/** Vietnamese reason for a recorded reduction check; null when the worker recorded none. */
export function reductionCheckText(incident: Incident): string | null {
  if (!incident.reduction_check) return null;
  const reason = incident.reduction_check_reason;
  if (reason && REDUCTION_REASON_LABEL[reason]) return REDUCTION_REASON_LABEL[reason];
  return incident.reduction_check === "load_reduction_unverified"
    ? REDUCTION_REASON_LABEL.insufficient_data
    : REDUCTION_REASON_LABEL.load_not_reduced;
}

const TERMINAL = ["applied", "failed", "timed_out"];

export function IncidentDetail() {
  const { id = "" } = useParams();
  const polled = usePolling(() => api.incident(id), [id]);
  return (
    <section>
      <p>
        <Link to="/incidents">← Danh sách sự cố</Link>
      </p>
      <Async polled={polled}>{(incident) => <IncidentView incident={incident} onChanged={polled.reload} />}</Async>
    </section>
  );
}

export function IncidentView({ incident, onChanged }: { incident: Incident; onChanged?: () => void }) {
  const { role } = useAuth();
  const incidentId = idOf(incident);
  const ack = latestAppliedReduction(incident);
  const verifiedAt = incident.load_reduction_verified_at ?? null;

  return (
    <div className="stack">
      <div className="page-head">
        <h1>Sự cố {incident.connector_id}</h1>
        <StateBadge state={incident.state} />
        <SourceBadge source={incident.source} />
      </div>
      <p className="muted small">
        Mã: {incidentId} · Run: {incident.run_id} · Mở lúc {fmtTime(incident.opened_at)} · Trạng thái từ{" "}
        {fmtTime(incident.state_since)}
      </p>
      <StateStepper state={incident.state} />

      <div className="grid two">
        <div className="card">
          <h2>Giảm tải: áp dụng lệnh và giảm dòng thật</h2>
          <p className="muted small">
            ACK chỉ cho biết simulator đã áp dụng lệnh. Dòng giảm thật phải được telemetry sau ACK chứng minh.
          </p>
          <div className="proof">
            <ProofRow
              ok={!!ack}
              title="Đã áp dụng lệnh (ACK)"
              detail={ack ? `Simulator xác nhận lúc ${fmtTime(ack.applied_at)}` : "Chưa có ACK từ simulator"}
            />
            <ProofRow
              ok={!!verifiedAt}
              title="Đã giảm dòng (đã kiểm chứng bằng telemetry)"
              detail={
                verifiedAt
                  ? `Cửa sổ 60 giây đạt điều kiện lúc ${fmtTime(verifiedAt)}`
                  : (reductionCheckText(incident) ?? "Chưa có bằng chứng giảm dòng")
              }
              warn={!!incident.reduction_check && !verifiedAt}
            />
            {verifiedAt && incident.reduction_check_reason === "residual_not_recovered" && (
              <ProofRow
                ok={false}
                warn
                title="Giảm tải chưa hiệu quả"
                detail={`${REDUCTION_REASON_LABEL.residual_not_recovered}: được phép bảo trì ngay`}
              />
            )}
          </div>
        </div>

        <div className="card">
          <h2>Bằng chứng trước và sau</h2>
          <table className="kv">
            <thead>
              <tr>
                <th />
                <th>Trước can thiệp</th>
                <th>Sau can thiệp</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <th>Residual nhiệt (°C)</th>
                <td>{fmtNum(incident.evidence_before?.residual_mean ?? incident.evidence?.residual_mean, "°C")}</td>
                <td>{fmtNum(incident.evidence_after?.residual_mean, "°C")}</td>
              </tr>
              <tr>
                <th>Dòng (A)</th>
                <td>{fmtNum(incident.reference_current_a, "A")} (tham chiếu)</td>
                <td>{fmtNum(incident.target_current_a, "A")} (mục tiêu)</td>
              </tr>
              <tr>
                <th>Giới hạn dòng (A)</th>
                <td>{fmtNum(incident.original_limit_a, "A")} (gốc)</td>
                <td>{fmtNum(incident.target_current_a, "A")} (đặt)</td>
              </tr>
              <tr>
                <th>Thời điểm</th>
                <td>{fmtTime(incident.evidence_before?.end ?? incident.evidence?.end)}</td>
                <td>{fmtTime(incident.evidence_after?.end)}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <CommandPanel incident={incident} onChanged={onChanged} role={role} />

      <div className="card">
        <h2>Dòng thời gian</h2>
        <ol className="timeline">
          {incident.timeline.map((e, i) => (
            <li key={`${e.at}-${i}`}>
              <time>{fmtTime(e.at)}</time>
              <span>{EVENT_LABEL[e.event] ?? e.event}</span>
              {typeof e.error === "string" && <span className="muted small"> · {e.error}</span>}
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

/** Newest applied reduce_load in the current handling cycle (after the last recurrence), not the first ever. */
export function latestAppliedReduction(incident: Incident): Command | undefined {
  const cycleStart = incident.timeline
    .filter((e) => e.event === "recurrence")
    .map((e) => Date.parse(e.at))
    .reduce((a, b) => Math.max(a, b), 0);
  return (incident.commands ?? [])
    .filter((c) => c.type === "reduce_load" && c.status === "applied" && c.applied_at)
    .filter((c) => Date.parse(c.applied_at as string) >= cycleStart)
    .sort((a, b) => Date.parse(b.applied_at as string) - Date.parse(a.applied_at as string))[0];
}

function ProofRow({ ok, title, detail, warn }: { ok: boolean; title: string; detail: string; warn?: boolean }) {
  return (
    <div className={`proof-row ${ok ? "ok" : warn ? "warn" : ""}`}>
      {ok ? <CheckCircle2 size={20} aria-hidden /> : warn ? <ShieldAlert size={20} aria-hidden /> : <CircleDashed size={20} aria-hidden />}
      <div>
        <strong>{title}</strong>
        <div className="small">{detail}</div>
      </div>
    </div>
  );
}

interface Sent {
  commandId: string;
  type: CommandType;
}

function CommandPanel({
  incident,
  role,
  onChanged,
}: {
  incident: Incident;
  role: string;
  onChanged?: () => void;
}) {
  const incidentId = idOf(incident);
  const isOperator = role === "operator";
  const [busy, setBusy] = useState<CommandType | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [sent, setSent] = useState<Sent[]>([]);

  async function send(type: CommandType) {
    setBusy(type);
    setError(null);
    try {
      const accepted = await api.sendCommand(incidentId, type, newIdempotencyKey());
      setSent((s) => [{ commandId: accepted.command_id, type }, ...s]);
      onChanged?.();
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, "unknown", String(e), null));
    } finally {
      setBusy(null);
    }
  }

  const resolved = incident.state === "resolved";
  const sentIds = new Set(sent.map((s) => s.commandId));
  const history = (incident.commands ?? []).filter((c) => !sentIds.has(idOf(c)));

  return (
    <div className="card">
      <h2>Lệnh vận hành</h2>
      {!isOperator && (
        <p className="muted small" data-testid="readonly-note">
          Bạn đang ở chế độ chỉ xem. Đăng nhập operator để gửi lệnh.
        </p>
      )}
      <div className="cmd-row">
        {COMMANDS.map(({ type, allowed: allows, hint }) => {
          const allowed = allows(incident) && !resolved;
          return (
            <button
              key={type}
              type="button"
              className="btn primary"
              disabled={!isOperator || !allowed || busy !== null}
              title={!isOperator ? "Cần quyền operator" : allowed ? hint : "Không áp dụng ở trạng thái hiện tại"}
              onClick={() => void send(type)}
            >
              {COMMAND_LABEL[type]}
            </button>
          );
        })}
      </div>
      {error && <ErrorBox error={error} />}
      {sent.length > 0 && (
        <ul className="cmd-list">
          {sent.map((s) => (
            <TrackedCommand key={s.commandId} sent={s} onSettled={onChanged} />
          ))}
        </ul>
      )}
      {history.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Lệnh</th>
              <th>Trạng thái</th>
              <th>Tạo lúc</th>
              <th>Ghi chú</th>
            </tr>
          </thead>
          <tbody>
            {history.map((c) => (
              <tr key={idOf(c)}>
                <td>{COMMAND_LABEL[c.type] ?? c.type}</td>
                <td>{COMMAND_STATUS_LABEL[c.status] ?? c.status}</td>
                <td>{fmtTime(c.created_at)}</td>
                <td>{c.error ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

/** Polls GET /commands/{id} every 5 s until the command is applied, failed or timed out. */
function TrackedCommand({ sent, onSettled }: { sent: Sent; onSettled?: () => void }) {
  const [command, setCommand] = useState<Command | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const next = await api.command(sent.commandId);
        if (!alive) return;
        setCommand(next);
        setError(null);
        if (TERMINAL.includes(next.status)) {
          onSettled?.();
          return;
        }
      } catch (e) {
        if (!alive) return;
        setError(e instanceof ApiError ? e : new ApiError(0, "unknown", String(e), null));
      }
      timer = setTimeout(poll, 5000);
    };
    void poll();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sent.commandId]);

  const status = command?.status ?? "pending";
  return (
    <li data-testid="tracked-command">
      <strong>{COMMAND_LABEL[sent.type]}</strong>: {COMMAND_STATUS_LABEL[status] ?? status}
      {status === "applied" && sent.type === "reduce_load" && (
        <span className="muted small"> · ACK chưa phải bằng chứng đã giảm dòng, chờ telemetry xác nhận</span>
      )}
      {command?.error && <span className="muted small"> · {command.error}</span>}
      {error && <span className="muted small"> · lỗi tải trạng thái lệnh: {error.message}</span>}
    </li>
  );
}
