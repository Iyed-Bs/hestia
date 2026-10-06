"""
Gateway configuration, read from environment variables (or a .env file).

Every variable is documented in .env.example at the repository root; copy it
to .env and fill it in. Names all start with HESTIA_.

Secrets policy:
- in production (HESTIA_ENV=production) the gateway refuses to start if a
  required secret is missing or too short: a half-configured safety system
  should fail loudly, not run with a default password;
- in development, missing secrets are generated once, stored in the data
  directory (never in git) and a warning is logged, so `uvicorn` just works.
"""

from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class MailServer:
    """Where alert e-mails go out from (see Settings.mail_server)."""

    provider: str
    host: str
    port: int
    starttls: bool
    username: str
    password: str = field(repr=False)  # never in logs or error messages
    sender: str = ""


class Settings(BaseSettings):
    # hide_input_in_errors: a configuration error must never print the raw
    # environment (passwords, keys) into the logs.
    model_config = SettingsConfigDict(
        env_prefix="HESTIA_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    # ── General ──────────────────────────────────────────────────────────────
    env: Literal["development", "production"] = "development"
    mode: Literal["twin", "device"] = "twin"
    site_name: str = "Demo building, Tunis"
    data_dir: Path = Path("data")
    log_level: str = "INFO"

    # ── Secrets ──────────────────────────────────────────────────────────────
    secret_key: SecretStr | None = None  # signs session cookies (≥ 32 chars)
    journal_key: SecretStr | None = None  # HMAC key of the safety journal (≥ 64 hex chars)

    # ── Web security ─────────────────────────────────────────────────────────
    cookie_secure: bool | None = None  # default: True in production
    session_hours: int = Field(12, ge=1, le=72)
    login_max_failures: int = Field(5, ge=3, le=20)
    login_lockout_minutes: int = Field(15, ge=1, le=1440)
    allowed_origins: list[str] = []  # CORS; empty = same origin only (recommended)
    demo_users: bool = (
        False  # twin mode only (never with a real device): "visitor" and "operator" demo logins
    )

    # ── Digital twin ─────────────────────────────────────────────────────────
    twin_speed: float = Field(60.0, ge=1.0, le=3600.0)  # simulated seconds per real second
    twin_step_s: float = Field(10.0, ge=1.0, le=60.0)  # physics step (simulated seconds)
    twin_seed: int = 42

    # ── Simulator (a private whole-site sandbox per signed-in session) ───────
    sim_enabled: bool = True
    sim_max_sessions: int = Field(8, ge=1, le=64)  # each one runs its own physics loop
    sim_idle_minutes: int = Field(15, ge=1, le=240)  # closed after this long unwatched

    # ── Real device over MQTT (HESTIA_MODE=device) ───────────────────────────
    mqtt_host: str = "mosquitto"
    mqtt_port: int = 8883
    mqtt_tls: bool = True
    mqtt_ca_cert: Path | None = None
    mqtt_username: str = "hestia-gateway"
    mqtt_password: SecretStr | None = None
    topic_prefix: str = "hestia"
    site_id: str = Field("site1", pattern=r"^[a-zA-Z0-9_-]{1,32}$")
    device_id: str = Field("esp32_1", pattern=r"^[a-zA-Z0-9_-]{1,32}$")
    device_command_key: SecretStr | None = None  # shared with the firmware (COMMAND_HMAC_KEY)
    device_period_s: float = Field(2.0, ge=0.5, le=60.0)
    # Where solar production comes from on a real site:
    #   weather  estimate from live Open-Meteo irradiance × pv_kwp (needs internet)
    #   bench    a fixed 1 kW, for a lab bench powered from the mains
    pv_source: Literal["weather", "bench"] = "weather"
    site_latitude: float = Field(36.8, ge=-90, le=90)
    site_longitude: float = Field(10.2, ge=-180, le=180)
    pv_kwp: float = Field(1.8, gt=0, le=1000)
    pv_tilt_deg: float = Field(30.0, ge=0, le=90)
    pv_azimuth_deg: float = Field(0.0, ge=-180, le=180)  # 0 = facing the equator, negative = east
    # The stack on the controller: its rating and minimum load set when the
    # energy manager may start it (physics/electrolyser.py explains the minimum).
    stack_rated_w: float = Field(1000.0, gt=0, le=1_000_000)
    stack_min_load: float = Field(0.20, ge=0.05, le=0.6)
    bench_supply_w: float = Field(1000.0, ge=0, le=1_000_000)  # pv_source=bench
    permit_valid_s: int = Field(120, ge=30, le=900)  # the device drops an unrefreshed permit after this

    # ── E-mail alerts (off until an administrator turns them on) ─────────────
    # H₂ alarm, untrusted sensor, over-temperature, controller offline.
    email_alerts: bool = False
    # gmail: a Gmail account with an app password (server settings are known);
    # smtp:  any other mail server, configured with the smtp_* fields below.
    email_provider: Literal["gmail", "smtp"] = "gmail"
    gmail_address: str = ""
    gmail_app_password: SecretStr | None = None  # 16 letters from myaccount.google.com/apppasswords
    smtp_host: str = ""
    smtp_port: int = Field(587, ge=1, le=65535)  # 587 = STARTTLS, 465 = TLS from the start
    smtp_starttls: bool = True
    smtp_username: str = ""
    smtp_password: SecretStr | None = None
    smtp_from: str = ""
    alert_recipients: list[str] = []
    alert_cooldown_minutes: int = Field(15, ge=1, le=1440)

    # ── Trust layer ──────────────────────────────────────────────────────────
    h2_baseline_ppm: float = Field(55.0, ge=0.0, le=500.0)
    bump_test_days: int = Field(30, ge=1, le=365)
    calibration_days: int = Field(180, ge=7, le=730)
    inspection_days: int = Field(365, ge=30, le=1095)
    maintenance_grace_days: int = Field(14, ge=0, le=90)

    # ── Planner ──────────────────────────────────────────────────────────────
    planner_internet: bool = True  # fetch weather years from Open-Meteo; False = bundled Tunis year only

    @model_validator(mode="after")
    def _secrets(self) -> Settings:
        if self.cookie_secure is None:
            self.cookie_secure = self.env == "production"
        if self.env == "production":
            problems = []
            if not self.secret_key or len(self.secret_key.get_secret_value()) < 32:
                problems.append("HESTIA_SECRET_KEY (≥ 32 characters)")
            if not self.journal_key or len(self.journal_key.get_secret_value()) < 64:
                problems.append("HESTIA_JOURNAL_KEY (≥ 64 hex characters)")
            if self.mode == "device":
                if not self.mqtt_password:
                    problems.append("HESTIA_MQTT_PASSWORD")
                if not self.device_command_key or len(self.device_command_key.get_secret_value()) < 64:
                    problems.append("HESTIA_DEVICE_COMMAND_KEY (≥ 64 hex characters)")
                if self.mqtt_tls and not self.mqtt_ca_cert:
                    problems.append("HESTIA_MQTT_CA_CERT")
            if problems:
                raise ValueError("Missing or weak production settings: " + ", ".join(problems))
        if self.email_alerts:
            # Turned on on purpose, so a wrong setting must stop the gateway
            # rather than leave everyone believing they will be warned.
            problems = self._email_problems()
            if problems:
                raise ValueError("E-mail alerts are on but not usable: " + "; ".join(problems))
        # Demo logins can only ever reach a simulation: a public demo runs
        # HESTIA_ENV=production HESTIA_MODE=twin HESTIA_DEMO_USERS=true, while
        # a real installation (HESTIA_MODE=device) can never have them.
        if self.demo_users and self.mode != "twin":
            raise ValueError("HESTIA_DEMO_USERS is only allowed in twin mode")
        return self

    # ── E-mail alerts ────────────────────────────────────────────────────────

    def _email_problems(self) -> list[str]:
        problems = []
        if not self.alert_recipients:
            problems.append('HESTIA_ALERT_RECIPIENTS is empty (e.g. ["you@example.com"])')
        problems += [
            f"not an e-mail address in HESTIA_ALERT_RECIPIENTS: {r!r}"
            for r in self.alert_recipients
            if not EMAIL.match(r)
        ]
        if self.email_provider == "gmail":
            if not EMAIL.match(self.gmail_address):
                problems.append("HESTIA_GMAIL_ADDRESS must be the Gmail address that sends the alerts")
            password = "".join(
                (self.gmail_app_password.get_secret_value() if self.gmail_app_password else "").split()
            )
            if not re.fullmatch(r"[A-Za-z]{16}", password):
                problems.append(
                    "HESTIA_GMAIL_APP_PASSWORD must be a 16-letter app password from "
                    "https://myaccount.google.com/apppasswords (not the account's normal password)"
                )
        else:
            if not self.smtp_host:
                problems.append("HESTIA_SMTP_HOST is empty")
            if not EMAIL.match(self.smtp_from):
                problems.append("HESTIA_SMTP_FROM must be the sender's e-mail address")
        return problems

    def mail_server(self) -> MailServer | None:
        """The configured mail server, or None while e-mail alerts are off."""
        if not self.email_alerts:
            return None
        if self.email_provider == "gmail":
            password = self.gmail_app_password.get_secret_value() if self.gmail_app_password else ""
            # Google shows app passwords in groups of four; the spaces are not part of it.
            return MailServer(
                provider="gmail",
                host="smtp.gmail.com",
                port=587,
                starttls=True,
                username=self.gmail_address,
                password="".join(password.split()),
                sender=self.gmail_address,
            )
        return MailServer(
            provider="smtp",
            host=self.smtp_host,
            port=self.smtp_port,
            starttls=self.smtp_starttls,
            username=self.smtp_username,
            password=self.smtp_password.get_secret_value() if self.smtp_password else "",
            sender=self.smtp_from,
        )

    # ── Secrets with a development fallback ──────────────────────────────────

    def _dev_secret(self, name: str, nbytes: int) -> str:
        path = self.data_dir / f".dev-{name}"
        if path.exists():
            return path.read_text(encoding="ascii").strip()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        value = secrets.token_hex(nbytes)
        path.write_text(value, encoding="ascii")
        log.warning(
            "Generated a development %s in %s (set HESTIA_%s in production)", name, path, name.upper()
        )
        return value

    def session_secret(self) -> str:
        return self.secret_key.get_secret_value() if self.secret_key else self._dev_secret("secret_key", 32)

    def journal_secret(self) -> bytes:
        value = (
            self.journal_key.get_secret_value() if self.journal_key else self._dev_secret("journal_key", 32)
        )
        return value.encode("utf-8")

    def command_secret(self) -> bytes:
        if self.device_command_key:
            return bytes.fromhex(self.device_command_key.get_secret_value())
        return bytes.fromhex(self._dev_secret("device_command_key", 32))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "hestia.sqlite3"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
