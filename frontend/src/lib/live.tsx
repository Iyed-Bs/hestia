// Live snapshots from the gateway over Server-Sent Events, plus a rolling
// history for the trends. Two streams use it: the live installation
// (/api/stream, shared by every page through LiveProvider) and a visitor's
// private simulation (/api/sim/stream, opened by the simulator page only).
// EventSource reconnects by itself; `connected` says when the stream is down.
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { Snapshot } from "./types";

export const HISTORY_POINTS = 900;

// Every trend the pages draw, one array per series, aligned on `t`.
export interface History {
  t: number[]; // seconds (simulated time on a twin or simulator, wall clock on a real site)
  pv: number[];
  load: number[];
  heatPump: number[];
  electrolyser: number[];
  fuelCell: number[];
  grid: number[];
  battery: number[];
  electrolyte: number[];
  koh: number[];
  h2: number[];
  tankBar: number[];
  tankPct: number[];
  indoor: number[];
  outdoor: number[];
}

type Series = Exclude<keyof History, "t">;

export const empty = (): History => ({
  t: [],
  pv: [],
  load: [],
  heatPump: [],
  electrolyser: [],
  fuelCell: [],
  grid: [],
  battery: [],
  electrolyte: [],
  koh: [],
  h2: [],
  tankBar: [],
  tankPct: [],
  indoor: [],
  outdoor: [],
});

const missing = Number.NaN;

function point(s: Snapshot): Record<Series, number> {
  const site = s.site;
  const tel = s.telemetry;
  return {
    pv: site?.pv_w ?? missing,
    load: site?.load_w ?? missing,
    heatPump: site?.heat_pump_w ?? missing,
    electrolyser: site?.electrolyser_w ?? missing,
    fuelCell: site?.fuel_cell_w ?? missing,
    grid: site?.grid_w ?? missing,
    battery: site?.battery_w ?? missing,
    electrolyte: tel?.temp_valid ? tel.electrolyte_c : missing,
    koh: tel?.koh_valid ? tel.koh_wt_pct : missing,
    h2: tel?.h2_valid ? tel.h2_ppm : missing,
    tankBar: site?.tank_bar ?? missing,
    tankPct: site?.h2_tank_pct ?? missing,
    indoor: site?.indoor_c ?? missing,
    outdoor: site?.outdoor_c ?? missing,
  };
}

export function push(h: History, s: Snapshot): History {
  const when = Date.parse(s.site?.timestamp ?? s.updated_at) / 1000;
  const last = h.t[h.t.length - 1];
  if (last !== undefined && when <= last) {
    // The clock jumped backwards (a scenario or a jump): start a fresh chart.
    if (last - when > 60) h = empty();
    else return h;
  }
  const p = point(s);
  const next = { t: [...h.t, when] } as History;
  for (const key of Object.keys(p) as Series[]) next[key] = [...h[key], p[key]];
  if (next.t.length > HISTORY_POINTS) {
    for (const key of Object.keys(next) as (keyof History)[]) next[key] = next[key].slice(-HISTORY_POINTS);
  }
  return next;
}

export interface Stream {
  snapshot: Snapshot | null;
  history: History;
  connected: boolean;
}

// Subscribe to a snapshot stream while `url` is set.
export function useStream(url: string | null): Stream {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [history, setHistory] = useState<History>(empty);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!url) return;
    const es = new EventSource(url, { withCredentials: true });
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.onmessage = (event: MessageEvent<string>) => {
      const snap = JSON.parse(event.data) as Snapshot;
      setSnapshot(snap);
      setHistory((h) => push(h, snap));
      setConnected(true);
    };
    return () => {
      es.close();
      setConnected(false);
      setSnapshot(null);
      setHistory(empty());
    };
  }, [url]);

  return { snapshot, history, connected };
}

const LiveContext = createContext<Stream | null>(null);

// The live installation, shared by every page once signed in.
export function LiveProvider({ children }: { children: ReactNode }) {
  const stream = useStream("/api/stream");
  return <LiveContext.Provider value={stream}>{children}</LiveContext.Provider>;
}

export function useLive(): Stream {
  const ctx = useContext(LiveContext);
  if (!ctx) throw new Error("useLive outside LiveProvider");
  return ctx;
}
