// The simulator: a private whole-site sandbox for whoever is signed in.
//
//   No simulation yet  → pick a scenario, or build your own site.
//   Running            → the cockpit: time controls, the site diagram, what
//                        flows where right now, comfort and storage, trends,
//                        faults to inject, conditions to set, the controller,
//                        a bump test, and the simulation's own journal.
//
// It talks to /api/sim/* only: nothing here can touch the live bench.
import { useEffect, useMemo, useState } from "react";
import {
  ArrowsClockwiseIcon,
  CaretDoubleRightIcon,
  ClockIcon,
  CloudSunIcon,
  PauseIcon,
  PlayIcon,
  StopIcon,
  WrenchIcon,
} from "@phosphor-icons/react";
import { useI18n, type Key } from "../lib/i18n";
import { api } from "../lib/api";
import { useStream, type History } from "../lib/live";
import { useApi } from "../lib/useApi";
import type { JournalEntry, ScenarioCard, SimCatalog, SimConfig, Snapshot } from "../lib/types";
import { Merit } from "../components/Merit";
import { Card, Gauge, Readout, formatDate, formatLocal, useAction } from "../components/ui";
import { Annunciator } from "../components/Annunciator";
import { AlarmBanner } from "../components/Alarm";
import { SiteDiagram } from "../components/SiteDiagram";
import { ControllerPanel } from "../components/ControllerPanel";
import { TimeChart } from "../components/TimeChart";

export function SimulatorPage() {
  const { t } = useI18n();
  const [active, setActive] = useState<boolean | null>(null);
  const [setup, setSetup] = useState(false);
  // A new simulation means a new stream: bump the generation to reconnect.
  const [generation, setGeneration] = useState(0);
  const stream = useStream(active && !setup ? `/api/sim/stream?g=${generation}` : null);

  useEffect(() => {
    api<{ running: boolean }>("/api/sim/status")
      .then((r) => setActive(r.running))
      .catch(() => setActive(false));
  }, []);

  // A stream that stays down usually means the simulation was closed (idle,
  // or replaced from another tab): check, and fall back to the start screen.
  useEffect(() => {
    if (!active || setup || stream.connected) return;
    const timer = window.setTimeout(() => {
      api<{ running: boolean }>("/api/sim/status")
        .then((r) => setActive(r.running))
        .catch(() => setActive(false));
    }, 5000);
    return () => window.clearTimeout(timer);
  }, [active, setup, stream.connected]);

  if (active === null) return <p className="dim">{t("common.loading")}</p>;
  if (!active || setup) {
    return (
      <SimSetup
        current={stream.snapshot?.sim?.config}
        onStarted={() => {
          setGeneration((g) => g + 1);
          setActive(true);
          setSetup(false);
        }}
        onBack={active ? () => setSetup(false) : undefined}
      />
    );
  }
  return (
    <Cockpit
      snap={stream.snapshot}
      history={stream.history}
      onChange={() => setSetup(true)}
      onClosed={() => setActive(false)}
      onRestarted={() => setGeneration((g) => g + 1)}
    />
  );
}

// ── Start screen ─────────────────────────────────────────────────────────────

const GROUPS = ["energy", "comfort", "safety", "trust"] as const;

