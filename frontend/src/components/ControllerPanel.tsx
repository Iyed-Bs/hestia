// Operator controls for a controller: mode, manual actuators, thresholds.
// The same panel drives the live installation (/api/commands, operators only)
// and a private simulation (/api/sim/commands, anyone signed in). Every
// request is checked again by the controller itself: limits outside the
// design range are refused, never clamped, and the refusal is shown here.
import { useState } from "react";
import { useI18n, type Key } from "../lib/i18n";
import type { Mode, Telemetry } from "../lib/types";
import { Card, useAction } from "./ui";

const ACTUATORS = ["electrolyser", "cooling_pump", "koh_dosing", "water_makeup", "ventilation"] as const;

// The design limits (domain/control.py), so the form shows what is allowed.
const LIMITS = {
  koh_low_pct: { min: 20, max: 30, step: 0.5, unit: "wt%" },
  koh_high_pct: { min: 28, max: 35, step: 0.5, unit: "wt%" },
  temp_alert_c: { min: 62, max: 70, step: 1, unit: "°C" },
  h2_warning_ppm: { min: 100, max: 1000, step: 50, unit: "ppm" },
  h2_alarm_ppm: { min: 500, max: 4000, step: 100, unit: "ppm" },
} as const;

export function ControllerPanel({ tel, commandPath, canControl }: { tel: Telemetry | null; commandPath: string; canControl: boolean }) {
  const { t } = useI18n();
  const { run, error, busy } = useAction();
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  if (!tel) return null;
  if (!canControl) {
    return (
      <Card title={t("controls.title")}>
        <p className="dim" style={{ margin: 0 }}>
          {t("controls.readonly")}
        </p>
      </Card>
    );
  }
  const send = (body: Record<string, unknown>) => void run(commandPath, body);

  return (
    <Card title={t("controls.title")}>
      <div className="stack">
        <div className="field">
          <span className="label">{t("controls.mode")}</span>
          <div className="segmented" role="group" aria-label={t("controls.mode")}>
            {(["AUTO", "MANUAL", "SAFE_SHUTDOWN"] as Mode[]).map((m) => (
              <button key={m} aria-pressed={tel.mode === m} disabled={busy} onClick={() => send({ kind: "set_mode", mode: m })}>
                {t(`ctl.${m}`)}
              </button>
            ))}
          </div>
        </div>

        {tel.mode === "MANUAL" && (
          <div className="field">
            <span className="label">{t("controls.manual")}</span>
            <div className="actuators">
              {ACTUATORS.map((a) => (
                <button key={a} className={`toggle ${tel[a] ? "on" : ""}`} aria-pressed={tel[a]} disabled={busy}
                  onClick={() => send({ kind: "set_actuator", actuator: a, on: !tel[a] })}>
                  <span className="toggle-dot" aria-hidden />
                  {t(`act.${a}` as Key)}
                  <span className="faint">{tel[a] ? t("common.on") : t("common.off")}</span>
                </button>
              ))}
            </div>
            <span className="hint">{t("controls.manual.hint")}</span>
          </div>
        )}

        <div className="field">
          <span className="label">{t("controls.thresholds")}</span>
          <div className="thresholds">
            {(Object.keys(LIMITS) as (keyof typeof LIMITS)[]).map((name) => {
              const lim = LIMITS[name];
              const current = tel[name];
              const draft = drafts[name] ?? String(current);
              return (
                <div key={name} className="threshold">
                  <label htmlFor={`th-${name}`}>{t(`th.${name}`)}</label>
                  <input id={`th-${name}`} className="input num" type="number" min={lim.min} max={lim.max} step={lim.step} value={draft}
                    onChange={(e) => setDrafts({ ...drafts, [name]: e.target.value })} />
                  <span className="faint num">
                    {lim.min}–{lim.max} {lim.unit}
                  </span>
                  <button className="btn small" disabled={busy || Number(draft) === current}
                    onClick={() => send({ kind: "set_threshold", name, value: Number(draft) })}>
                    {t("common.save")}
                  </button>
                </div>
              );
            })}
          </div>
          <span className="hint">{t("controls.thresholds.hint")}</span>
        </div>
        {error && (
          <div className="error" role="alert">
            {error}
          </div>
        )}
      </div>
    </Card>
  );
}
