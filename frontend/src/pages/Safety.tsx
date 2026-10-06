// The trust layer, made visible. Three questions an owner, an inspector or an
// insurer will ask, answered on one page:
//   1. Can the hydrogen detector be trusted right now?   (sensor integrity)
//   2. Has it been checked when it should have been?       (maintenance)
//   3. Can we prove what happened, and that nobody edited it? (journal)
import { useState } from "react";
import { Link } from "react-router";
import {
  CheckCircleIcon,
  FileTextIcon,
  InfoIcon,
  SealCheckIcon,
  ShieldCheckIcon,
  ShieldWarningIcon,
  SirenIcon,
  WarningIcon,
} from "@phosphor-icons/react";
import { useI18n, type Key } from "../lib/i18n";
import { useLive } from "../lib/live";
import { useSession } from "../lib/session";
import { useApi } from "../lib/useApi";
import { api, ApiError, post } from "../lib/api";
import type { CheckStatus, JournalEntry, SensorHealth, TrustPage, Verification } from "../lib/types";
import { Card, LevelPill, formatDate } from "../components/ui";

export function SafetyPage() {
  const { t } = useI18n();
  const { snapshot } = useLive();
  // Re-fetch whenever the journal moves: every trust change is journalled.
  const head = snapshot?.journal_head;
  const trustPage = useApi<TrustPage>("/api/trust", head);
  const page = trustPage.data;
  const integrity = snapshot?.trust ?? page?.integrity ?? null;

  return (
    <div className="stack">
      <header className="topbar">
        <div className="title">
          <h1>{t("safety.title")}</h1>
          <span className="dim">{t("safety.lead")}</span>
        </div>
        <Link to="/report" className="btn">
          <FileTextIcon size={16} /> {t("safety.report")}
        </Link>
      </header>

      <Headline trusted={integrity?.h2_trusted ?? false} why={integrity?.h2_why ?? "—"} standing={page?.h2_standing} />

      <Card title={t("safety.sensors")}>
        {integrity ? (
          <div className="grid grid-4">
            {(["h2", "temperature", "electrolyte", "link"] as const).map((name) => (
              <SensorCard key={name} name={name} health={integrity.sensors[name]} />
            ))}
          </div>
        ) : (
          <p className="dim">{t("safety.no_data")}</p>
        )}
      </Card>

      <div className="grid grid-2">
        <Maintenance checks={page?.maintenance ?? []} plan={page?.plan} onRecorded={trustPage.reload} />
        <Journal head={head} />
      </div>
    </div>
  );
}

function Headline({ trusted, why, standing }: { trusted: boolean; why: string; standing?: { level: string; why: string } }) {
  const { t } = useI18n();
  const Icon = trusted ? ShieldCheckIcon : ShieldWarningIcon;
  return (
    <section className={`card headline ${trusted ? "ok" : "danger"}`} aria-live="polite">
      <Icon size={44} weight="duotone" aria-hidden />
      <div className="stack" style={{ gap: 4 }}>
        <strong style={{ fontSize: "1.15rem" }}>{trusted ? t("safety.production.ok") : t("safety.production.blocked")}</strong>
        <span className="dim">{why}</span>
        {/* The maintenance standing, unless it is already the reason above. */}
        {standing && !why.toLowerCase().includes(standing.why.toLowerCase()) && (
          <span className="dim">
            {t("safety.standing")} · {standing.why}
          </span>
        )}
      </div>
    </section>
  );
}

const SENSOR_NAME: Record<string, Key> = {
  h2: "safety.sensor.h2",
  temperature: "safety.sensor.temperature",
  electrolyte: "safety.sensor.electrolyte",
  link: "safety.sensor.link",
};

