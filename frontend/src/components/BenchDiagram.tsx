// The bench as a process diagram (P&ID style): the lab supply through the
// emergency relay to the stack; hydrogen and oxygen to their vents; the KOH
// loop with its cooler, water make-up and concentrate dosing; the room with its
// H₂ sensor and extraction fan.
//
// Drawing rules, everywhere in Hestia: equipment is neutral grey; a line takes
// its medium's colour only while something really flows in it (amber
// electricity, teal hydrogen, blue water and electrolyte, coral heat), faster
// when more flows; red is kept for safety. Every state is also written out.
import type { CSSProperties } from "react";
import type { Snapshot } from "../lib/types";
import { useI18n } from "../lib/i18n";
import { stackStatusKey } from "./ui";

export function flowSpeed(value: number, full: number): CSSProperties {
  // 0.45 s per dash cycle at full rate, 2.4 s near zero.
  const ratio = Math.max(0.05, Math.min(1, Math.abs(value) / full));
  return { animationDuration: `${(2.4 - 1.95 * ratio).toFixed(2)}s` };
}

export function Flow({ d, kind, active, style }: { d: string; kind: string; active: boolean; style?: CSSProperties }) {
  return <path d={d} className={`flow ${kind} ${active ? "active" : ""}`} style={active ? style : undefined} />;
}

export function Fan({ x, y, r, on, color }: { x: number; y: number; r: number; on: boolean; color: string }) {
  return (
    <g>
      <circle cx={x} cy={y} r={r} className="box" stroke={on ? color : undefined} />
      <g className={on ? "spin" : ""} style={{ transformOrigin: `${x}px ${y}px` }}>
        {[0, 120, 240].map((a) => (
          <path key={a} d={`M${x} ${y} L ${x} ${y - r + 3} A ${r / 2.4} ${r / 2.4} 0 0 1 ${x + r / 2} ${y - r / 3} Z`}
            fill={on ? color : "var(--border-strong)"} transform={`rotate(${a} ${x} ${y})`} />
        ))}
      </g>
    </g>
  );
}