function SimSetup({ current, onStarted, onBack }: { current?: SimConfig; onStarted: () => void; onBack?: () => void }) {
  const { t, lang } = useI18n();
  const catalog = useApi<SimCatalog>("/api/sim/catalog");
  const [config, setConfig] = useState<SimConfig | null>(current ?? null);
  const { run, busy, error } = useAction();
  const cfg = config ?? catalog.data?.defaults ?? null;

  const start = async (scenario?: string) => {
    if ((await run("/api/sim/start", { config: cfg ?? {}, scenario })) !== null) onStarted();
  };

  return (
    <div className="stack">
      <header className="topbar">
        <div className="title">
          <h1>{t("sim.title")}</h1>
          <span className="dim">{t("sim.lead")}</span>
        </div>
        {onBack && <button className="btn" onClick={onBack}>{t("common.cancel")}</button>}
      </header>
      {error && <div className="error" role="alert">{error}</div>}

      <h2 className="section-title">{t("sim.gallery")}</h2>
      {GROUPS.map((group) => {
        const cards = (catalog.data?.scenarios ?? []).filter((s) => s.group === group);
        if (cards.length === 0) return null;
        return (
          <section key={group} className="stack" style={{ gap: 10 }}>
            <h3 className={`group-title ${group}`}>{t(`sim.group.${group}` as Key)}</h3>
            <div className="scenario-grid">
              {cards.map((s) => (
                <ScenarioTile key={s.id} s={s} lang={lang} disabled={busy} onStart={() => void start(s.id)} />
              ))}
            </div>
          </section>
        );
      })}

      <h2 className="section-title">{t("sim.custom")}</h2>
      {cfg ? (
        <SiteForm cfg={cfg} setCfg={setConfig} busy={busy} onStart={() => void start()} />
      ) : (
        <p className="dim">{t("common.loading")}</p>
      )}
    </div>
  );
}

function ScenarioTile({ s, lang, disabled, onStart }: { s: ScenarioCard; lang: "en" | "fr"; disabled: boolean; onStart: () => void }) {
  const { t } = useI18n();
  const when = new Date(Date.UTC(2025, s.when.month - 1, s.when.day)).toLocaleDateString(lang === "fr" ? "fr-FR" : "en-GB", {
    day: "numeric",
    month: "long",
    timeZone: "UTC",
  });
  const hour = `${String(Math.floor(s.when.hour)).padStart(2, "0")}:${String(Math.round((s.when.hour % 1) * 60)).padStart(2, "0")}`;
  return (
    <button className={`card scenario-tile ${s.group}`} disabled={disabled} onClick={onStart}>
      <strong>{s.title[lang]}</strong>
      <p>{s.story[lang]}</p>
      <span className="row tile-meta">
        <span className="chip-mini"><ClockIcon size={12} /> {when} · {hour}</span>
        <span className="chip-mini">{t("sim.speed").replace("{speed}", String(s.speed))}</span>
        {s.faults.map((f) => (
          <span key={f} className="chip-mini fault-chip">{t(`fault.${f}` as Key)}</span>
        ))}
      </span>
    </button>
  );
}

// Field limits mirror SimConfig in backend/src/hestia/sim/config.py (the gateway checks them again).
const NUM: Record<string, { min: number; max: number; step: number }> = {
  floor_area_m2: { min: 40, max: 1000, step: 10 },
  heat_setpoint_c: { min: 16, max: 24, step: 0.5 },
  cool_setpoint_c: { min: 22, max: 30, step: 0.5 },
  heat_pump_kw: { min: 1, max: 30, step: 0.5 },
  pv_kwp: { min: 0, max: 50, step: 0.5 },
  pv_tilt_deg: { min: 0, max: 90, step: 5 },
  pv_azimuth_deg: { min: -180, max: 180, step: 5 },
  battery_kwh: { min: 0, max: 100, step: 1 },
  battery_kw: { min: 0.5, max: 30, step: 0.5 },
  export_limit_kw: { min: 0, max: 50, step: 0.5 },
  stack_kw: { min: 0.5, max: 10, step: 0.5 },
  tank_kg: { min: 0.5, max: 50, step: 0.5 },
  tank_mawp_bar: { min: 10, max: 30, step: 1 },
  fuel_cell_kw: { min: 0.5, max: 10, step: 0.5 },
  boiler_kw: { min: 1, max: 30, step: 0.5 },
  h2_reserve_pct: { min: 0, max: 50, step: 1 },
};

