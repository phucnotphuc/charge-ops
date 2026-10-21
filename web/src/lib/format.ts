export const TZ = "Asia/Ho_Chi_Minh";

const dateTime = new Intl.DateTimeFormat("vi-VN", {
  timeZone: TZ,
  day: "2-digit",
  month: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});
const clock = new Intl.DateTimeFormat("vi-VN", {
  timeZone: TZ,
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

/** ISO UTC -> "dd/MM HH:mm:ss" in Asia/Ho_Chi_Minh; null -> "—". */
export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : dateTime.format(d);
}

export function fmtClock(ms: number): string {
  return clock.format(new Date(ms));
}

/** Number with unit; missing values stay "—" (never 0). */
export function fmtNum(value: number | null | undefined, unit: string, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${value.toFixed(digits)} ${unit}`;
}

export function fmtAge(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 90) return `${Math.round(seconds)} giây trước`;
  return `${Math.round(seconds / 60)} phút trước`;
}

/** Keep at most `max` points, evenly spaced and always including the last one. */
export function downsample<T>(items: T[], max = 1500): T[] {
  if (items.length <= max) return items;
  const out: T[] = [];
  const step = (items.length - 1) / (max - 1);
  for (let i = 0; i < max; i++) out.push(items[Math.round(i * step)]);
  return out;
}

export const STATE_LABEL: Record<string, string> = {
  open: "Mới mở",
  acknowledged: "Đã xác nhận",
  mitigating: "Đang giảm tải",
  mitigated: "Đã giảm tải",
  verifying: "Đang kiểm tra phục hồi",
  resolved: "Đã giải quyết",
};

export const COMMAND_LABEL: Record<string, string> = {
  acknowledge: "Xác nhận sự cố",
  reduce_load: "Giảm tải",
  maintenance: "Bảo trì",
  verify_recovery: "Kiểm tra phục hồi",
};

export const COMMAND_STATUS_LABEL: Record<string, string> = {
  pending: "Đang chờ",
  claimed: "Simulator đã nhận",
  applied: "Đã áp dụng lệnh (ACK)",
  failed: "Thất bại",
  timed_out: "Quá thời gian 30 giây",
};

export const EVENT_LABEL: Record<string, string> = {
  opened: "Mở sự cố",
  recurrence: "Tái phát, quay lại trạng thái mở",
  acknowledge: "Operator xác nhận",
  reduce_load: "Yêu cầu giảm tải",
  reduce_load_dispatched: "Gửi lại lệnh giảm tải",
  maintenance_dispatched: "Gửi lệnh bảo trì",
  verify_recovery_dispatched: "Gửi lệnh kiểm tra phục hồi",
  load_reduction_verified: "Telemetry xác nhận dòng đã giảm",
  load_reduction_ineffective: "Dòng chưa giảm sau 120 giây (không hiệu quả)",
  load_reduction_unverified: "Chưa đủ dữ liệu để xác nhận giảm dòng",
  mitigated: "Dòng đã giảm và nhiệt phục hồi",
  maintenance_applied: "Bảo trì đã áp dụng",
  verifying: "Bắt đầu kiểm tra phục hồi 120 giây",
  retest_reset: "Kiểm tra phục hồi bị đặt lại",
  resolved: "Đã giải quyết",
  run_stopped: "Run demo đã dừng, sự cố chưa giải quyết",
  command_rejected: "Lệnh bị từ chối",
  command_timed_out: "Lệnh quá thời gian chờ ACK",
  late_command_result: "Kết quả lệnh đến muộn",
};

export const SCENARIO_LABEL: Record<string, string> = {
  healthy: "Khỏe mạnh",
  load_increase: "Tăng tải",
  demand_drop: "Giảm nhu cầu",
  thermal_fault: "Lỗi nhiệt",
};

export const RUN_STATUS_LABEL: Record<string, string> = {
  pending: "Đang chờ",
  active: "Đang chạy",
  stopped: "Đã dừng",
  rejected: "Bị từ chối",
  created: "Đã tạo, chưa khởi động",
  cancelled: "Đã hủy",
};

export const REASON_LABEL: Record<string, string> = {
  configure_dd_monitor_id_first: "Chưa cấu hình DD_MONITOR_ID cho Datadog; hãy cấu hình trước khi chạy chế độ Datadog.",
  trial_run_budget_exhausted: "Đã dùng hết 20 run ID Datadog của bản dùng thử; chỉ còn chạy chế độ Local.",
  demo_ended_unresolved: "Run demo đã kết thúc khi sự cố chưa giải quyết.",
};

export const COMPOSITION_LABEL: Record<string, string> = {
  synthetic: "Tổng hợp",
  public_replay: "Phát lại dữ liệu công khai",
  hybrid: "Lai (tải công khai, nhiệt mô phỏng)",
};
