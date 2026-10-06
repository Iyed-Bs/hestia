// The live bench (the échantillon): a "level 2" process display. The diagram
// shows where things flow; the faceplates beside it give the numbers that
// matter for each piece of equipment; the trends below show every reading
// against the limits it must respect, so a drift is seen before it trips.
import { useMemo, useState } from "react";
import { PauseIcon, PlayIcon } from "@phosphor-icons/react";
import { useI18n, type Key } from "../lib/i18n";
import { useLive, type History } from "../lib/live";
import { useSession } from "../lib/session";
import type { Snapshot } from "../lib/types";
import { Card, Gauge, LevelPill, Readout, stackStatusKey, useAction } from "../components/ui";
import { BenchDiagram } from "../components/BenchDiagram";
import { ControllerPanel } from "../components/ControllerPanel";
import { TimeChart } from "../components/TimeChart";

export function LivePage() {
  const { t } = useI18n();
  const { snapshot: snap, history } = useLive();
  const { can } = useSession();

  if (!snap) return <p className="dim">{t("common.loading")}</p>;
  const tel = snap.telemetry;

  return (
    <div className="stack">
      <header className="topbar">
        <div className="title">
          <h1>{t("bench.title")}</h1>
          <span className="dim">{t("bench.lead")}</span>
        </div>
        {tel && <span className={`pill ${tel.electrolyser ? "accent" : ""}`}>{t(stackStatusKey(tel))}</span>}
        {tel && <span className={`pill ${tel.mode === "AUTO" ? "" : tel.mode === "MANUAL" ? "warn" : "danger"}`}>{t(`ctl.${tel.mode}`)}</span>}
      </header>

      <div className="process">
        <div className="stack">
          <Card className="diagram-card">
            <BenchDiagram snap={snap} />
          </Card>
          <BenchTrends snap={snap} history={history} />
        </div>
        <Faceplates snap={snap} />
      </div>

      <div className="grid grid-2">
        <ControllerPanel tel={tel} commandPath="/api/commands" canControl={can("operator")} />
        {snap.mode === "twin" && can("operator") ? <BenchTwin snap={snap} /> : <Advice snap={snap} />}
      </div>
      {snap.mode === "twin" && can("operator") && <Advice snap={snap} />}
    </div>
  );
}