function SiteForm({ cfg, setCfg, busy, onStart }: { cfg: SimConfig; setCfg: (c: SimConfig) => void; busy: boolean; onStart: () => void }) {
  const { t } = useI18n();
  const set = <K extends keyof SimConfig>(k: K, v: SimConfig[K]) => setCfg({ ...cfg, [k]: v });
  const num = (k: keyof SimConfig) => (
    <div className="field" key={k}>
      <label htmlFor={`cfg-${k}`}>{t(`cfg.${k}` as Key)}</label>
      <input id={`cfg-${k}`} className="input num" type="number" {...NUM[k]} value={String(cfg[k])}
        onChange={(e) => set(k, Number(e.target.value) as never)} />
    </div>
  );
  const choice = <K extends keyof SimConfig>(k: K, options: string[], label: (o: string) => string) => (
    <div className="field" key={k}>
      <span className="label">{t(`cfg.${k}` as Key)}</span>
      <div className="segmented wrap" role="group" aria-label={t(`cfg.${k}` as Key)}>
        {options.map((o) => (
          <button type="button" key={o} aria-pressed={cfg[k] === o} onClick={() => set(k, o as SimConfig[K])}>
            {label(o)}
          </button>
        ))}
      </div>
    </div>
  );
  return (
    <Card>
      <p className="hint" style={{ marginTop: 0 }}>{t("sim.custom.lead")}</p>
      <div className="site-form">
        <fieldset>
          <legend>{t("sim.place")}</legend>
          {choice("location", ["tunis", "paris"], (o) => t(`opt.${o}` as Key))}
        </fieldset>
        <fieldset>
          <legend>{t("sim.building")}</legend>
          {num("floor_area_m2")}
          {choice("insulation", ["poor", "average", "good"], (o) => t(`ins.${o}` as Key))}
          <div className="grid grid-2" style={{ gap: 8 }}>
            {num("heat_setpoint_c")}
            {num("cool_setpoint_c")}
          </div>
          {num("heat_pump_kw")}
        </fieldset>
        <fieldset>
          <legend>{t("sim.electricity")}</legend>
          <div className="grid grid-2" style={{ gap: 8 }}>
            {num("pv_kwp")}
            {num("pv_tilt_deg")}
            {num("battery_kwh")}
            {num("battery_kw")}
            {num("pv_azimuth_deg")}
            {num("export_limit_kw")}
          </div>
          <label className="row check">
            <input type="checkbox" checked={cfg.grid_connected} onChange={(e) => set("grid_connected", e.target.checked)} />
            {t("cfg.grid_connected")}
          </label>
        </fieldset>
        <fieldset>
          <legend>{t("sim.hydrogen")}</legend>
          <div className="grid grid-2" style={{ gap: 8 }}>
            {num("stack_kw")}
            {num("tank_kg")}
            {num("tank_mawp_bar")}
            {cfg.end_use === "fuel_cell" ? num("fuel_cell_kw") : num("boiler_kw")}
          </div>
          {choice("end_use", ["fuel_cell", "boiler"], (o) => t(`opt.${o}` as Key))}
        </fieldset>
        <fieldset>
          <legend>{t("sim.rules")}</legend>
          {choice("strategy", ["value", "battery_first", "hydrogen_first"], (o) => t(`opt.${o}` as Key))}
          {choice("h2_use", ["auto", "grid_backup", "heating_only", "never"], (o) => t(`opt.${o}` as Key))}
          {num("h2_reserve_pct")}
        </fieldset>
      </div>
      <button className="btn primary" disabled={busy} onClick={onStart}>
        <PlayIcon size={15} weight="fill" /> {busy ? t("sim.starting") : t("sim.start")}
      </button>
    </Card>
  );
}

// ── Cockpit ──────────────────────────────────────────────────────────────────

const SPEEDS = [1, 5, 20, 60, 300, 600, 3600];
const JUMPS: { key: Key; month: number; day: number; hour: number }[] = [
  { key: "jump.spring_morning", month: 4, day: 2, hour: 8 },
  { key: "jump.summer_noon", month: 7, day: 10, hour: 12 },
  { key: "jump.heat_wave", month: 7, day: 28, hour: 15 },
  { key: "jump.winter_evening", month: 1, day: 15, hour: 18 },
  { key: "jump.winter_night", month: 1, day: 20, hour: 2 },
];