export function BenchDiagram({ snap }: { snap: Snapshot }) {
  const { t, fmt } = useI18n();
  const tel = snap.telemetry;
  const stack = snap.twin?.stack;
  const power = snap.site?.electrolyser_w ?? 0;
  const producing = power > 0;
  const alarm = tel?.h2_alarm_latched ?? false;
  const warning = tel?.h2_warning ?? false;
  const relayOpen = tel?.h2_relay_closed === false;
  const cooling = tel?.cooling_pump ?? false;
  const fan = tel?.ventilation ?? false;
  const h2Trust = snap.trust?.sensors.h2.level ?? "ok";
  const sensorColor = alarm ? "var(--danger)" : warning ? "var(--warn)" : h2Trust === "ok" ? "var(--ok)" : h2Trust === "degraded" ? "var(--warn)" : "var(--danger)";
  const level = tel?.level_low ? 0.55 : 0.8;
  const tooHot = tel?.temp_valid && tel.electrolyte_c >= (tel.temp_alert_c ?? 70);

  return (
    <svg className="diagram" viewBox="0 0 1000 470" role="img" aria-label={t("dg.bench")}>
      {/* ── The room ── */}
      <rect x="196" y="16" width="786" height="440" rx="14" className={`room ${alarm ? "alarm" : warning ? "warning" : ""}`} />
      <text x="214" y="40" className="label caps">{t("dg.room")}</text>

      {/* ── Flows first, under the equipment ── */}
      <Flow d="M154 215 L 330 215" kind="power" active={producing} style={flowSpeed(power, 1000)} />
      <Flow d="M470 110 L 470 66 L 790 66" kind="h2" active={producing} style={flowSpeed(power, 1000)} />
      <Flow d="M515 110 L 515 92 L 790 92" kind="o2" active={producing} style={flowSpeed(power, 1000)} />
      <Flow d="M400 300 L 400 344" kind="water" active={producing || cooling} />
      <Flow d="M500 344 L 500 300" kind="water" active={producing || cooling} />
      <Flow d="M540 250 L 600 250 L 600 330 L 646 330" kind="water" active={cooling} />
      <Flow d="M646 392 L 600 392 L 600 392 L 510 392" kind="water" active={cooling} />
      <Flow d="M300 392 L 360 392" kind="water" active={tel?.water_makeup ?? false} />
      <Flow d="M300 300 L 330 300 L 330 360 L 360 360" kind="water" active={tel?.koh_dosing ?? false} />
      <Flow d="M922 66 L 980 66" kind="air" active={fan} />

      {/* ── Lab supply and emergency relay ── */}
      <rect x="24" y="178" width="130" height="74" rx="10" className="box" />
      <text x="40" y="206" className="label">{t("dg.supply")}</text>
      <text x="40" y="232" className="value">{fmt(snap.site?.available_w)} W</text>
      <g transform="translate(232 215)">
        <circle cx="-14" cy="0" r="4" className="node" />
        <circle cx="14" cy="0" r="4" className="node" />
        <line x1="-14" y1="0" x2={relayOpen ? 8 : 14} y2={relayOpen ? -16 : 0} stroke={relayOpen ? "var(--danger)" : "var(--text-dim)"} strokeWidth="3" strokeLinecap="round" />
      </g>
      <text x="206" y="244" className={`label ${relayOpen ? "danger-text" : ""}`}>
        {relayOpen ? t("dg.relay.open") : t("dg.relay.closed")}
      </text>

      {/* ── Stack ── */}
      <g className={producing ? "producing" : ""}>
        <rect x="330" y="110" width="210" height="190" rx="12" className={`box ${alarm ? "stroke-danger" : tooHot ? "stroke-warn" : ""}`} />
        {Array.from({ length: 9 }, (_, i) => (
          <rect key={i} x={348 + i * 20} y="128" width="10" height="154" rx="3" className="cell" />
        ))}
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <circle key={i} className="bubble" cx={354 + i * 34} cy="270" r="4" style={{ animationDelay: `${i * 0.37}s` }} />
        ))}
      </g>
      <text x="330" y="98" className="label">
        {t("dg.stack")} · <tspan className={producing ? "accent-text" : ""}>{tel ? t(stackStatusKey(tel)) : "—"}</tspan>
      </text>
      <text x="556" y="140" className="value">{fmt(power)} W</text>
      <text x="556" y="164" className={`value ${tooHot ? "warn-text" : ""}`}>{tel?.temp_valid ? `${fmt(tel.electrolyte_c, 1)} °C` : "— °C"}</text>
      <text x="556" y="186" className="label">{stack ? `${fmt(stack.cell_voltage, 3)} V/cell · ${fmt(stack.current_a, 1)} A` : ""}</text>
      <text x="556" y="206" className="label">{stack && stack.h2_g_per_h > 0 ? `${fmt(stack.h2_g_per_h, 1)} g H₂/h` : ""}</text>

      {/* ── Gas outlets ── */}
      <text x="800" y="70" className="label">H₂ → {t("dg.vent")}</text>
      <text x="800" y="96" className="label">O₂ → {t("dg.vent")}</text>

      {/* ── H₂ sensor near the ceiling, above the stack ── */}
      <circle cx="680" cy="140" r="18" fill="var(--surface)" stroke={sensorColor} strokeWidth="3" />
      <text x="680" y="145" textAnchor="middle" className="label strong" style={{ fill: sensorColor }}>H₂</text>
      <text x="706" y="136" className="value">{tel?.h2_valid ? `${fmt(tel.h2_ppm)} ppm` : "—"}</text>
      <text x="706" y="156" className={`label ${alarm ? "danger-text" : warning ? "warn-text" : ""}`}>
        {alarm ? t("dg.alarm") : warning ? t("dg.warning") : `${t("dg.sensor")} · ${t(`dg.sensor.${h2Trust}`)}`}
      </text>

      {/* ── Extraction fan in the wall ── */}
      <Fan x={900} y={66} r={20} on={fan} color="var(--water)" />
      <text x="866" y="112" className="label">{t("dg.fan")} {fan ? t("common.on") : t("common.off")}</text>

      {/* ── Electrolyte reservoir (KOH loop) ── */}
      <rect x="360" y="344" width="150" height="86" rx="10" className="box" />
      <rect x="364" y={348 + 78 * (1 - level)} width="142" height={78 * level} rx="7" className="liquid" />
      <text x="372" y="372" className="label">{t("dg.loop")}</text>
      <text x="372" y="398" className="value">{tel?.koh_valid ? `${fmt(tel.koh_wt_pct, 1)} %` : "—"}</text>
      <text x="372" y="418" className="label">{snap.twin ? `${fmt(snap.twin.electrolyte_l, 1)} L` : ""}</text>

      {/* ── Cooler: radiator and its fan ── */}
      <rect x="646" y="304" width="150" height="110" rx="10" className="box" />
      {Array.from({ length: 10 }, (_, i) => (
        <line key={i} x1={660 + i * 13} y1="314" x2={660 + i * 13} y2="404" stroke="var(--border-strong)" />
      ))}
      <Fan x={836} y={359} r={22} on={cooling} color="var(--water)" />
      <text x="646" y="438" className="label">{t("dg.cooler")} {cooling ? t("common.on") : t("common.off")}</text>

      {/* ── Water reserve and KOH concentrate ── */}
      <rect x="222" y="360" width="78" height="70" rx="10" className="box" />
      <text x="232" y="390" className="label">{t("dg.water")}</text>
      <text x="232" y="410" className={`label ${tel?.water_makeup ? "accent-text" : ""}`}>{t("dg.makeup")}</text>
      <rect x="222" y="270" width="78" height="62" rx="10" className="box" />
      <text x="232" y="296" className="label">{t("dg.koh")}</text>
      <text x="232" y="316" className={`label ${tel?.koh_dosing ? "accent-text" : ""}`}>{t("dg.dosing")}</text>
    </svg>
  );
}
