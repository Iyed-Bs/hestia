// Fetch a GET endpoint and keep the result. `refreshKey` re-fetches whenever
// it changes (e.g. the journal head hash from the live stream), so pages stay
// current without polling.
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "./api";

export function useApi<T>(path: string | null, refreshKey?: unknown) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!path) return;
    let alive = true;
    api<T>(path)
      .then((d) => {
        if (!alive) return;
        setData(d);
        setError("");
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof ApiError ? e.message : String(e));
      });
    return () => {
      alive = false;
    };
  }, [path, tick, refreshKey]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { data, error, reload };
}