function Cockpit({
  snap,
  history,
  onChange,
  onClosed,
  onRestarted,
}: {
  snap: Snapshot | null;
  history: History;
  onChange: () => void;
  onClosed: () => void;
  onRestarted: () => void;
}) {
  const { t, lang } = useI18n();
  const catalog = useApi<SimCatalog>("/api/sim/catalog");
  const { run, busy, error } = useAction();
  if (!snap || !snap.sim || !snap.twin) return <p className="dim">{t("common.loading")}</p>;
  const twin = snap.twin;
  const sim = snap.sim;
  const scenario = catalog.data?.scenarios.find((s) => s.id === sim.scenario);
  const { date, time } = formatLocal(twin.local_time, lang);
  const timeCall = (body: Record<string, unknown>) => void run("/api/sim/time", body);

  return (
    <div className="stack sim">
      <AlarmBanner snap={snap} commandPath="/api/sim/commands" canReset />
      <Annunciator snap={snap} />

      <section className="card timebar">
        <div className="clock">
          <span className="clock-time num">{time}</span>
          <span className="clock-date">{date}</span>
        </div>
        <div className="time-controls">
          <button className="btn primary icon-btn" aria-label={twin.paused ? t("sim.play") : t("sim.pause")}
            onClick={() => timeCall({ paused: !twin.paused })}>
            {twin.paused ? <PlayIcon size={18} weight="fill" /> : <PauseIcon size={18} weight="fill" />}
          </button>
          <div className="segmented" role="group" aria-label="Speed">
            {SPEEDS.map((s) => (
              <button key={s} aria-pressed={twin.speed === s} onClick={() => timeCall({ speed: s })}>
                {s}×
              </button>
            ))}
          </div>
          <div className="row" style={{ gap: 6 }}>
            {[1, 6, 24].map((h) => (
              <button key={h} className="btn small" disabled={busy || sim.busy} onClick={() => timeCall({ advance_hours: h })}>
                <CaretDoubleRightIcon size={13} /> +{h} h
              </button>
            ))}
          </div>
          {(busy || sim.busy) && <span className="pill accent">{t("sim.busy")}</span>}
        </div>
        <div className="row jumps">
          <span className="label">{t("sim.jump")}</span>
          {JUMPS.map((j) => (
            <button key={j.key} className="chip" onClick={() => timeCall({ jump: { month: j.month, day: j.day, hour: j.hour } })}>
              {t(j.key)}
            </button>
          ))}
        </div>
        <div className="row timebar-end">
          <span className="pill">{scenario ? `${t("sim.scenario")} · ${scenario.title[lang]}` : t("sim.own")}</span>
          <button className="btn small" onClick={onChange}>
            <ArrowsClockwiseIcon size={14} /> {t("sim.change")}
          </button>
          <button className="btn small ghost" onClick={() => void api("/api/sim/stop", { method: "POST" }).finally(onClosed)}>
            <StopIcon size={14} /> {t("sim.close")}
          </button>
        </div>
        {error && <div className="error">{error}</div>}
      </section>

      {scenario && (
        <section className={`card watch ${scenario.group}`}>
          <strong>{t("sim.watch")}</strong>
          <p>{scenario.story[lang]}</p>
        </section>
      )}

      <div className="process">
        <div className="stack">
          <Card className="diagram-card">
            <SiteDiagram snap={snap} />
            <div className="row weather faint">
              <CloudSunIcon size={15} /> {t("sim.weather")}: {sim.weather_source}
            </div>
          </Card>
          <SimTrends snap={snap} history={history} />
        </div>
        <div className="faceplates">
          <Balance snap={snap} />
          <Comfort snap={snap} />
          <Storage snap={snap} />
          <Totals snap={snap} />
        </div>
      </div>

      <div className="grid grid-2">
        <Faults snap={snap} />
        <Conditions snap={snap} onDone={onRestarted} />
      </div>
      <div className="grid grid-2">
        <ControllerPanel tel={snap.telemetry} commandPath="/api/sim/commands" canControl />
        <BumpTest />
      </div>
      <Events head={snap.journal_head} />
    </div>
  );
}

