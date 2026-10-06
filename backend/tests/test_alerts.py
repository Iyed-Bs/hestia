"""E-mail alerts: off by default, Gmail with an app password, failures journalled."""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Any, ClassVar

import pytest

from hestia.runtime.notifier import AlertError, Notifier
from hestia.settings import Settings
from hestia.storage.db import Database
from hestia.trust.journal import EventKind, SafetyJournal

APP_PASSWORD = "abcd efgh ijkl mnop"  # the format Google shows (16 letters in groups of four)


def gmail(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "data_dir": tmp_path,
        "email_alerts": True,
        "gmail_address": "building.alerts@gmail.com",
        "gmail_app_password": APP_PASSWORD,
        "alert_recipients": ["owner@example.com", "technician@example.com"],
    }
    return Settings(**(values | overrides))


class FakeSMTP:
    """Stands in for smtplib.SMTP and records what the notifier does."""

    sent: ClassVar[list[EmailMessage]] = []
    logins: ClassVar[list[tuple[str, str]]] = []
    starttls_calls: ClassVar[int] = 0
    refuse_login: ClassVar[bool] = False

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host, self.port = host, port

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def starttls(self, context: object) -> None:
        FakeSMTP.starttls_calls += 1

    def login(self, user: str, password: str) -> None:
        if FakeSMTP.refuse_login:
            raise smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")
        FakeSMTP.logins.append((user, password))

    def send_message(self, message: EmailMessage) -> None:
        FakeSMTP.sent.append(message)


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.sent, FakeSMTP.logins, FakeSMTP.starttls_calls, FakeSMTP.refuse_login = [], [], 0, False
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


@pytest.fixture
def journal(tmp_path: Path) -> SafetyJournal:
    return SafetyJournal(Database(tmp_path / "hestia.sqlite3"), b"k" * 32)


# ── Settings ─────────────────────────────────────────────────────────────────


def test_alerts_are_off_until_turned_on(tmp_path: Path) -> None:
    s = Settings(data_dir=tmp_path, gmail_address="x@gmail.com", gmail_app_password=APP_PASSWORD)
    assert s.mail_server() is None


def test_gmail_needs_only_an_address_and_an_app_password(tmp_path: Path) -> None:
    server = gmail(tmp_path).mail_server()
    assert server is not None
    assert (server.host, server.port, server.starttls) == ("smtp.gmail.com", 587, True)
    assert server.password == "abcdefghijklmnop"  # spaces as Google displays them are removed
    assert "abcd" not in repr(server)  # the password never shows up in logs


def test_turned_on_but_incomplete_stops_the_gateway_and_says_why(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as error:
        gmail(tmp_path, gmail_app_password="MyNormalGooglePassword!1", alert_recipients=[])
    text = str(error.value)
    assert "HESTIA_ALERT_RECIPIENTS" in text
    assert "apppasswords" in text
    assert "MyNormalGooglePassword" not in text


def test_recipients_must_be_addresses(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not an e-mail address"):
        gmail(tmp_path, alert_recipients=["owner"])


def test_another_mail_server_can_be_used(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="HESTIA_SMTP_HOST"):
        gmail(tmp_path, email_provider="smtp")
    server = gmail(
        tmp_path, email_provider="smtp", smtp_host="mail.example.com", smtp_from="hestia@example.com"
    ).mail_server()
    assert server is not None and server.host == "mail.example.com"


# ── Sending ──────────────────────────────────────────────────────────────────


def test_an_alert_goes_out_encrypted_once_per_cooldown(
    tmp_path: Path, smtp: type[FakeSMTP], journal: SafetyJournal
) -> None:
    notifier = Notifier(gmail(tmp_path), journal)
    first = asyncio.run(notifier.alert("h2-alarm", "H2 leak alarm", "Alarme fuite H2", "900 ppm", "900 ppm"))
    again = asyncio.run(notifier.alert("h2-alarm", "H2 leak alarm", "Alarme fuite H2", "950 ppm", "950 ppm"))
    assert (first, again) == (True, False)
    assert smtp.starttls_calls == 1
    assert smtp.logins == [("building.alerts@gmail.com", "abcdefghijklmnop")]
    message = smtp.sent[0]
    assert message["To"] == "owner@example.com, technician@example.com"
    assert "H2 leak alarm / Alarme fuite H2" in message["Subject"]


def test_a_refused_login_is_journalled_with_a_useful_hint(
    tmp_path: Path, smtp: type[FakeSMTP], journal: SafetyJournal
) -> None:
    smtp.refuse_login = True
    notifier = Notifier(gmail(tmp_path), journal)
    assert (
        asyncio.run(notifier.alert("overtemp", "Over-temperature", "Surchauffe", "71 °C", "71 °C")) is False
    )
    failed = journal.entries(kinds=[EventKind.ALERT_FAILED.value])
    assert len(failed) == 1 and "app password" in failed[0].summary
    assert "app password" in notifier.status()["last_error"]


def test_the_test_e_mail_is_journalled(tmp_path: Path, smtp: type[FakeSMTP], journal: SafetyJournal) -> None:
    notifier = Notifier(gmail(tmp_path), journal)
    asyncio.run(notifier.send_test("alice"))
    assert len(smtp.sent) == 1
    entry = journal.entries(kinds=[EventKind.ALERT_TEST.value])[0]
    assert entry.actor == "alice"
    status = notifier.status()
    assert status["enabled"] and status["last_sent_at"] and "password" not in str(status).lower()


def test_testing_while_off_explains_how_to_turn_it_on(tmp_path: Path) -> None:
    with pytest.raises(AlertError, match="HESTIA_EMAIL_ALERTS=true"):
        asyncio.run(Notifier(Settings(data_dir=tmp_path)).send_test("alice"))