function SensorCard({ name, health }: { name: string; health: SensorHealth }) {
  const { t, fmt } = useI18n();
  const color = health.level === "ok" ? "var(--ok)" : health.level === "degraded" ? "var(--warn)" : "var(--danger)";
  const unit = name === "h2" ? " ppm" : name === "temperature" ? " °C" : name === "electrolyte" ? " wt%" : "";
  return (
    <div className="card sensor">
      <strong>{t(SENSOR_NAME[name] ?? "safety.sensors")}</strong>
      <span>
        <LevelPill level={health.level} />
      </span>
      {name === "link" ? (
        <span className="num dim">
          {fmt(health.received)} {t("safety.link.received")} · {fmt(health.gaps)} {t("safety.link.gaps")} · {fmt(health.reboots)}{" "}
          {t("safety.link.reboots")}
        </span>
      ) : (
        <span className="num">
          {health.value === null ? "—" : `${fmt(health.value, name === "electrolyte" ? 2 : 1)}${unit}`}
          {name === "h2" && health.baseline_ppm !== undefined && (
            <span className="dim">
              {" "}
              · {t("safety.baseline")} {fmt(health.baseline_ppm)} ppm
            </span>
          )}
        </span>
      )}
      <div className="bar" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={health.score} aria-label={t("safety.score")}>
        <span style={{ width: `${health.score}%`, background: color }} />
      </div>
      <span className="faint">
        {t("safety.score")} {fmt(health.score)} %
      </span>
      {health.findings.length === 0 ? (
        <span className="hint row" style={{ gap: 6 }}>
          <CheckCircleIcon size={14} color="var(--ok)" /> {t("safety.no_findings")}
        </span>
      ) : (
        <ul className="findings">
          {health.findings.map((f) => (
            <li key={f.code} className={f.level}>
              {f.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ── Maintenance ──────────────────────────────────────────────────────────────

type CheckKind = "bump_test" | "calibration" | "inspection" | "sensor_replaced";

// Which sensors each kind of check applies to, and what has to be measured.
// Mirrors REQUIRED_DATA in backend/src/hestia/api/routes.py.
const SENSORS_FOR: Record<CheckKind, string[]> = {
  bump_test: ["h2"],
  calibration: ["h2", "electrolyte"],
  inspection: ["installation"],
  sensor_replaced: ["h2", "temperature", "electrolyte"],
};

const FIELDS: Record<string, { name: string; label: Key; placeholder: string }[]> = {
  "bump_test:h2": [
    { name: "gas_ppm", label: "safety.field.gas_ppm", placeholder: "1000" },
    { name: "peak_reading_ppm", label: "safety.field.peak_reading_ppm", placeholder: "940" },
  ],
  "calibration:h2": [
    { name: "span_gas_ppm", label: "safety.field.span_gas_ppm", placeholder: "1000" },
    { name: "span_reading_ppm", label: "safety.field.span_reading_ppm", placeholder: "985" },
    { name: "clean_air_ppm", label: "safety.field.clean_air_ppm", placeholder: "55" },
  ],
  "calibration:electrolyte": [
    { name: "std_low_pct", label: "safety.field.std_low_pct", placeholder: "20" },
    { name: "reading_low_pct", label: "safety.field.reading_low_pct", placeholder: "20.3" },
    { name: "std_high_pct", label: "safety.field.std_high_pct", placeholder: "30" },
    { name: "reading_high_pct", label: "safety.field.reading_high_pct", placeholder: "29.8" },
  ],
};

const STATE_CLASS: Record<CheckStatus["state"], string> = {
  ok: "ok",
  due_soon: "warn",
  overdue: "danger",
  never: "danger",
  failed: "danger",
};

function Maintenance({
  checks,
  plan,
  onRecorded,
}: {
  checks: CheckStatus[];
  plan?: TrustPage["plan"];
  onRecorded: () => void;
}) {
  const { t, lang } = useI18n();
  const { can } = useSession();
  const { snapshot } = useLive();
  const [kind, setKind] = useState<CheckKind>("bump_test");
  const [sensor, setSensor] = useState("h2");
  const [values, setValues] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState("");
  const [inspectionPassed, setInspectionPassed] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const fields = FIELDS[`${kind}:${sensor}`] ?? [];

  const choose = (next: CheckKind) => {
    setKind(next);
    setSensor(SENSORS_FOR[next][0] ?? "h2");
    setValues({});
    setMessage(null);
  };

  const simulateBump = async () => {
    const gas = Number(values.gas_ppm || 1000);
    try {
      const r = await post<{ gas_ppm: number; peak_reading_ppm: number }>("/api/twin/bump-test", { gas_ppm: gas });
      setValues({ gas_ppm: String(r.gas_ppm), peak_reading_ppm: String(Math.round(r.peak_reading_ppm)) });
    } catch (e) {
      setMessage({ ok: false, text: e instanceof ApiError ? e.message : String(e) });
    }
  };

  const submit = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const data: Record<string, number> = Object.fromEntries(fields.map((f) => [f.name, Number(values[f.name])]));
      // An inspection has no measurement: the technician states the outcome.
      if (kind === "inspection") data.passed = inspectionPassed ? 1 : 0;
      const r = await post<{ result: string; verdict: string }>("/api/maintenance", { kind, sensor, data, notes });
      setMessage({ ok: r.result === "pass", text: `${r.result.toUpperCase()}: ${r.verdict}` });
      setValues({});
      setNotes("");
      onRecorded();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  const complete = fields.every((f) => values[f.name] !== undefined && values[f.name] !== "" && Number.isFinite(Number(values[f.name])));

  return (
    <Card title={t("safety.maintenance")}>
      <div className="stack">
        <table>
          <thead>
            <tr>
              <th>{t("safety.check")}</th>
              <th>{t("safety.state")}</th>
              <th>{t("safety.last")}</th>
              <th>{t("safety.next_due")}</th>
            </tr>
          </thead>
          <tbody>
            {checks.map((c) => (
              <tr key={`${c.kind}:${c.sensor}`}>
                <td>
                  {t(`safety.kind.${c.kind}`)}
                  <div className="faint">{t(SENSOR_NAME[c.sensor] ?? "safety.sensor.installation")}</div>
                </td>
                <td>
                  <span className={`pill ${STATE_CLASS[c.state]}`}>{t(`safety.state.${c.state}`)}</span>
                  {c.days_overdue > 0 && <div className="faint num">+{c.days_overdue} d</div>}
                </td>
                <td className="num">{formatDate(c.last_at, lang, false)}</td>
                <td className="num">{formatDate(c.next_due, lang, false)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {plan && (
          <p className="hint" style={{ margin: 0 }}>
            {t("safety.plan")
              .replace("{bump}", String(plan.bump_test_days))
              .replace("{cal}", String(plan.calibration_days))
              .replace("{insp}", String(plan.inspection_days))
              .replace("{grace}", String(plan.grace_days))}
          </p>
        )}

        {can("operator") && (
          <div className="stack form-block">
            <h3>{t("safety.record")}</h3>
            <div className="segmented" role="group" aria-label={t("safety.check")}>
              {(Object.keys(SENSORS_FOR) as CheckKind[]).map((k) => (
                <button key={k} aria-pressed={kind === k} onClick={() => choose(k)}>
                  {t(`safety.kind.${k}`)}
                </button>
              ))}
            </div>
            <p className="hint" style={{ margin: 0 }}>
              {t(`safety.help.${kind}`)}
            </p>
            {SENSORS_FOR[kind].length > 1 && (
              <div className="field">
                <label htmlFor="m-sensor">{t("safety.form.sensor")}</label>
                <select id="m-sensor" className="input" value={sensor} onChange={(e) => {
                  setSensor(e.target.value);
                  setValues({});
                }}>
                  {SENSORS_FOR[kind].map((s) => (
                    <option key={s} value={s}>
                      {t(SENSOR_NAME[s] ?? "safety.sensor.installation")}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {fields.length > 0 && (
              <div className="grid grid-2" style={{ gap: 10 }}>
                {fields.map((f) => (
                  <div className="field" key={f.name}>
                    <label htmlFor={`m-${f.name}`}>{t(f.label)}</label>
                    <input id={`m-${f.name}`} className="input num" type="number" min={0} step="any" inputMode="decimal"
                      placeholder={f.placeholder} value={values[f.name] ?? ""}
                      onChange={(e) => setValues({ ...values, [f.name]: e.target.value })} />
                  </div>
                ))}
              </div>
            )}
            {kind === "inspection" && (
              <div className="segmented" role="group" aria-label={t("safety.inspection.outcome")}>
                <button aria-pressed={inspectionPassed} onClick={() => setInspectionPassed(true)}>
                  {t("safety.inspection.pass")}
                </button>
                <button aria-pressed={!inspectionPassed} onClick={() => setInspectionPassed(false)}>
                  {t("safety.inspection.fail")}
                </button>
              </div>
            )}
            {kind === "bump_test" && snapshot?.mode === "twin" && (
              <button className="btn small" style={{ alignSelf: "start" }} onClick={() => void simulateBump()}>
                {t("safety.simulate_bump")}
              </button>
            )}
            <div className="field">
              <label htmlFor="m-notes">{t("safety.form.notes")}</label>
              <textarea id="m-notes" className="input" rows={2} maxLength={1000} value={notes} onChange={(e) => setNotes(e.target.value)} />
            </div>
            <div className="row">
              <button className="btn primary" disabled={busy || !complete} onClick={() => void submit()}>
                {t("safety.form.submit")}
              </button>
              {message && (
                <span className={`pill ${message.ok ? "ok" : "danger"}`} role="status">
                  {message.text}
                </span>
              )}
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

// ── Journal ──────────────────────────────────────────────────────────────────

const SEVERITY_ICON = { info: InfoIcon, warning: WarningIcon, critical: SirenIcon } as const;
const ALERT_KINDS = [
  "h2_warning",
  "pressure_relief",
  "alert_failed",
  "alarm_latched",
  "alarm_reset",
  "over_temperature",
  "sensor_trust_changed",
  "permit_withdrawn_for_safety",
  "device_offline",
  "command_rejected",
  "telemetry_rejected",
  "model_integrity",
  "login_failed",
];
const MAINTENANCE_KINDS = ["bump_test", "calibration", "inspection", "sensor_replaced"];

function Journal({ head }: { head?: string }) {
  const { t, lang, fmt } = useI18n();
  const [filter, setFilter] = useState<"all" | "alerts" | "maintenance">("all");
  const [older, setOlder] = useState<JournalEntry[]>([]);
  const [verification, setVerification] = useState<Verification | null>(null);
  const [verifying, setVerifying] = useState(false);
  const kinds = filter === "alerts" ? ALERT_KINDS : filter === "maintenance" ? MAINTENANCE_KINDS : [];
  const query = `/api/journal?limit=40${kinds.map((k) => `&kind=${k}`).join("")}`;
  const latest = useApi<{ entries: JournalEntry[] }>(query, head);
  const entries = [...(latest.data?.entries ?? []), ...older];

  const loadOlder = async () => {
    const last = entries[entries.length - 1];
    if (!last) return;
    const r = await api<{ entries: JournalEntry[] }>(`${query}&before_id=${last.id}`);
    setOlder([...older, ...r.entries]);
  };

  const verify = async () => {
    setVerifying(true);
    try {
      setVerification(await post<Verification>("/api/journal/verify", {}));
    } finally {
      setVerifying(false);
    }
  };

  return (
    <Card
      title={t("safety.journal")}
      actions={
        <button className="btn small" onClick={() => void verify()} disabled={verifying}>
          <SealCheckIcon size={15} /> {t("safety.verify")}
        </button>
      }
    >
      <div className="stack">
        <p className="hint" style={{ margin: 0 }}>
          {t("safety.journal.lead")}
        </p>
        {verification && (
          <div className={`pill ${verification.ok ? "ok" : "danger"}`} role="status" style={{ whiteSpace: "normal" }}>
            {verification.ok ? t("safety.verified") : t("safety.tampered")} · {fmt(verification.checked)} {t("safety.verify.checked")}
            {!verification.ok && ` · #${verification.first_broken_id}: ${verification.problem}`}
          </div>
        )}
        <div className="segmented" role="group" aria-label="Filter">
          {(["all", "alerts", "maintenance"] as const).map((f) => (
            <button key={f} aria-pressed={filter === f} onClick={() => {
              setFilter(f);
              setOlder([]);
            }}>
              {t(`safety.journal.${f}`)}
            </button>
          ))}
        </div>
        <div className="timeline" style={{ maxHeight: 560, overflowY: "auto" }}>
          {entries.map((e) => {
            const Icon = SEVERITY_ICON[e.severity];
            return (
              <div className="entry" key={e.id}>
                <Icon size={18} className={`sev-${e.severity}`} weight={e.severity === "info" ? "regular" : "fill"} aria-label={e.severity} />
                <div>
                  <div>{e.summary}</div>
                  <div className="faint">
                    {e.actor} · <span className="hash">#{e.id} {e.hash.slice(0, 12)}…</span>
                  </div>
                </div>
                <span className="faint num" style={{ whiteSpace: "nowrap" }}>
                  {formatDate(e.ts, lang)}
                </span>
              </div>
            );
          })}
          {entries.length === 0 && <p className="dim">{t("safety.journal.empty")}</p>}
        </div>
        {entries.length >= 40 && (
          <button className="btn small ghost" onClick={() => void loadOlder()}>
            {t("safety.journal.more")}
          </button>
        )}
        <span className="hash">
          {t("safety.journal.head")}: {verification?.head_hash ?? head ?? "—"}
        </span>
      </div>
    </Card>
  );
}

