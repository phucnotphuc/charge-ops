import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./client";

export const POLL_MS = 5000;

export interface Polled<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

/** Fetch now and then every 5 s. A failed poll clears data: the UI shows the error, never stale or mock data. */
export function usePolling<T>(load: () => Promise<T>, deps: unknown[], intervalMs = POLL_MS): Polled<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const loader = useRef(load);
  loader.current = load;
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setData(null);
    setError(null);
    const run = async () => {
      try {
        const next = await loader.current();
        if (!alive) return;
        setData(next);
        setError(null);
      } catch (e) {
        if (!alive) return;
        setData(null);
        setError(e instanceof ApiError ? e : new ApiError(0, "unknown", String(e), null));
      } finally {
        if (alive) setLoading(false);
      }
    };
    void run();
    const timer = intervalMs > 0 ? setInterval(run, intervalMs) : undefined;
    return () => {
      alive = false;
      if (timer) clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, intervalMs, tick]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { data, error, loading, reload };
}
