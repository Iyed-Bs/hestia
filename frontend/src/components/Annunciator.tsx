// The annunciator: one strip that answers "is anything wrong?" before any
// diagram is read. Borrowed from control-room practice (ISA-101 displays,
// ISA-18.2 alarm priorities): neutral when normal, colour only for abnormal
// states, and every state spelled out in words so colour is never the only
// signal. Critical states pulse until they clear.
import type { Snapshot } from "../lib/types";
import { useI18n, type Key } from "../lib/i18n";

type Severity = "normal" | "advisory" | "warning" | "critical";

interface Tile {
  name: Key;
  state: Key;
  value?: string;
  severity: Severity;
  title?: string;
}

export function annunciate(snap: Snapshot, fmt: (v: number | null | undefined, d?: number) => string): Tile[] {
  const tel = snap.telemetry;
  const tiles: Tile[] = [];
  if (!tel) {
    return [{ name: "ann.link", state: "ann.link.down", severity: "critical" }];
  }

  // Gas detection.
  if (tel.h2_alarm_latched) tiles.push({ name: "ann.gas", state: "ann.gas.alarm", value: `${fmt(tel.h2_ppm)} ppm`, severity: "critical" });
  else if (tel.h2_warning) tiles.push({ name: "ann.gas", state: "ann.gas.warning", value: `${fmt(tel.h2_ppm)} ppm`, severity: "warning" });
  else if (!tel.h2_valid) tiles.push({ name: "ann.gas", state: "ann.gas.invalid", severity: "warning" });
  else tiles.push({ name: "ann.gas", state: "ann.gas.clear", value: `${fmt(tel.h2_ppm)} ppm`, severity: "normal" });

  // Electrolyte temperature.
  const temp = `${fmt(tel.electrolyte_c, 1)} °C`;
  if (!tel.temp_valid) tiles.push({ name: "ann.temperature", state: "ann.temp.invalid", severity: "warning" });
  else if (tel.electrolyte_c >= tel.temp_alert_c) tiles.push({ name: "ann.temperature", state: "ann.temp.over", value: temp, severity: "critical" });
  else if (tel.phase === "COOLING") tiles.push({ name: "ann.temperature", state: "ann.temp.cooling", value: temp, severity: "advisory" });
  else if (tel.cooling_pump) tiles.push({ name: "ann.temperature", state: "ann.temp.loop", value: temp, severity: "normal" });
  else tiles.push({ name: "ann.temperature", state: "ann.temp.normal", value: temp, severity: "normal" });

  // Electrolyte strength and level.
  const koh = `${fmt(tel.koh_wt_pct, 1)} %`;
  if (!tel.koh_valid) tiles.push({ name: "ann.electrolyte", state: "ann.koh.invalid", severity: "warning" });
  else if (tel.level_low) tiles.push({ name: "ann.electrolyte", state: "ann.koh.level", value: koh, severity: "advisory" });
  else if (tel.koh_wt_pct < tel.koh_low_pct) tiles.push({ name: "ann.electrolyte", state: "ann.koh.low", value: koh, severity: "advisory" });
  else if (tel.koh_wt_pct > tel.koh_high_pct) tiles.push({ name: "ann.electrolyte", state: "ann.koh.high", value: koh, severity: "advisory" });
  else tiles.push({ name: "ann.electrolyte", state: "ann.koh.band", value: koh, severity: "normal" });

  // Pressurised storage.
  if (tel.storage_mawp_bar <= 0) tiles.push({ name: "ann.storage", state: "ann.storage.none", severity: "normal" });
  else if (!tel.tank_valid) tiles.push({ name: "ann.storage", state: "ann.storage.invalid", severity: "warning" });
  else if (tel.tank_bar >= 0.95 * tel.storage_mawp_bar)
    tiles.push({ name: "ann.storage", state: "ann.storage.interlock", value: `${fmt(tel.tank_bar, 1)} bar`, severity: "advisory" });
  else tiles.push({ name: "ann.storage", state: "ann.storage.ok", value: `${fmt(tel.tank_bar, 1)} bar`, severity: "normal" });

  // Controller link and production permit.
  tiles.push({ name: "ann.link", state: snap.online ? "ann.link.ok" : "ann.link.down", severity: snap.online ? "normal" : "critical" });
  const permit = snap.energy?.permit ?? tel.production_permit;
  tiles.push({
    name: "ann.permit",
    state: permit ? "ann.permit.granted" : "ann.permit.withheld",
    severity: permit ? "normal" : "advisory",
    title: snap.energy?.permit_reason,
  });
  return tiles;
}

export function Annunciator({ snap, extra }: { snap: Snapshot | null; extra?: React.ReactNode }) {
  const { t, fmt } = useI18n();
  if (!snap) return <div className="annunciator placeholder" aria-hidden />;
  const tiles = annunciate(snap, fmt);
  return (
    <div className="annunciator" role="status" aria-label="Plant status">
      {tiles.map((tile) => (
        <div key={tile.name} className={`ann ${tile.severity}`} title={tile.title}>
          <span className="ann-name">{t(tile.name)}</span>
          <span className="ann-state">{t(tile.state)}</span>
          {tile.value && <span className="ann-value num">{tile.value}</span>}
        </div>
      ))}
      {extra}
    </div>
  );
}