function Balance({ snap }: { snap: Snapshot }) {
  const { t, fmt } = useI18n();
  const s = snap.site;
  if (!s) return null;
  const batt = s.battery_w ?? 0;
  const grid = s.grid_w ?? 0;
  const sources = [
    { key: "bal.pv", w: s.pv_w, color: "var(--solar)" },
    { key: "bal.battery", w: Math.max(0, -batt), color: "var(--solar)" },
    { key: "bal.fuel_cell", w: s.fuel_cell_w ?? 0, color: "var(--accent)" },
    { key: "bal.grid", w: Math.max(0, grid), color: "var(--grid)" },
  ] as const;
  const uses = [
    { key: "bal.load", w: s.load_w ?? 0, color: "var(--text-dim)" },
    { key: "bal.heat_pump", w: s.heat_pump_w ?? 0, color: s.hvac_mode === "cool" ? "var(--water)" : "var(--heat)" },
    { key: "bal.electrolyser", w: s.electrolyser_w, color: "var(--accent)" },
    { key: "bal.battery", w: Math.max(0, batt), color: "var(--solar)" },
    { key: "bal.export", w: Math.max(0, -grid), color: "var(--grid)" },
    { key: "bal.curtailed", w: s.curtailed_w ?? 0, color: "var(--warn)" },
  ] as const;
  const top = Math.max(1, ...sources.map((x) => x.w), ...uses.map((x) => x.w));
  const rows = (items: readonly { key: string; w: number; color: string }[]) =>
    items
      .filter((x) => x.w > 1)
      .map((x) => (
        <div key={x.key} className="bal-row">
          <span className="bal-label">{t(x.key as Key)}</span>
          <span className="bal-bar"><span style={{ width: `${(x.w / top) * 100}%`, background: x.color }} /></span>
          <span className="bal-value num">{fmt(x.w)} W</span>
        </div>
      ));
  return (
    <Card title={t("sim.balance")}>
      {s.dispatch && <p className="dispatch">{s.dispatch}</p>}
      <h3>{t("sim.sources")}</h3>
      <div className="bal">{rows(sources)}</div>
      <h3 style={{ marginTop: 10 }}>{t("sim.uses")}</h3>
      <div className="bal">{rows(uses)}</div>
      {snap.sim && <Merit rows={snap.sim.merit} />}
    </Card>
  );
}

function Comfort({ snap }: { snap: Snapshot }) {
  const { t, fmt } = useI18n();
  const s = snap.site;
  const hvac = snap.energy?.hvac;
  if (!s) return null;
  const mode = s.hvac_mode ?? "off";
  return (
    <Card title={t("sim.comfort")}>
      <div className="readouts cols-2">
        <Readout label={t("sim.inside")} value={fmt(s.indoor_c, 1)} unit="°C" />
        <Readout label={t("sim.outside")} value={fmt(s.outdoor_c, 1)} unit="°C" />
      </div>
      <Gauge label={t("sim.inside")} value={s.indoor_c} min={10} max={34}
        band={[snap.sim?.config.heat_setpoint_c ?? 20, snap.sim?.config.cool_setpoint_c ?? 26]} tone="accent" />
      <div className="row" style={{ marginTop: 10 }}>
        <span className={`pill ${mode === "heat" ? "heat" : mode === "cool" ? "cool" : ""}`}>
          {t("dg.heat_pump")} · {mode === "heat" ? t("dg.heat") : mode === "cool" ? t("dg.cool") : t("dg.idle")}
        </span>
        {hvac?.allow_h2 && <span className="pill accent">H₂</span>}
      </div>
      {hvac?.reason && <p className="small-text dim" style={{ marginBottom: 0 }}>{hvac.reason}</p>}
    </Card>
  );
}

