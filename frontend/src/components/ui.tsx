// Small building blocks shared by every page.
import { useState, type ReactNode } from "react";
import { CheckCircleIcon, WarningIcon, XCircleIcon } from "@phosphor-icons/react";
import type { Level, Telemetry } from "../lib/types";
import { useI18n, type Key } from "../lib/i18n";
import { ApiError, post } from "../lib/api";

const LEVEL_TEXT: Record<Level, { en: string; fr: string }> = {
  ok: { en: "Trusted", fr: "Fiable" },
  degraded: { en: "Degraded", fr: "Dégradé" },
  untrusted: { en: "Not trusted", fr: "Non fiable" },
};

// What the stack is doing, in words: "Producing" only when the electrolyser
// is actually on. In its production phase without a permit, it is on standby.
export function stackStatusKey(tel: Telemetry): Key {
  if (tel.electrolyser) return "phase.ELECTROLYSIS";
  return tel.phase === "ELECTROLYSIS" ? "phase.standby" : `phase.${tel.phase}`;
}

export function LevelPill({ level }: { level: Level }) {
  const { lang } = useI18n();
  const Icon = level === "ok" ? CheckCircleIcon : level === "degraded" ? WarningIcon : XCircleIcon;
  return (
    <span className={`pill ${level}`}>
      <Icon size={14} weight="fill" aria-hidden />
      {LEVEL_TEXT[level][lang]}
    </span>
  );
}

export function Card({
  title,
  actions,
  children,
  className = "",
  id,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  id?: string;
}) {
  return (
    <section className={`card ${className}`} id={id}>
      {(title || actions) && (
        <div className="card-head">
          {title && <h2>{title}</h2>}
          {actions && <div className="row">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

// One line of a faceplate: what, how much, in which unit.
export function Readout({ label, value, unit, tone }: { label: ReactNode; value: ReactNode; unit?: string; tone?: "warn" | "danger" | "ok" }) {
  return (
    <div className={`readout ${tone ?? ""}`}>
      <span className="readout-label">{label}</span>
      <span className="readout-value num">
        {value}
        {unit && <small>{unit}</small>}
      </span>
    </div>
  );
}

// A horizontal scale with its safe band and limits drawn on it: the reading is
// always seen against what it must stay within, never on its own.
export function Gauge({
  value,
  min,
  max,
  band,
  marks = [],
  tone,
  label,
}: {
  value: number | null;
  min: number;
  max: number;
  band?: [number, number];
  marks?: { at: number; label: string; tone?: "warn" | "danger" }[];
  tone?: "warn" | "danger" | "ok" | "accent";
  label: string;
}) {
  const pos = (v: number) => `${Math.max(0, Math.min(100, ((v - min) / (max - min)) * 100))}%`;
  return (
    <div className="gauge" role="meter" aria-label={label} aria-valuemin={min} aria-valuemax={max} aria-valuenow={value ?? undefined}>
      <div className="gauge-track">
        {band && <span className="gauge-band" style={{ left: pos(band[0]), width: `calc(${pos(band[1])} - ${pos(band[0])})` }} />}
        {marks.map((m) => (
          <span key={m.label} className={`gauge-mark ${m.tone ?? ""}`} style={{ left: pos(m.at) }} title={m.label} />
        ))}
        {value !== null && Number.isFinite(value) && <span className={`gauge-needle ${tone ?? ""}`} style={{ left: pos(value) }} />}
      </div>
    </div>
  );
}

export function formatDate(iso: string | null | undefined, lang: string, withTime = true): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(lang === "fr" ? "fr-FR" : "en-GB", {
    dateStyle: "medium",
    ...(withTime ? { timeStyle: "short" } : {}),
  });
}

// "2025-07-10 13:05" (the site's own clock) → "10 Jul, 13:05" in the reader's language.
export function formatLocal(local: string | undefined, lang: string): { date: string; time: string } {
  if (!local) return { date: "—", time: "—" };
  const [d = "", time = ""] = local.split(" ");
  const [y, m, day] = d.split("-").map(Number);
  const date = new Date(Date.UTC(y ?? 2025, (m ?? 1) - 1, day ?? 1)).toLocaleDateString(lang === "fr" ? "fr-FR" : "en-GB", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
  return { date, time };
}

// Send a POST and keep its error and busy state, for buttons and forms.
export function useAction() {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async <T,>(path: string, body: unknown): Promise<T | null> => {
    setBusy(true);
    setError("");
    try {
      return await post<T>(path, body);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      return null;
    } finally {
      setBusy(false);
    }
  };
  return { run, error, busy, setError };
}
