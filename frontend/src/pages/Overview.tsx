// The signed-in home: the "level 1" display. One panel per part of the
// product, each answering its first question at a glance and opening onto
// the detail: is the bench healthy and producing, what is my simulation
// doing, can the detector be trusted, what would a building gain.
import { Link, useNavigate } from "react-router";
import { ArrowRightIcon, ChartLineUpIcon, CubeIcon, FlaskIcon, ShieldCheckIcon } from "@phosphor-icons/react";
import { useI18n } from "../lib/i18n";
import { useLive } from "../lib/live";
import { useApi } from "../lib/useApi";
import type { JournalEntry, SimCatalog, Snapshot } from "../lib/types";
import { Card, LevelPill, Readout, formatDate, formatLocal, stackStatusKey, useAction } from "../components/ui";

const FEATURED = ["summer_day", "winter_evening", "large_leak"];

export function OverviewPage() {
  const { t, fmt, lang } = useI18n();
  const { snapshot: snap } = useLive();
  const status = useApi<{ running: boolean }>("/api/sim/status");
  const sim = useApi<Snapshot>(status.data?.running ? "/api/sim/state" : null);
  const catalog = useApi<SimCatalog>("/api/sim/catalog");
  const journal = useApi<{ entries: JournalEntry[] }>("/api/journal?limit=5", snap?.journal_head);
  const navigate = useNavigate();
  const { run, busy, error } = useAction();
  const tel = snap?.telemetry;
  const stack = snap?.twin?.stack;
  const s = sim.data?.site;

  const startScenario = async (id: string) => {
    if ((await run("/api/sim/start", { scenario: id })) !== null) navigate("/simulator");
  };

  return (
    <div className="stack">
      <header className="topbar">
        <div className="title">
          <h1>{t("ov.title")}</h1>
          <span className="dim">{t("ov.lead")}</span>
        </div>
      </header>

      <div className="grid grid-2">
        <Card title={<span className="row"><FlaskIcon size={18} /> {t("ov.bench")}</span>}
          actions={<Link to="/live" className="btn small">{t("ov.open")} <ArrowRightIcon size={13} /></Link>}>
          {tel ? (
            <div className="stack">
              <div className="row">
                <span className={`pill ${tel.electrolyser ? "accent" : ""}`}>{t(stackStatusKey(tel))}</span>
                <span className={`pill ${tel.mode === "AUTO" ? "" : "warn"}`}>{t(`ctl.${tel.mode}`)}</span>
                <span className="dim small-text">{tel.reason}</span>
              </div>
              <div className="readouts cols-2">
                <Readout label={t("bench.power")} value={fmt(snap?.site?.electrolyser_w)} unit="W" />
                <Readout label={t("ov.production")} value={fmt(stack?.h2_g_per_h, 1)} unit="g/h" />
                <Readout label={t("bench.temperature")} value={tel.temp_valid ? fmt(tel.electrolyte_c, 1) : "—"} unit="°C" />
                <Readout label={t("bench.koh")} value={tel.koh_valid ? fmt(tel.koh_wt_pct, 1) : "—"} unit="wt%" />
                <Readout label={t("bench.h2")} value={tel.h2_valid ? fmt(tel.h2_ppm) : "—"} unit="ppm"
                  tone={tel.h2_alarm_latched ? "danger" : tel.h2_warning ? "warn" : undefined} />
                <Readout label={t("bench.energy")} value={stack?.kwh_per_kg ? fmt(stack.kwh_per_kg, 1) : "—"} unit="kWh/kg" />
              </div>
            </div>
          ) : (
            <p className="dim">{t("common.loading")}</p>
          )}
        </Card>

        <Card title={<span className="row"><CubeIcon size={18} /> {t("ov.sim")}</span>}
          actions={<Link to="/simulator" className="btn small">{t("ov.open")} <ArrowRightIcon size={13} /></Link>}>
          {s ? (
            <div className="stack">
              <div className="row">
                <span className="pill accent">{formatLocal(s.local_time, lang).date} · {formatLocal(s.local_time, lang).time}</span>
                <span className="dim small-text">{sim.data?.site_name}</span>
              </div>
              <div className="readouts cols-2">
                <Readout label={t("bal.pv")} value={fmt(s.pv_w)} unit="W" />
                <Readout label={t("bal.electrolyser")} value={fmt(s.electrolyser_w)} unit="W" />
                <Readout label={t("sim.stored")} value={fmt(s.h2_tank_kg, 2)} unit="kg" />
                <Readout label={t("sim.pressure")} value={fmt(s.tank_bar, 1)} unit="bar" />
                <Readout label={t("sim.inside")} value={fmt(s.indoor_c, 1)} unit="°C" />
                <Readout label={t("dg.battery")} value={fmt(s.battery_soc_pct)} unit="%" />
              </div>
            </div>
          ) : (
            <div className="stack">
              <p className="dim" style={{ margin: 0 }}>{t("ov.sim.none")}</p>
              <div className="scenario-chips">
                {(catalog.data?.scenarios ?? [])
                  .filter((sc) => FEATURED.includes(sc.id))
                  .map((sc) => (
                    <button key={sc.id} className={`chip ${sc.group}`} disabled={busy} onClick={() => void startScenario(sc.id)}>
                      {sc.title[lang]}
                    </button>
                  ))}
              </div>
              {error && <div className="error">{error}</div>}
            </div>
          )}
        </Card>

        <Card title={<span className="row"><ShieldCheckIcon size={18} /> {t("ov.safety")}</span>}
          actions={<Link to="/safety" className="btn small">{t("ov.open")} <ArrowRightIcon size={13} /></Link>}>
          {snap?.trust ? (
            <div className="stack">
              <div className="row">
                <LevelPill level={snap.trust.overall} />
                <span className="dim small-text">{snap.trust.h2_why}</span>
              </div>
              <h3>{t("ov.safety.journal")}</h3>
              <ul className="mini-journal">
                {(journal.data?.entries ?? []).map((e) => (
                  <li key={e.id} className={`sev-${e.severity}`}>
                    <span>{e.summary}</span>
                    <span className="faint num">{formatDate(e.ts, lang)}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="dim">{t("common.loading")}</p>
          )}
        </Card>

        <Card title={<span className="row"><ChartLineUpIcon size={18} /> {t("ov.planner")}</span>}
          actions={<Link to="/planner" className="btn small">{t("ov.open")} <ArrowRightIcon size={13} /></Link>}>
          <div className="stack">
            <p className="dim" style={{ margin: 0 }}>{t("ov.planner.body")}</p>
            <Link to="/planner" className="btn primary" style={{ justifySelf: "start" }}>
              {t("ov.planner.open")} <ArrowRightIcon size={14} />
            </Link>
          </div>
        </Card>
      </div>
    </div>
  );
}
