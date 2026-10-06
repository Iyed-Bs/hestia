// Sign-in. The gateway answers with an HttpOnly session cookie; failed
// attempts are throttled per account and per address, and journalled.
// The demo hint only shows when the gateway runs with demo accounts
// (HESTIA_DEMO_USERS=true), which production refuses; /login?demo=visitor
// (the home page's "Try the demo") fills in the read-only visitor account.
import { useEffect, useState, type SyntheticEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { ArrowLeftIcon } from "@phosphor-icons/react";
import { useI18n } from "../lib/i18n";
import { useSession } from "../lib/session";
import { ApiError } from "../lib/api";
import { Logo } from "../components/Logo";
import { LangSwitch } from "../components/Layout";

export function LoginPage() {
  const { t } = useI18n();
  const { signIn } = useSession();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [demo, setDemo] = useState(false);

  useEffect(() => {
    // /api/health says whether demo accounts exist (their passwords are public anyway).
    fetch("/api/health")
      .then((r) => r.json() as Promise<{ demo_users?: boolean }>)
      .then((h) => {
        setDemo(Boolean(h.demo_users));
        if (h.demo_users && params.get("demo") === "visitor") {
          setUsername("visitor");
          setPassword("hestia-visitor");
        }
      })
      .catch(() => setDemo(false));
  }, [params]);

  const submit = async (e: SyntheticEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await signIn(username.trim(), password);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? t("login.failed") : err instanceof Error ? err.message : String(err));
      setPassword("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <form className="card stack" onSubmit={(e) => void submit(e)} aria-labelledby="login-title">
        <div className="row spread">
          <div className="row">
            <Logo size={38} />
            <div>
              <strong style={{ fontSize: "1.3rem" }}>Hestia</strong>
              <div className="faint">{t("app.tagline")}</div>
            </div>
          </div>
          <LangSwitch />
        </div>
        <h1 id="login-title" style={{ fontSize: "1.1rem", margin: "8px 0 0" }}>
          {t("login.title")}
        </h1>
        <div className="field">
          <label htmlFor="username">{t("login.username")}</label>
          <input id="username" className="input" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required autoFocus />
        </div>
        <div className="field">
          <label htmlFor="password">{t("login.password")}</label>
          <input id="password" className="input" type="password" autoComplete="current-password" value={password}
            onChange={(e) => setPassword(e.target.value)} required />
        </div>
        {error && (
          <div className="error" role="alert">
            {error}
          </div>
        )}
        <button className="btn primary" type="submit" disabled={busy || !username || !password}>
          {t("login.submit")}
        </button>
        {demo && (
          <div className="stack" style={{ gap: 6 }}>
            <button type="button" className="btn small" onClick={() => {
              setUsername("visitor");
              setPassword("hestia-visitor");
            }}>
              {t("login.visitor")}
            </button>
            <p className="hint" style={{ margin: 0 }}>{t("login.demo")}</p>
          </div>
        )}
        <Link to="/" className="hint row" style={{ gap: 6 }}>
          <ArrowLeftIcon size={14} /> {t("login.back")}
        </Link>
      </form>
    </div>
  );
}