function Faceplates({ snap }: { snap: Snapshot }) {
  const { t, fmt } = useI18n();
  const tel = snap.telemetry;
  const stack = snap.twin?.stack;
  const twin = snap.twin;
  if (!tel) return null;
  const hot = tel.electrolyte_c >= tel.temp_alert_c;
  return (
    <div className="faceplates">
      <Card title={t("bench.stack")}>
        <div className="readouts">
          <Readout label={t("bench.power")} value={fmt(snap.site?.electrolyser_w)} unit="W" />
          <Readout label={t("bench.h2_rate")} value={fmt(stack?.h2_g_per_h, 1)} unit="g/h" />
          <Readout label={t("bench.cell_voltage")} value={fmt(stack?.cell_voltage, 3)} unit="V" />
          <Readout label={t("bench.current")} value={fmt(stack?.current_a, 1)} unit="A" />
          <Readout label={t("bench.faraday")} value={stack ? fmt(stack.faraday_efficiency * 100, 1) : "—"} unit="%" />
          <Readout label={t("bench.energy")} value={stack?.kwh_per_kg ? fmt(stack.kwh_per_kg, 1) : "—"} unit="kWh/kg" />
          <Readout label={t("bench.heat")} value={fmt(stack?.heat_w)} unit="W" />
        </div>
      </Card>
      <Card title={t("bench.electrolyte")}>
        <div className="readouts">
          <Readout label={t("bench.temperature")} value={tel.temp_valid ? fmt(tel.electrolyte_c, 1) : "—"} unit="°C" tone={hot ? "danger" : undefined} />
        </div>
        <Gauge label={t("bench.temperature")} value={tel.temp_valid ? tel.electrolyte_c : null} min={15} max={75} band={[50, 55]}
          marks={[{ at: 60, label: t("line.stop"), tone: "warn" }, { at: tel.temp_alert_c, label: t("line.alarm"), tone: "danger" }]} tone={hot ? "danger" : "accent"} />
        <div className="readouts">
          <Readout label={t("bench.koh")} value={tel.koh_valid ? fmt(tel.koh_wt_pct, 2) : "—"} unit="wt%"
            tone={tel.koh_valid && (tel.koh_wt_pct < tel.koh_low_pct || tel.koh_wt_pct > tel.koh_high_pct) ? "warn" : undefined} />
        </div>
        <Gauge label={t("bench.koh")} value={tel.koh_valid ? tel.koh_wt_pct : null} min={18} max={38} band={[tel.koh_low_pct, tel.koh_high_pct]} tone="accent" />
        <div className="readouts">
          <Readout label={t("bench.volume")} value={fmt(twin?.electrolyte_l, 1)} unit="L" />
          <Readout label={t("bench.conductivity")} value={fmt(twin?.conductivity_s_per_cm, 3)} unit="S/cm" />
        </div>
      </Card>
      <Card title={t("bench.gas")}>
        <div className="readouts">
          <Readout label={t("bench.h2")} value={tel.h2_valid ? fmt(tel.h2_ppm) : "—"} unit="ppm"
            tone={tel.h2_alarm_latched ? "danger" : tel.h2_warning ? "warn" : undefined} />
          <Readout label={t("bench.stages")} value={`${fmt(tel.h2_warning_ppm)} / ${fmt(tel.h2_alarm_ppm)}`} unit="ppm" />
          <Readout label={t("bench.ventilation")} value={tel.ventilation ? t("common.on") : t("common.off")} />
        </div>
        <Gauge label={t("bench.h2")} value={tel.h2_valid ? tel.h2_ppm : null} min={0} max={Math.max(2500, tel.h2_alarm_ppm * 1.2)}
          marks={[{ at: tel.h2_warning_ppm, label: t("line.stage1"), tone: "warn" }, { at: tel.h2_alarm_ppm, label: t("line.stage2"), tone: "danger" }]}
          tone={tel.h2_alarm_latched ? "danger" : tel.h2_warning ? "warn" : "accent"} />
        {snap.trust && (
          <div className="row" style={{ marginTop: 10 }}>
            <LevelPill level={snap.trust.sensors.h2.level} />
            <span className="faint small-text">{snap.trust.h2_why}</span>
          </div>
        )}
      </Card>
      <Card title={t("bench.controller")}>
        <div className="stack" style={{ gap: 8 }}>
          <div className="row">
            <span className={`pill ${snap.energy?.permit ? "ok" : "warn"}`}>
              {t("bench.permit")} · {snap.energy?.permit ? t("ann.permit.granted") : t("ann.permit.withheld")}
            </span>
          </div>
          <span className="dim small-text">{snap.energy?.permit_reason}</span>
          <span className="small-text">{tel.reason}</span>
        </div>
      </Card>
    </div>
  );
}

