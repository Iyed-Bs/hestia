// The signed-in shell: navigation on the left, and above every page the
// installation's annunciator and gas banners, so a problem is visible from
// anywhere. The simulator page shows its own (simulated) ones instead.
import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router";
import {
  ChartLineUpIcon,
  CubeIcon,
  FlaskIcon,
  InfoIcon,
  MoonIcon,
  ShieldCheckIcon,
  SignOutIcon,
  SquaresFourIcon,
  SunIcon,
  UsersIcon,
} from "@phosphor-icons/react";
import { useI18n } from "../lib/i18n";
import { useSession } from "../lib/session";
import { useLive } from "../lib/live";
import { AlarmBanner } from "./Alarm";
import { Annunciator } from "./Annunciator";
import { Logo } from "./Logo";

export function useTheme(): [string, () => void] {
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem("hestia.theme") ?? "dark";
    } catch {
      return "dark";
    }
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("hestia.theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);
  return [theme, () => setTheme((t) => (t === "dark" ? "light" : "dark"))];
}

export function LangSwitch() {
  const { lang, setLang } = useI18n();
  return (
    <div className="segmented" role="group" aria-label="Language">
      {(["en", "fr"] as const).map((l) => (
        <button key={l} type="button" aria-pressed={lang === l} onClick={() => setLang(l)}>
          {l.toUpperCase()}
        </button>
      ))}
    </div>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const { me, signOut, can } = useSession();
  const { snapshot, connected } = useLive();
  const [theme, toggleTheme] = useTheme();
  const { pathname } = useLocation();
  const onSimulator = pathname.startsWith("/simulator");
  const live = connected && snapshot?.online;

  return (
    <div className="shell">
      <nav className="sidebar" aria-label="Main">
        <NavLink to="/" end className="brand">
          <Logo size={30} />
          <div>
            Hestia
            <small>{snapshot?.site_name ?? me?.site_name}</small>
          </div>
        </NavLink>
        <span className="nav-group">{t("nav.group.operate")}</span>
        <NavLink to="/" end className="nav-link">
          <SquaresFourIcon size={19} /> {t("nav.overview")}
        </NavLink>
        <NavLink to="/live" className="nav-link">
          <FlaskIcon size={19} /> {t("nav.live")}
        </NavLink>
        <NavLink to="/simulator" className="nav-link">
          <CubeIcon size={19} /> {t("nav.simulator")}
        </NavLink>
        <span className="nav-group">{t("nav.group.prove")}</span>
        <NavLink to="/planner" className="nav-link">
          <ChartLineUpIcon size={19} /> {t("nav.planner")}
        </NavLink>
        <NavLink to="/safety" className="nav-link">
          <ShieldCheckIcon size={19} /> {t("nav.safety")}
        </NavLink>
        {can("admin") && (
          <NavLink to="/users" className="nav-link">
            <UsersIcon size={19} /> {t("nav.users")}
          </NavLink>
        )}
        <div className="sidebar-foot">
          <div className="row spread">
            <span className={`pill ${snapshot?.mode === "twin" ? "accent" : ""}`}>
              {snapshot?.mode === "device" ? t("mode.device") : t("mode.twin")}
            </span>
            <span className={`pill ${live ? "ok" : "danger"}`}>
              <span className={`dot ${live ? "live" : ""}`} />
              {live ? t("common.live") : t("common.offline")}
            </span>
          </div>
          <div className="row spread">
            <LangSwitch />
            <button className="btn ghost small" onClick={toggleTheme} aria-label={t("nav.theme")} title={t("nav.theme")}>
              {theme === "dark" ? <SunIcon size={17} /> : <MoonIcon size={17} />}
            </button>
          </div>
          <NavLink to="/about" className="nav-link small">
            <InfoIcon size={16} /> {t("nav.about")}
          </NavLink>
          <div className="row spread who">
            <span className="dim">
              {me?.user.display_name || me?.user.username} · {me?.user.role}
            </span>
            <button className="btn ghost small" onClick={() => void signOut()} aria-label={t("nav.signout")} title={t("nav.signout")}>
              <SignOutIcon size={17} />
            </button>
          </div>
          <span className="faint version">v{snapshot?.version ?? me?.version}</span>
        </div>
      </nav>
      <main className="main">
        {!onSimulator && pathname !== "/about" && (
          <div className="no-print">
            <Annunciator snap={snapshot} />
            <AlarmBanner snap={snapshot} commandPath="/api/commands" canReset={can("operator")} />
          </div>
        )}
        {children}
      </main>
    </div>
  );
}
