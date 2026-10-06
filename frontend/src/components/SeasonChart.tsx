// The seasonal story of the planner in one picture: what the building needs
// each month (heating coral, cooling blue, stacked), the useful energy that
// comes back from stored hydrogen (teal), and the tank level at the end of
// each month (line, right axis): it fills with summer sun and empties into
// winter comfort. Plain SVG so it prints cleanly.
import { useI18n } from "../lib/i18n";

interface Props {
  heat: number[]; // kWh per month, Jan..Dec
  cool: number[]; // kWh per month
  fromH2: number[]; // useful kWh per month (electricity + heat)
  tankKg: number[]; // kg at month end
  tankSize: number; // kg
}

const W = 760;
const H = 270;
const PAD = { l: 52, r: 52, t: 18, b: 30 };

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  return Math.ceil(v / p / 2) * 2 * p;
}

export function SeasonChart({ heat, cool, fromH2, tankKg, tankSize }: Props) {
  const { lang, fmt, t } = useI18n();
  const months = Array.from({ length: 12 }, (_, i) =>
    new Intl.DateTimeFormat(lang === "fr" ? "fr-FR" : "en-GB", { month: "short" }).format(new Date(2025, i, 1)),
  );
  const demand = months.map((_, i) => (heat[i] ?? 0) + (cool[i] ?? 0));
  const yMax = niceMax(Math.max(...demand, ...fromH2, 1));
  const kgMax = niceMax(Math.max(tankSize, ...tankKg, 0.1));
  const iw = W - PAD.l - PAD.r;
  const ih = H - PAD.t - PAD.b;
  const slot = iw / 12;
  const bw = slot * 0.34;
  const y = (v: number) => PAD.t + ih * (1 - v / yMax);
  const yk = (v: number) => PAD.t + ih * (1 - v / kgMax);
  const base = PAD.t + ih;
  const line = tankKg.map((v, i) => `${i === 0 ? "M" : "L"}${(PAD.l + slot * (i + 0.5)).toFixed(1)} ${yk(v).toFixed(1)}`).join(" ");

  return (
    <figure className="season" style={{ margin: 0 }}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t("planner.season.aria")}>
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <g key={f}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(yMax * f)} y2={y(yMax * f)} stroke="var(--border)" />
            <text x={PAD.l - 8} y={y(yMax * f) + 4} textAnchor="end" className="axis">
              {fmt(yMax * f)}
            </text>
            <text x={W - PAD.r + 8} y={yk(kgMax * f) + 4} className="axis">
              {fmt(kgMax * f, kgMax < 10 ? 1 : 0)}
            </text>
          </g>
        ))}
        <text x={PAD.l - 8} y={10} textAnchor="end" className="axis">kWh</text>
        <text x={W - PAD.r + 8} y={10} className="axis">kg H₂</text>
        {months.map((m, i) => {
          const x = PAD.l + slot * i + slot / 2;
          const hv = heat[i] ?? 0;
          const cv = cool[i] ?? 0;
          const covered = fromH2[i] ?? 0;
          return (
            <g key={m}>
              <rect x={x - bw - 1} y={y(hv)} width={bw} height={Math.max(0, base - y(hv))} rx="3" fill="var(--heat)" opacity="0.8">
                <title>{`${m}: ${fmt(hv)} kWh`}</title>
              </rect>
              <rect x={x - bw - 1} y={y(hv + cv)} width={bw} height={Math.max(0, y(hv) - y(hv + cv))} rx="3" fill="var(--water)" opacity="0.8">
                <title>{`${m}: ${fmt(cv)} kWh`}</title>
              </rect>
              <rect x={x + 1} y={y(covered)} width={bw} height={Math.max(0, base - y(covered))} rx="3" fill="var(--accent)">
                <title>{`${m}: ${fmt(covered)} kWh`}</title>
              </rect>
              <text x={x} y={H - 10} textAnchor="middle" className="axis">
                {m}
              </text>
            </g>
          );
        })}
        <path d={line} fill="none" stroke="var(--text-dim)" strokeWidth="2" strokeDasharray="5 4" strokeLinejoin="round" />
        {tankKg.map((v, i) => (
          <circle key={i} cx={PAD.l + slot * (i + 0.5)} cy={yk(v)} r="3.5" fill="var(--text-dim)">
            <title>{`${months[i] ?? ""}: ${fmt(v, 1)} kg`}</title>
          </circle>
        ))}
      </svg>
      <figcaption className="row legend">
        <span><i style={{ background: "var(--heat)" }} /> {t("planner.season.heat")}</span>
        <span><i style={{ background: "var(--water)" }} /> {t("planner.season.cool")}</span>
        <span><i style={{ background: "var(--accent)" }} /> {t("planner.season.h2")}</span>
        <span><i style={{ background: "var(--text-dim)" }} /> {t("planner.season.tank")}</span>
      </figcaption>
    </figure>
  );
}