function BenchTrends({ snap, history }: { snap: Snapshot; history: History }) {
  const { t } = useI18n();
  const tel = snap.telemetry;
  const temp = useMemo(() => [{ label: t("trend.temperature"), color: "var(--heat)", values: history.electrolyte, unit: "°C" }], [history, t]);
  const koh = useMemo(() => [{ label: t("trend.koh"), color: "var(--water)", values: history.koh, unit: "wt%" }], [history, t]);
  const h2 = useMemo(() => [{ label: "H₂", color: "var(--accent)", values: history.h2, unit: "ppm" }], [history]);
  const power = useMemo(() => [{ label: t("trend.stack"), color: "var(--solar)", values: history.electrolyser, unit: "W", fill: true }], [history, t]);
  return (
    <div className="grid grid-2">
      <Card title={t("trend.temperature")}>
        <TimeChart times={history.t} series={temp} thresholds={[
          { value: 50, color: "var(--water)", label: t("line.loop_off") },
          { value: 55, color: "var(--water)", label: t("line.loop_on") },
          { value: 60, color: "var(--warn)", label: t("line.stop") },
          { value: tel?.temp_alert_c ?? 70, color: "var(--danger)", label: t("line.alarm") },
        ]} />
      </Card>
      <Card title={t("trend.h2")}>
        <TimeChart times={history.t} series={h2} zero thresholds={[
          { value: tel?.h2_warning_ppm ?? 500, color: "var(--warn)", label: t("line.stage1") },
          { value: tel?.h2_alarm_ppm ?? 2000, color: "var(--danger)", label: t("line.stage2") },
        ]} />
      </Card>
      <Card title={t("trend.koh")}>
        <TimeChart times={history.t} series={koh} thresholds={[
          { value: tel?.koh_low_pct ?? 25, color: "var(--warn)", label: t("line.dose") },
          { value: tel?.koh_high_pct ?? 32, color: "var(--water)", label: t("line.water") },
        ]} />
      </Card>
      <Card title={t("trend.stack")}>
        <TimeChart times={history.t} series={power} zero />
      </Card>
    </div>
  );
}

function Advice({ snap }: { snap: Snapshot }) {
  const { t, fmt } = useI18n();
  const a = snap.advice;
  return (
    <Card title={t("bench.advice")}>
      {a.available ? (
        <div className="readouts cols-2">
          <Readout label={t("bench.advice.level")} value={a.production_label ?? "—"} />
          <Readout label={t("bench.advice.conditions")} value={a.anomaly ? t("bench.advice.unusual") : t("bench.advice.normal")} tone={a.anomaly ? "warn" : undefined} />
          <Readout label="PV" value={fmt(a.pv_forecast_w)} unit="W" />
        </div>
      ) : (
        <p className="hint">{t("bench.advice.unavailable")}</p>
      )}
      <p className="hint" style={{ marginBottom: 0 }}>
        {snap.twin?.model === "bench" ? t("bench.advice.supply") : t("bench.advice.note")}
      </p>
    </Card>
  );
}

function BenchTwin({ snap }: { snap: Snapshot }) {
  const { t } = useI18n();
  const twin = snap.twin;
  const { run, error } = useAction();
  // Optimistic toggles: the click shows at once, the stream confirms it.
  const [pending, setPending] = useState<Record<string, boolean>>({});
  if (!twin) return null;
  const toggle = (name: string, on: boolean) => {
    setPending((p) => ({ ...p, [name]: on }));
    void run("/api/twin/fault", { name, on }).finally(() =>
      window.setTimeout(() => setPending((p) => Object.fromEntries(Object.entries(p).filter(([k]) => k !== name))), 1500),
    );
  };
  return (
    <Card title={t("bench.twin")} actions={<span className="pill accent num">{twin.speed}×</span>}>
      <div className="stack">
        <p className="hint" style={{ margin: 0 }}>{t("bench.twin.lead")}</p>
        <div className="row">
          <div className="segmented" role="group" aria-label="Speed">
            {[1, 10, 60, 300, 1200].map((s) => (
              <button key={s} aria-pressed={twin.speed === s} onClick={() => void run("/api/twin/speed", { speed: s, paused: twin.paused })}>
                {s}×
              </button>
            ))}
          </div>
          <button className="btn small" onClick={() => void run("/api/twin/speed", { speed: twin.speed, paused: !twin.paused })}>
            {twin.paused ? <PlayIcon size={14} /> : <PauseIcon size={14} />} {twin.paused ? t("sim.play") : t("sim.pause")}
          </button>
        </div>
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
      </div>
    </Card>
  );
}