function Storage({ snap }: { snap: Snapshot }) {
  const { t, fmt } = useI18n();
  const s = snap.site;
  const tank = snap.sim?.tank;
  if (!s || !tank) return null;
  const bar = s.tank_bar ?? 0;
  return (
    <Card title={t("sim.storage")}>
      <div className="readouts cols-2">
        <Readout label={t("sim.pressure")} value={fmt(bar, 1)} unit="bar" tone={bar >= 0.95 * tank.mawp_bar ? "warn" : undefined} />
        <Readout label={t("sim.stored")} value={`${fmt(s.h2_tank_kg, 2)} / ${fmt(tank.capacity_kg, 1)}`} unit="kg" />
      </div>
      <Gauge label={t("sim.pressure")} value={bar} min={0} max={tank.mawp_bar * 1.05}
        marks={[{ at: 0.95 * tank.mawp_bar, label: t("line.interlock"), tone: "warn" }, { at: tank.mawp_bar, label: t("line.mawp"), tone: "danger" }]}
        tone={bar >= 0.95 * tank.mawp_bar ? "warn" : "accent"} />
      <div className="readouts cols-2" style={{ marginTop: 8 }}>
        <Readout label={t("sim.volume")} value={fmt(tank.volume_m3, 2)} unit="m³" />
        <Readout label={t("sim.vented")} value={fmt((s.h2_vented_kg ?? 0) * 1000)} unit="g" tone={(s.h2_vented_kg ?? 0) > 0 ? "warn" : undefined} />
      </div>
    </Card>
  );
}

function SimTrends({ snap, history }: { snap: Snapshot; history: History }) {
  const { t } = useI18n();
  const mawp = snap.sim?.tank.mawp_bar ?? 30;
  const tel = snap.telemetry;
  const tz = snap.sim?.config.location === "paris" ? "Europe/Paris" : "Africa/Tunis";
  const power = useMemo(
    () => [
      { label: t("bal.pv"), color: "var(--solar)", values: history.pv, unit: "W", fill: true },
      { label: t("bal.load"), color: "var(--grid)", values: history.load, unit: "W" },
      { label: t("bal.heat_pump"), color: "var(--heat)", values: history.heatPump, unit: "W" },
      { label: t("bal.electrolyser"), color: "var(--accent)", values: history.electrolyser, unit: "W" },
      { label: t("bal.fuel_cell"), color: "var(--fuel)", values: history.fuelCell, unit: "W", dash: [7, 3] },
      { label: t("bal.grid"), color: "var(--grid)", values: history.grid, unit: "W", dash: [4, 4] },
    ],
    [history, t],
  );
  const temps = useMemo(
    () => [
      { label: t("sim.inside"), color: "var(--accent)", values: history.indoor, unit: "°C" },
      { label: t("sim.outside"), color: "var(--grid)", values: history.outdoor, unit: "°C", dash: [4, 4] },
    ],
    [history, t],
  );
  const tank = useMemo(() => [{ label: t("trend.tank"), color: "var(--accent)", values: history.tankBar, unit: "bar", fill: true }], [history, t]);
  const stack = useMemo(() => [{ label: t("trend.temperature"), color: "var(--heat)", values: history.electrolyte, unit: "°C" }], [history, t]);
  return (
    <div className="grid grid-2">
      <Card title={t("trend.power")}>
        <TimeChart timeZone={tz} times={history.t} series={power} height={220} zero />
      </Card>
      <Card title={t("trend.temps")}>
        <TimeChart timeZone={tz} times={history.t} series={temps} height={220} thresholds={[
          { value: snap.sim?.config.heat_setpoint_c ?? 20, color: "var(--heat)", label: t("dg.heat") },
          { value: snap.sim?.config.cool_setpoint_c ?? 26, color: "var(--water)", label: t("dg.cool") },
        ]} />
      </Card>
      <Card title={t("trend.tank")}>
        <TimeChart timeZone={tz} times={history.t} series={tank} zero thresholds={[
          { value: 0.95 * mawp, color: "var(--warn)", label: t("line.interlock") },
          { value: mawp, color: "var(--danger)", label: t("line.mawp") },
        ]} />
      </Card>
      <Card title={t("trend.temperature")}>
        <TimeChart timeZone={tz} times={history.t} series={stack} thresholds={[
          { value: 55, color: "var(--water)", label: t("line.loop_on") },
          { value: 60, color: "var(--warn)", label: t("line.stop") },
          { value: tel?.temp_alert_c ?? 70, color: "var(--danger)", label: t("line.alarm") },
        ]} />
      </Card>
    </div>
  );
}

