// The home page: what Hestia is, for whom, and why it can be trusted, before
// anyone signs in. Public (/) when signed out, and at /about once signed in.
// The hero picture is the real site diagram, drawn from a fixed summer-noon
// state, so what visitors see here is exactly what the simulator shows.
import { useEffect, useState } from "react";
import { Link } from "react-router";
import {
  ArrowRightIcon,
  BroadcastIcon,
  ChartLineUpIcon,
  CpuIcon,
  CubeIcon,
  FlaskIcon,
  LockKeyIcon,
  MoonIcon,
  ShieldCheckIcon,
  SirenIcon,
  SunIcon,
  TreeStructureIcon,
  WindIcon,
} from "@phosphor-icons/react";
import { useI18n, type Key } from "../lib/i18n";
import type { Snapshot } from "../lib/types";
import { Logo } from "../components/Logo";
import { SiteDiagram } from "../components/SiteDiagram";
import { LangSwitch, useTheme } from "../components/Layout";

// A summer noon in Tunis, as the simulator draws it (values from a real run).
const SCENE = {
  site: {
    source: "simulation",
    timestamp: "2025-07-10T11:00:00Z",
    local_time: "2025-07-10 12:00",
    outdoor_c: 33.4,
    humidity_pct: 38,
    irradiance_wpm2: 905,
    pv_w: 4120,
    electrolyser_w: 1840,
    available_w: 1880,
    load_w: 540,
    heat_pump_w: 690,
    fuel_cell_w: 0,
    battery_w: 0,
    battery_soc_pct: 100,
    grid_w: -1050,
    curtailed_w: 0,
    h2_tank_kg: 2.31,
    h2_tank_pct: 58,
    tank_bar: 17.9,
    tank_mawp_bar: 30,
    h2_vented_kg: 0,
    indoor_c: 25.8,
    hvac_mode: "cool",
    hvac_w: -2070,
    fuel_cell_heat_w: 0,
    boiler_w: 0,
    end_use: "fuel_cell",
    room_h2_ppm: 0,
  },
  telemetry: { electrolyte_c: 53.2, temp_valid: true, h2_ppm: 57, h2_valid: true, h2_warning: false, h2_alarm_latched: false, ventilation: false },
  twin: { faults: [] },
} as unknown as Snapshot;

function useDemoUsers(): boolean {
  const [demo, setDemo] = useState(false);
  useEffect(() => {
    fetch("/api/health")
      .then((r) => r.json() as Promise<{ demo_users?: boolean }>)
      .then((h) => setDemo(Boolean(h.demo_users)))
      .catch(() => setDemo(false));
  }, []);
  return demo;
}

const WINGS = [
  { icon: FlaskIcon, title: "home.wing.live", body: "home.wing.live.body", to: "/live" },
  { icon: CubeIcon, title: "home.wing.sim", body: "home.wing.sim.body", to: "/simulator" },
  { icon: ChartLineUpIcon, title: "home.wing.plan", body: "home.wing.plan.body", to: "/planner" },
] as const;

const SAFETY = [
  { icon: SirenIcon, title: "home.safety.gas", body: "home.safety.gas.body" },
  { icon: CpuIcon, title: "home.safety.local", body: "home.safety.local.body" },
  { icon: LockKeyIcon, title: "home.safety.journal", body: "home.safety.journal.body" },
  { icon: ShieldCheckIcon, title: "home.safety.trust", body: "home.safety.trust.body" },
] as const;

const PHYSICS: Key[] = ["home.physics.pv", "home.physics.stack", "home.physics.koh", "home.physics.tank"];

const BUILT = [
  { icon: CpuIcon, title: "home.built.controller", body: "home.built.controller.body" },
  { icon: BroadcastIcon, title: "home.built.gateway", body: "home.built.gateway.body" },
  { icon: TreeStructureIcon, title: "home.built.dashboard", body: "home.built.dashboard.body" },
] as const;

export function LandingPage({ embedded = false }: { embedded?: boolean }) {
  const { t } = useI18n();
  const demo = useDemoUsers();
  const [theme, toggleTheme] = useTheme();
  const start = embedded ? "/" : demo ? "/login?demo=visitor" : "/login";

  return (
    <div className={`landing ${embedded ? "embedded" : ""}`}>
      {!embedded && (
        <header className="landing-bar">
          <Link to="/" className="brand">
            <Logo size={30} />
            <span>Hestia</span>
          </Link>
          <nav className="row">
            <a href="#how" className="landing-link">{t("home.cta.how")}</a>
            <LangSwitch />
            <button className="btn ghost small" onClick={toggleTheme} aria-label={t("nav.theme")} title={t("nav.theme")}>
              {theme === "dark" ? <SunIcon size={17} /> : <MoonIcon size={17} />}
            </button>
            <Link to="/login" className="btn small">{t("nav.signin")}</Link>
          </nav>
        </header>
      )}

      <section className="hero">
        <div className="hero-text">
          <span className="kicker">{t("home.kicker")}</span>
          <h1>{t("home.title")}</h1>
          <p className="lead">{t("home.lead")}</p>
          <div className="row">
            <Link to={start} className="btn primary large">
              {embedded ? t("home.cta.dashboard") : t("home.cta.demo")} <ArrowRightIcon size={16} weight="bold" />
            </Link>
            <a href="#how" className="btn large">{t("home.cta.how")}</a>
          </div>
          {demo && !embedded && <p className="hint">{t("home.demo")}</p>}
        </div>
        <div className="hero-figure card" aria-hidden>
          <SiteDiagram snap={SCENE} />
        </div>
      </section>

      <section className="landing-section" id="how">
        <h2>{t("home.wings")}</h2>
        <div className="grid grid-3">
          {WINGS.map((w) => (
            <Link key={w.title} to={embedded ? w.to : start} className="card wing">
              <w.icon size={28} weight="duotone" className="wing-icon" />
              <h3>{t(w.title)}</h3>
              <p>{t(w.body)}</p>
              <span className="wing-go">
                {t("common.open")} <ArrowRightIcon size={14} />
              </span>
            </Link>
          ))}
        </div>
      </section>

      <section className="landing-section">
        <h2>{t("home.safety")}</h2>
        <div className="grid grid-4">
          {SAFETY.map((s) => (
            <div key={s.title} className="card feature">
              <s.icon size={24} weight="duotone" className="feature-icon" />
              <h3>{t(s.title)}</h3>
              <p>{t(s.body)}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="landing-section two-col">
        <div>
          <h2>{t("home.physics")}</h2>
          <ul className="checklist">
            {PHYSICS.map((k) => (
              <li key={k}>
                <WindIcon size={16} className="accent-text" aria-hidden /> {t(k)}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h2>{t("home.built")}</h2>
          <div className="stack" style={{ gap: 10 }}>
            {BUILT.map((b) => (
              <div key={b.title} className="built-row">
                <b.icon size={22} weight="duotone" className="feature-icon" />
                <div>
                  <strong>{t(b.title)}</strong>
                  <p className="dim">{t(b.body)}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="landing-section story card">
        <h2>{t("home.story")}</h2>
        <p>{t("home.story.body")}</p>
      </section>

      <footer className="landing-foot faint">
        <Logo size={18} /> Hestia · {t("home.footer")}
      </footer>
    </div>
  );
}
