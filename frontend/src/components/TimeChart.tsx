// A thin React wrapper around uPlot: fast canvas charts for live data
// (hundreds of points, several updates a second, no jank).
import { useEffect, useRef, useSyncExternalStore } from "react";
import uPlot, { type AlignedData, type Options } from "uplot";
import { useI18n } from "../lib/i18n";

export interface SeriesSpec {
  label: string;
  color: string;
  values: number[];
  unit?: string;
  dash?: number[];
  fill?: boolean;
}

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

// "var(--solar)" → the theme's value, so series follow the palette and the theme.
function paint(color: string): string {
  const m = /^var\((--[\w-]+)\)$/.exec(color);
  return m?.[1] ? cssVar(m[1]) : color;
}

// The canvas reads colours once, so charts are rebuilt when the theme changes.
function subscribeTheme(onChange: () => void): () => void {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return () => observer.disconnect();
}
const currentTheme = () => document.documentElement.dataset.theme ?? "dark";

export function TimeChart({
  times,
  series,
  height = 190,
  thresholds = [],
  zero = false,
  timeZone = "Africa/Tunis",
}: {
  times: number[];
  series: SeriesSpec[];
  height?: number;
  thresholds?: { value: number; color: string; label: string }[];
  zero?: boolean; // keep 0 in view (powers, concentrations), so a level is never exaggerated
  timeZone?: string; // the site's own clock (simulated time is shown as the site lives it)
}) {
  const host = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const shape = series.map((s) => s.label).join("|");
  const thresholdsKey = thresholds.map((t) => `${t.label}:${t.value}`).join("|");
  const theme = useSyncExternalStore(subscribeTheme, currentTheme);
  const { lang } = useI18n();

  // (Re)create the chart when the set of series changes.
  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const grid = { stroke: cssVar("--border"), width: 1 };
    const axis = { stroke: cssVar("--text-faint"), grid, ticks: { show: false }, font: '10.5px "JetBrains Mono Variable", monospace' };
    const opts: Options = {
      width: el.clientWidth,
      height,
      cursor: { drag: { x: false, y: false } },
      // Static legend (labels only): the metric cards above already show live values.
      legend: { live: false },
      // The y range always includes the thresholds: a safety chart shows how
      // far the reading is from its limit, not just its wiggles.
      scales: {
        x: { time: true },
        y: {
          range: (_u, min, max) => {
            const lo = Math.min(min, ...thresholds.map((t) => t.value), ...(zero ? [0] : []));
            const hi = Math.max(max, ...thresholds.map((t) => t.value), ...(zero ? [0] : []));
            const pad = (hi - lo || Math.abs(hi) || 1) * 0.08;
            return [lo >= 0 && lo - pad < 0 ? 0 : lo - pad, hi + pad];
          },
        },
      },
      axes: [
        {
          ...axis,
          values: (_u: uPlot, splits: number[]) => {
            const clock = new Intl.DateTimeFormat(lang === "fr" ? "fr-FR" : "en-GB", {
              hour: "2-digit",
              minute: "2-digit",
              timeZone,
            });
            return splits.map((v) => clock.format(new Date(v * 1000)));
          },
        },
        { ...axis, size: 48 },
      ],
      series: [
        {},
        ...series.map((s) => ({
          label: s.label,
          stroke: paint(s.color),
          width: 2,
          dash: s.dash,
          points: { show: false },
          fill: s.fill ? `${paint(s.color)}22` : undefined,
          value: (_: uPlot, v: number | null) => (v === null || Number.isNaN(v) ? "—" : `${v.toFixed(1)}${s.unit ? ` ${s.unit}` : ""}`),
        })),
      ],
      hooks: {
        draw: [
          (u) => {
            // Each limit as a dashed line, labelled at the right end inside
            // the plot. Labels that would overlap a neighbour are left out
            // (the line stays): close limits stay readable.
            const ctx = u.ctx;
            const dpr = devicePixelRatio;
            const right = u.bbox.left + u.bbox.width;
            let lastLabelY = Number.POSITIVE_INFINITY;
            const sorted = [...thresholds].sort((a, b) => a.value - b.value);
            for (const t of sorted) {
              const y = u.valToPos(t.value, "y", true);
              ctx.save();
              ctx.strokeStyle = paint(t.color);
              ctx.setLineDash([5, 5]);
              ctx.lineWidth = 1.2 * dpr;
              ctx.beginPath();
              ctx.moveTo(u.bbox.left, y);
              ctx.lineTo(right, y);
              ctx.stroke();
              if (lastLabelY - y >= 17 * dpr) {
                ctx.fillStyle = paint(t.color);
                ctx.font = `600 ${10 * dpr}px "JetBrains Mono Variable", monospace`;
                ctx.textAlign = "right";
                ctx.textBaseline = "bottom";
                ctx.fillText(t.label, right - 4 * dpr, y - 3 * dpr);
                lastLabelY = y;
              }
              ctx.restore();
            }
          },
        ],
      },
    };
    const data = [times, ...series.map((s) => s.values)] as AlignedData;
    plot.current = new uPlot(opts, data, el);
    const resize = new ResizeObserver(() => plot.current?.setSize({ width: el.clientWidth, height }));
    resize.observe(el);
    return () => {
      resize.disconnect();
      plot.current?.destroy();
      plot.current = null;
    };
    // Recreate only when the series layout or thresholds change; data updates go through setData below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shape, height, thresholdsKey, theme, zero, timeZone, lang]);

  useEffect(() => {
    plot.current?.setData([times, ...series.map((s) => s.values)] as AlignedData);
  }, [times, series]);

  return <div ref={host} className="chart" />;
}
