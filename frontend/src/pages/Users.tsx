// Accounts (admin only). Three roles:
//   viewer   – sees everything, changes nothing (an owner, an inspector);
//   operator – runs the installation: modes, thresholds, alarm resets, checks;
//   admin    – operator + manages accounts.
// Disabling an account or changing its password signs it out everywhere
// (the gateway bumps the account's session version). Every change is journalled.
import { useState } from "react";
import { EnvelopeSimpleIcon, UserPlusIcon } from "@phosphor-icons/react";
import { useI18n } from "../lib/i18n";
import { useSession } from "../lib/session";
import { useApi } from "../lib/useApi";
import { api, ApiError, post } from "../lib/api";
import type { AlertStatus, Role, User } from "../lib/types";
import { Card, formatDate } from "../components/ui";

const ROLES: Role[] = ["viewer", "operator", "admin"];

export function UsersPage() {
  const { t } = useI18n();
  const { me } = useSession();
  const { data: users, error, reload } = useApi<User[]>("/api/users");
  const [message, setMessage] = useState("");

  const patch = async (id: number, body: Partial<{ role: Role; disabled: boolean; password: string }>) => {
    setMessage("");
    try {
      await api(`/api/users/${id}`, { method: "PATCH", body: JSON.stringify(body) });
      reload();
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : String(e));
    }
  };

  const resetPassword = (user: User) => {
    const password = window.prompt(t("users.new_password").replace("{name}", user.username));
    if (password) void patch(user.id, { password });
  };

  return (
    <div className="stack">
      <header className="topbar">
        <div className="title">
          <h1>{t("users.title")}</h1>
          <span className="dim">{t("users.lead")}</span>
        </div>
      </header>
      <div className="grid grid-2" style={{ alignItems: "start" }}>
        <Card title={t("users.accounts")}>
          {error && <div className="error">{error}</div>}
          <table>
            <thead>
              <tr>
                <th>{t("login.username")}</th>
                <th>{t("users.role")}</th>
                <th>{t("users.status")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(users ?? []).map((u) => {
                const self = u.id === me?.user.id;
                return (
                  <tr key={u.id} className={u.disabled ? "dim" : ""}>
                    <td>
                      <strong>{u.username}</strong>
                      <div className="faint">{u.display_name}</div>
                    </td>
                    <td>
                      <select className="input" value={u.role} disabled={self} aria-label={t("users.role")}
                        onChange={(e) => void patch(u.id, { role: e.target.value as Role })}>
                        {ROLES.map((r) => (
                          <option key={r} value={r}>
                            {t(`users.role.${r}`)}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <span className={`pill ${u.disabled ? "danger" : "ok"}`}>{u.disabled ? t("users.disabled") : t("users.active")}</span>
                    </td>
                    <td className="row" style={{ justifyContent: "flex-end" }}>
                      <button className="btn small ghost" onClick={() => resetPassword(u)}>
                        {t("users.reset_password")}
                      </button>
                      {!self && (
                        <button className="btn small" onClick={() => void patch(u.id, { disabled: !u.disabled })}>
                          {u.disabled ? t("users.enable") : t("users.disable")}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {message && <div className="error">{message}</div>}
          <p className="hint">{t("users.roles.hint")}</p>
        </Card>
        <div className="stack">
          <NewUser onCreated={reload} />
          <EmailAlerts />
        </div>
      </div>
    </div>
  );
}

// E-mail alerts are configured in the gateway's .env file, on purpose: the
// app password is never typed into a web page nor stored in the database.
// This card shows whether they are on and lets an administrator check that
// they really arrive.
function EmailAlerts() {
  const { t, lang } = useI18n();
  const { data: status, reload } = useApi<AlertStatus>("/api/alerts");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  const sendTest = async () => {
    setBusy(true);
    setResult(null);
    try {
      await post("/api/alerts/test", {});
      setResult({ ok: true, text: t("alerts.test.sent") });
    } catch (e) {
      setResult({ ok: false, text: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setBusy(false);
      reload();
    }
  };

  return (
    <Card
      title={t("alerts.title")}
      actions={status && <span className={`pill ${status.enabled ? "ok" : ""}`}>{status.enabled ? t("alerts.on") : t("alerts.off")}</span>}
    >
      <div className="stack" style={{ gap: 12 }}>
        <p className="hint" style={{ margin: 0 }}>
          {t("alerts.lead")}
        </p>
        {status?.enabled ? (
          <>
            <dl className="assumptions">
              <dt>{t("alerts.sender")}</dt>
              <dd>
                {status.sender} {status.provider === "gmail" && <span className="faint">(Gmail)</span>}
              </dd>
              <dt>{t("alerts.recipients")}</dt>
              <dd>{status.recipients.join(", ")}</dd>
              <dt>{t("alerts.cooldown")}</dt>
              <dd className="num">{status.cooldown_minutes} min</dd>
              <dt>{t("alerts.last_sent")}</dt>
              <dd>{formatDate(status.last_sent_at, lang)}</dd>
            </dl>
            {status.last_error && (
              <div className="error" role="alert">
                {status.last_error}
              </div>
            )}
            <div className="row">
              <button className="btn" disabled={busy} onClick={() => void sendTest()}>
                <EnvelopeSimpleIcon size={16} /> {t("alerts.test")}
              </button>
              {result && (
                <span className={`pill ${result.ok ? "ok" : "danger"}`} role="status" style={{ whiteSpace: "normal" }}>
                  {result.text}
                </span>
              )}
            </div>
          </>
        ) : (
          <>
            <p style={{ margin: 0 }}>{t("alerts.howto")}</p>
            <pre className="code">{"HESTIA_EMAIL_ALERTS=true\nHESTIA_GMAIL_ADDRESS=alerts.for.my.building@gmail.com\nHESTIA_GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx\nHESTIA_ALERT_RECIPIENTS=[\"you@example.com\"]"}</pre>
            <p className="hint" style={{ margin: 0 }}>
              {t("alerts.howto.password")}
            </p>
          </>
        )}
      </div>
    </Card>
  );
}

function NewUser({ onCreated }: { onCreated: () => void }) {
  const { t } = useI18n();
  const [form, setForm] = useState({ username: "", display_name: "", role: "viewer" as Role, password: "" });
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  const submit = async () => {
    setError("");
    setDone("");
    try {
      await post("/api/users", form);
      setDone(form.username);
      setForm({ username: "", display_name: "", role: "viewer", password: "" });
      onCreated();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  };

  return (
    <Card title={t("users.create")}>
      <form className="stack" onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}>
        <div className="field">
          <label htmlFor="nu-name">{t("login.username")}</label>
          <input id="nu-name" className="input" autoComplete="off" value={form.username} minLength={2} maxLength={64} required
            onChange={(e) => setForm({ ...form, username: e.target.value })} />
        </div>
        <div className="field">
          <label htmlFor="nu-display">{t("users.display_name")}</label>
          <input id="nu-display" className="input" value={form.display_name} maxLength={100}
            onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
        </div>
        <div className="field">
          <label htmlFor="nu-role">{t("users.role")}</label>
          <select id="nu-role" className="input" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {t(`users.role.${r}`)}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="nu-pass">{t("login.password")}</label>
          <input id="nu-pass" className="input" type="password" autoComplete="new-password" minLength={12} required value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })} />
          <span className="hint">{t("users.password.hint")}</span>
        </div>
        {error && <div className="error">{error}</div>}
        {done && <span className="pill ok">{t("users.created").replace("{name}", done)}</span>}
        <button className="btn primary" type="submit" disabled={form.username.length < 2 || form.password.length < 12}>
          <UserPlusIcon size={16} /> {t("users.create")}
        </button>
      </form>
    </Card>
  );
}