function Faults({ snap }: { snap: Snapshot }) {
  const { t } = useI18n();
  const { run, error } = useAction();
  const [pending, setPending] = useState<Record<string, boolean>>({});
  const twin = snap.twin;
  if (!twin) return null;
  const toggle = (name: string, on: boolean) => {
    setPending((p) => ({ ...p, [name]: on }));
    void run("/api/sim/fault", { name, on }).finally(() =>
      window.setTimeout(() => setPending((p) => Object.fromEntries(Object.entries(p).filter(([k]) => k !== name))), 1200),
    );
  };
  return (
    <Card title={<span className="row"><WrenchIcon size={17} /> {t("faults.title")}</span>}>
      <p className="hint" style={{ marginTop: 0 }}>{t("faults.hint")}</p>
      <div className="fault-list">
        {twin.available_faults.map((name) => {
          const on = pending[name] ?? twin.faults.includes(name);
          return (
            <label key={name} className={`fault ${on ? "on" : ""}`}>
              <input type="checkbox" checked={on} onChange={() => toggle(name, !on)} />
              <span>{t(`fault.${name}` as Key)}</span>
            </label>
          );
        })}
      </div>
      {error && <div className="error">{error}</div>}
    </Card>
  );
}

function Conditions({ snap, onDone }: { snap: Snapshot; onDone: () => void }) {
  const { t, fmt } = useI18n();
  const { run, busy, error } = useAction();
  const s = snap.site;
  const [values, setValues] = useState<Record<string, number>>({});
  const fields: { key: string; label: Key; min: number; max: number; step: number; now: number; unit: string }[] = [
    { key: "tank_pct", label: "sim.storage", min: 0, max: 100, step: 1, now: s?.h2_tank_pct ?? 0, unit: "%" },
    { key: "battery_pct", label: "dg.battery", min: 0, max: 100, step: 1, now: s?.battery_soc_pct ?? 0, unit: "%" },
    { key: "indoor_c", label: "sim.inside", min: 5, max: 35, step: 0.5, now: s?.indoor_c ?? 20, unit: "°C" },
    { key: "electrolyte_c", label: "bench.electrolyte", min: 5, max: 65, step: 1, now: snap.telemetry?.electrolyte_c ?? 25, unit: "°C" },
  ];
  const apply = async () => {
    if ((await run("/api/sim/conditions", values)) !== null) {
      setValues({});
      onDone();
    }
  };
  return (
    <Card title={t("sim.conditions")}>
      <p className="hint" style={{ marginTop: 0 }}>{t("sim.conditions.hint")}</p>
      <div className="sliders">
        {fields.map((f) => {
          const v = values[f.key] ?? f.now;
          return (
            <div key={f.key} className="slider">
              <label htmlFor={`cond-${f.key}`}>{t(f.label)}</label>
              <input id={`cond-${f.key}`} type="range" min={f.min} max={f.max} step={f.step} value={v}
                onChange={(e) => setValues({ ...values, [f.key]: Number(e.target.value) })} />
              <span className={`num ${values[f.key] !== undefined ? "accent-text" : "dim"}`}>
                {fmt(v, f.step < 1 ? 1 : 0)} {f.unit}
              </span>
            </div>
          );
        })}
      </div>
      <button className="btn primary small" disabled={busy || Object.keys(values).length === 0} onClick={() => void apply()}>
        {t("common.apply")}
      </button>
      {error && <div className="error">{error}</div>}
    </Card>
  );
}

