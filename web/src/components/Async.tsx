import type { ReactNode } from "react";
import { AlertTriangle, Inbox, Loader2 } from "lucide-react";
import type { ApiError } from "../api/client";
import type { Polled } from "../api/hooks";

export function ErrorBox({
  error,
  onRetry,
  reasons,
}: {
  error: ApiError;
  onRetry?: () => void;
  /** Optional Vietnamese explanations keyed by backend error code (e.g. 409 conflict codes). */
  reasons?: Record<string, string>;
}) {
  return (
    <div className="state state-error" role="alert">
      <AlertTriangle size={20} aria-hidden />
      <div>
        <strong>Không tải được dữ liệu</strong>
        <p>{reasons?.[error.code] ?? error.message}</p>
        <p className="muted small">
          {error.status ? `HTTP ${error.status} · ` : ""}
          {error.code}
          {error.requestId ? ` · mã yêu cầu ${error.requestId}` : ""}
        </p>
        {onRetry && (
          <button type="button" className="btn" onClick={onRetry}>
            Thử lại
          </button>
        )}
      </div>
    </div>
  );
}

export function Loading({ label = "Đang tải dữ liệu…" }: { label?: string }) {
  return (
    <div className="state" role="status">
      <Loader2 size={20} className="spin" aria-hidden />
      <span>{label}</span>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="state" data-testid="empty">
      <Inbox size={20} aria-hidden />
      <div>{children}</div>
    </div>
  );
}

/** Loading / error / empty / content in one place, so every screen handles the four states alike. */
export function Async<T>({
  polled,
  isEmpty,
  empty,
  children,
}: {
  polled: Polled<T>;
  isEmpty?: (data: T) => boolean;
  empty?: ReactNode;
  children: (data: T) => ReactNode;
}) {
  if (polled.error) return <ErrorBox error={polled.error} onRetry={polled.reload} />;
  if (polled.data === null) return polled.loading ? <Loading /> : null;
  if (isEmpty?.(polled.data)) return <Empty>{empty ?? "Chưa có dữ liệu."}</Empty>;
  return <>{children(polled.data)}</>;
}
