// The whole site as a single-line diagram: every electrical source and use
// meets one AC bus (left), hydrogen travels along the top (electrolyser →
// tank → fuel cell or boiler), and comfort arrives at the building on the
// right. Same drawing rules as the bench: a line has colour only while energy
// or gas really moves along it, and moves faster when more does.
import type { Snapshot } from "../lib/types";
import { useI18n } from "../lib/i18n";
import { Fan, Flow, flowSpeed } from "./BenchDiagram";

const BUS_X = 270;

export function SiteDiagram({ snap }: { snap: Snapshot }) {
  const { t, fmt } = useI18n();
  const s = snap.site;
  const tel = snap.telemetry;
  if (!s) return null;

  const pv = s.pv_w;
  const elec = s.electrolyser_w;
  const grid = s.grid_w ?? 0;
  const batt = s.battery_w ?? 0;
  const fc = s.fuel_cell_w ?? 0;
  const hp = s.heat_pump_w ?? 0;
  const load = s.load_w ?? 0;
  const boiler = s.boiler_w ?? 0;
  const fcHeat = s.fuel_cell_heat_w ?? 0;
  const tank = s.h2_tank_pct ?? 0;
  const soc = s.battery_soc_pct ?? 0;
  const mode = s.hvac_mode ?? "off";
  const outage = snap.twin?.faults.includes("grid_outage") ?? false;
  const boilerSite = s.end_use === "boiler";
  const alarm = tel?.h2_alarm_latched ?? false;
  const warning = tel?.h2_warning ?? false;
  const sun = Math.min(1, s.irradiance_wpm2 / 900);
  const h2Out = fc > 0 || boiler > 0;
  const interlock = s.tank_bar !== null && s.tank_mawp_bar !== null && s.tank_bar >= 0.95 * s.tank_mawp_bar;
  const hvacColor = mode === "cool" ? "var(--water)" : "var(--heat)";

  // Direction matters on the two-way links: draw the path the way energy goes.
  const gridPath = grid >= 0 ? `M140 300 L ${BUS_X} 300` : `M${BUS_X} 300 L 140 300`;
  const battPath = batt >= 0 ? `M${BUS_X} 425 L 150 425` : `M150 425 L ${BUS_X} 425`;

  return (
    <svg className="diagram" viewBox="0 0 1000 520" role="img" aria-label={t("dg.site")}>
      {/* ── Flows ── */}
      <Flow d={`M190 126 L ${BUS_X} 126`} kind="power" active={pv > 1} style={flowSpeed(pv, 5000)} />
      <Flow d={gridPath} kind="power" active={Math.abs(grid) > 1} style={flowSpeed(grid, 4000)} />
      <Flow d={battPath} kind="power" active={Math.abs(batt) > 1} style={flowSpeed(batt, 3000)} />
      <Flow d={`M${BUS_X} 140 L 340 140`} kind="power" active={elec > 1} style={flowSpeed(elec, 2000)} />
      <Flow d="M460 140 L 520 140" kind="h2" active={elec > 1} style={flowSpeed(elec, 2000)} />
      <Flow d="M600 140 L 660 140" kind="h2" active={h2Out} style={flowSpeed(fc + boiler, 2000)} />
      <Flow d={`M715 175 L 715 312 L ${BUS_X} 312`} kind="power" active={fc > 1} style={flowSpeed(fc, 1500)} />
      <Flow d="M770 140 L 900 140 L 900 256" kind="heat" active={fcHeat > 1 || boiler > 1} style={flowSpeed(fcHeat + boiler, 3000)} />
      <Flow d={`M${BUS_X} 366 L 560 366`} kind="power" active={hp > 1} style={flowSpeed(hp, 2000)} />
      <Flow d="M660 366 L 820 366" kind={mode === "cool" ? "water" : "heat"} active={hp > 1} style={flowSpeed(hp, 2000)} />
      <Flow d={`M${BUS_X} 452 L 820 452`} kind="power" active={load > 1} style={flowSpeed(load, 1500)} />

      {/* ── AC bus ── */}
      <line x1={BUS_X} y1="104" x2={BUS_X} y2="474" className="bus" />
      <text x={BUS_X - 10} y="96" className="label caps" textAnchor="middle">AC</text>

      {/* ── Sun and PV ── */}
      <g opacity={0.25 + 0.75 * sun}>
        <g className="sun-rays" style={{ transformOrigin: "110px 46px" }}>
          {Array.from({ length: 12 }, (_, i) => (
            <line key={i} x1="110" y1="20" x2="110" y2="12" stroke="var(--solar)" strokeWidth="3" strokeLinecap="round" transform={`rotate(${i * 30} 110 46)`} />
          ))}
        </g>
        <circle cx="110" cy="46" r="18" fill="var(--solar)" />
      </g>
      <text x="140" y="42" className="label">{fmt(s.irradiance_wpm2)} W/m²</text>
      <path d="M36 152 L 62 100 L 190 100 L 164 152 Z" className="box" />
      {[0, 1, 2, 3].map((i) => (
        <line key={i} x1={62 + i * 32} y1="100" x2={36 + i * 32} y2="152" stroke="var(--border-strong)" />
      ))}
      <text x="36" y="176" className="label">{t("dg.pv")}</text>
      <text x="36" y="196" className="value">{fmt(pv)} W</text>

      {/* ── Grid ── */}
      <g className={outage ? "danger-stroke" : ""}>
        <path d="M70 330 L 92 262 L 114 330 M78 306 L 106 306 M84 286 L 100 286 M64 270 L 120 270" className="pylon" />
      </g>
      <text x="22" y="356" className={`label ${outage ? "danger-text" : ""}`}>{outage ? t("dg.grid.down") : t("dg.grid")}</text>
      <text x="22" y="376" className="value">
        {outage ? "—" : `${fmt(Math.abs(grid))} W`}
      </text>
      <text x="22" y="394" className="label">{!outage && Math.abs(grid) > 1 ? (grid > 0 ? t("dg.import") : t("dg.export")) : ""}</text>

      {/* ── Battery ── */}
      <rect x="60" y="410" width="90" height="62" rx="8" className="box" />
      <rect x="150" y="430" width="6" height="22" rx="2" className="box" />
      <rect x="64" y={414 + 54 * (1 - soc / 100)} width="82" height={54 * (soc / 100)} rx="5" className="charge" />
      <text x="60" y="494" className="label">{t("dg.battery")} · <tspan className="num">{fmt(soc)} %</tspan></text>
      <text x="60" y="512" className="label">{Math.abs(batt) > 1 ? `${batt > 0 ? "+" : "−"}${fmt(Math.abs(batt))} W` : ""}</text>

      {/* ── Electrolyser in its room, with the H₂ sensor and extraction ── */}
      <rect x="318" y="34" width="306" height="186" rx="12" className={`room ${alarm ? "alarm" : warning ? "warning" : ""}`} />
      <g className={elec > 0 ? "producing" : ""}>
        <rect x="340" y="104" width="120" height="74" rx="10" className="box" />
        {Array.from({ length: 6 }, (_, i) => (
          <rect key={i} x={352 + i * 17} y="114" width="9" height="54" rx="2" className="cell" />
        ))}
        {[0, 1, 2, 3].map((i) => (
          <circle key={i} className="bubble" cx={358 + i * 26} cy="164" r="3.5" style={{ animationDelay: `${i * 0.45}s` }} />
        ))}
      </g>
      <text x="340" y="96" className="label">{t("dg.electrolyser")}</text>
      <text x="340" y="198" className="value">{fmt(elec)} W</text>
      <text x="420" y="198" className="label">{tel?.temp_valid ? `${fmt(tel.electrolyte_c, 1)} °C` : ""}</text>
      <circle cx="350" cy="62" r="12" fill="var(--surface)" stroke={alarm ? "var(--danger)" : warning ? "var(--warn)" : "var(--ok)"} strokeWidth="2.5" />
      <text x="350" y="66" textAnchor="middle" className="label strong">H₂</text>
      <text x="370" y="66" className={`label ${alarm ? "danger-text" : warning ? "warn-text" : ""}`}>
        {alarm ? t("dg.alarm") : warning ? t("dg.warning") : `${fmt(tel?.h2_ppm)} ppm`}
      </text>
      <Fan x={596} y={60} r={14} on={tel?.ventilation ?? false} color="var(--water)" />

      {/* ── H₂ tank ── */}
      <rect x="520" y="88" width="80" height="120" rx="36" className={`box ${interlock ? "stroke-warn" : ""}`} />
      <rect x="525" y={93 + 110 * (1 - tank / 100)} width="70" height={110 * (tank / 100)} rx="30" className="h2-fill" />
      <text x="508" y="236" className="label">{t("dg.tank")}</text>
      <text x="508" y="256" className="value">{s.tank_bar !== null ? `${fmt(s.tank_bar, 1)} bar` : "—"}</text>
      <text x="508" y="274" className="label">{s.h2_tank_kg !== null ? `${fmt(s.h2_tank_kg, 2)} kg · ${fmt(tank)} %` : ""}</text>
      {(s.h2_vented_kg ?? 0) > 0 && <text x="508" y="292" className="label warn-text">{t("dg.relief")}: {fmt((s.h2_vented_kg ?? 0) * 1000, 0)} g</text>}

      {/* ── Fuel cell or hydrogen boiler ── */}
      <rect x="660" y="106" width="110" height="68" rx="10" className={`box ${h2Out ? "stroke-accent" : ""}`} />
      <text x="672" y="132" className="label">{boilerSite ? t("dg.boiler") : t("dg.fuel_cell")}</text>
      <text x="672" y="156" className="value">{boilerSite ? `${fmt(boiler)} W` : `${fmt(fc)} W`}</text>
      {!boilerSite && fcHeat > 1 && <text x="780" y="132" className="label">+{fmt(fcHeat)} W {t("dg.heat")}</text>}

      {/* ── Heat pump outdoor unit ── */}
      <rect x="560" y="330" width="100" height="72" rx="8" className="box" />
      <Fan x={594} y={366} r={22} on={hp > 1} color={hvacColor} />
      <text x="626" y="358" className="label">{fmt(hp)}</text>
      <text x="626" y="374" className="label">W</text>
      <text x="560" y="422" className="label">{t("dg.heat_pump")} · {mode === "heat" ? t("dg.heat") : mode === "cool" ? t("dg.cool") : t("dg.idle")}</text>

      {/* ── Building ── */}
      <path d="M820 300 L 900 248 L 980 300 L 980 474 L 820 474 Z" className={`box ${mode !== "off" ? (mode === "cool" ? "stroke-water" : "stroke-heat") : ""}`} />
      <rect x="882" y="430" width="30" height="44" rx="3" className="cell" />
      <text x="836" y="326" className="label">{t("dg.house")}</text>
      <text x="836" y="356" className="big-value">{s.indoor_c !== null ? `${fmt(s.indoor_c, 1)} °C` : "—"}</text>
      <text x="836" y="378" className="label">{fmt(s.outdoor_c, 1)} °C ↗</text>
      <text x="836" y="414" className="label">{fmt(load)} W {t("dg.appliances")}</text>
      {(s.curtailed_w ?? 0) > 1 && <text x="22" y="230" className="label warn-text">{fmt(s.curtailed_w)} W {t("dg.curtailed")}</text>}
    </svg>
  );
}