function BumpTest() {
  const { t } = useI18n();
  const { run, busy, error } = useAction();
  const [peak, setPeak] = useState<number | null>(null);
  const [verdict, setVerdict] = useState<{ result: string; verdict: string } | null>(null);
  const GAS = 1000;
  const test = async () => {
    setVerdict(null);
    const r = await run<{ peak_reading_ppm: number }>("/api/sim/bump-test", { gas_ppm: GAS });
    if (r) setPeak(r.peak_reading_ppm);
  };
  const record = async () => {
    if (peak === null) return;
    const r = await run<{ result: string; verdict: string }>("/api/sim/maintenance", {
      kind: "bump_test",
      sensor: "h2",
      data: { gas_ppm: GAS, peak_reading_ppm: peak },
      notes: "Bump test in the simulator",
    });
    if (r) setVerdict(r);
  };
  return (
    <Card title={t("sim.bump")}>
      <p className="hint" style={{ marginTop: 0 }}>{t("sim.bump.lead")}</p>
      <div className="row">
        <button className="btn small" disabled={busy} onClick={() => void test()}>{t("sim.bump.run")}</button>
        {peak !== null && (
          <>
            <span className="num">{t("sim.bump.result").replace("{peak}", String(Math.round(peak))).replace("{gas}", String(GAS))}</span>
            <button className="btn small primary" disabled={busy} onClick={() => void record()}>{t("sim.bump.record")}</button>
          </>
        )}
      </div>
      {verdict && (
        <p className={`pill ${verdict.result === "pass" ? "ok" : "danger"}`} style={{ whiteSpace: "normal", marginBottom: 0 }}>
          {verdict.result.toUpperCase()}: {verdict.verdict}
        </p>
      )}
      {error && <div className="error">{error}</div>}
    </Card>
  );
}

function Events({ head }: { head: string }) {
  const { t, lang } = useI18n();
  const journal = useApi<{ entries: JournalEntry[] }>("/api/sim/journal?limit=40", head);
  const entries = journal.data?.entries ?? [];
  return (
    <Card title={t("sim.events")}>
      <ul className="mini-journal tall">
        {entries.map((e) => (
          <li key={e.id} className={`sev-${e.severity}`}>
            <span>{e.summary}</span>
            <span className="faint num">{formatDate(e.ts, lang)}</span>
          </li>
        ))}
        {entries.length === 0 && <li className="dim">{t("sim.events.empty")}</li>}
      </ul>
    </Card>
  );
}

const TOTAL_UNITS: Record<string, [string, number, number]> = {
  pv_kwh: ["kWh", 1, 1],
  load_kwh: ["kWh", 1, 1],
  heat_pump_kwh: ["kWh", 1, 1],
  electrolyser_kwh: ["kWh", 1, 1],
  fuel_cell_kwh: ["kWh", 1, 1],
  grid_import_kwh: ["kWh", 1, 1],
  grid_export_kwh: ["kWh", 1, 1],
  curtailed_kwh: ["kWh", 1, 1],
  unserved_kwh: ["kWh", 1, 1],
  heating_kwh: ["kWh", 1, 1],
  cooling_kwh: ["kWh", 1, 1],
  h2_produced_kg: ["g", 1000, 0],
  h2_used_kg: ["g", 1000, 0],
  h2_vented_kg: ["g", 1000, 0],
  water_l: ["L", 1, 2],
  electrolyser_hours: ["h", 1, 1],
};

function Totals({ snap }: { snap: Snapshot }) {
  const { t, fmt } = useI18n();
  const totals = snap.twin?.totals ?? {};
  return (
    <Card title={t("sim.totals")}>
      <div className="readouts cols-2">
        {Object.entries(TOTAL_UNITS).map(([key, [unit, scale, digits]]) =>
          totals[key] === undefined ? null : (
            <Readout key={key} label={t(`tot.${key}` as Key)} value={fmt((totals[key] ?? 0) * scale, digits)} unit={unit}
              tone={key === "unserved_kwh" && (totals[key] ?? 0) > 0 ? "warn" : undefined} />
          ),
        )}
      </div>
    </Card>
  );
}
