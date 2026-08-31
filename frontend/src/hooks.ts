import { useCallback, useEffect, useRef, useState } from "react";

/** Tracks document visibility. */
export function usePageVisible(): boolean {
  const [visible, setVisible] = useState(() => document.visibilityState === "visible");
  useEffect(() => {
    const onChange = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);
  return visible;
}

/**
 * Runs an async fetcher immediately and again every `intervalMs` while the tab
 * is visible and `enabled` is true. Interval of 0/undefined disables polling
 * (a single fetch still happens).
 */
export function useAsyncData<T>(
  fetcher: () => Promise<T>,
  deps: unknown[],
  opts?: { intervalMs?: number; enabled?: boolean }
) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const visible = usePageVisible();
  const [tick, setTick] = useState(0);
  const enabled = opts?.enabled !== false;
  const intervalMs = opts?.intervalMs ?? 0;

  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    fetcherRef
      .current()
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setError(null);
          setLoading(false);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick, enabled]);

  // Background refresh loop (does not toggle `loading`).
  useEffect(() => {
    if (!enabled || !intervalMs || !visible) return;
    const id = window.setInterval(() => {
      fetcherRef
        .current()
        .then((d) => {
          setData(d);
          setError(null);
        })
        .catch(() => {
          /* keep stale data on transient poll errors */
        });
    }, intervalMs);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, intervalMs, visible, enabled]);

  return { data, error, loading, reload, setData };
}
