"""
E-mail alerts for critical safety events: H₂ alarm, untrusted sensor,
over-temperature, controller offline.

Off by default. An administrator turns them on in .env
(HESTIA_EMAIL_ALERTS=true) with a Gmail address and its app password, or any
other mail server; see .env.example. Whatever happens to e-mail, the
dashboard and the journal still record everything.

- Mail goes out on a worker thread: a slow mail server never delays control.
- The connection is always encrypted (STARTTLS on 587, TLS on 465).
- Each kind of alert has a cooldown, so a flapping sensor cannot flood
  inboxes; the journal still records every single occurrence.
- An alert that cannot be sent is journalled: nobody should believe they
  were warned when they were not.
- Messages are bilingual (English, then French).
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
import time
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

from hestia.settings import MailServer, Settings
from hestia.trust.journal import EventKind, SafetyJournal

log = logging.getLogger(__name__)


class AlertError(RuntimeError):
    """The mail server refused or could not be reached (the message says why, without secrets)."""


def _explain(exc: Exception, server: MailServer) -> str:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        if server.provider == "gmail":
            return (
                "Gmail refused the login: check HESTIA_GMAIL_ADDRESS and HESTIA_GMAIL_APP_PASSWORD "
                "(an app password, with 2-Step Verification on for that account)"
            )
        return "The mail server refused the login: check HESTIA_SMTP_USERNAME and HESTIA_SMTP_PASSWORD"
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "The mail server refused the recipients: check HESTIA_ALERT_RECIPIENTS"
    if isinstance(exc, smtplib.SMTPException):
        return f"Mail server error ({type(exc).__name__})"
    return f"Cannot reach {server.host}:{server.port} ({type(exc).__name__})"


class Notifier:
    def __init__(self, settings: Settings, journal: SafetyJournal | None = None) -> None:
        self.s = settings
        self.journal = journal
        self.server = settings.mail_server()
        self.enabled = self.server is not None
        self._last_sent: dict[str, float] = {}
        self.last_sent_at: datetime | None = None
        self.last_error = ""

    def status(self) -> dict[str, Any]:
        """For the administration page. Never includes the password."""
        return {
            "enabled": self.enabled,
            "provider": self.server.provider if self.server else self.s.email_provider,
            "sender": self.server.sender if self.server else "",
            "recipients": list(self.s.alert_recipients) if self.enabled else [],
            "cooldown_minutes": self.s.alert_cooldown_minutes,
            "last_sent_at": self.last_sent_at.isoformat(timespec="seconds") if self.last_sent_at else None,
            "last_error": self.last_error,
        }

    async def alert(self, key: str, subject_en: str, subject_fr: str, body_en: str, body_fr: str) -> bool:
        """Send an alert unless alerts are off or the same key was sent within the cooldown."""
        if self.server is None:
            return False
        now = time.monotonic()
        if now - self._last_sent.get(key, -1e12) < self.s.alert_cooldown_minutes * 60:
            return False
        self._last_sent[key] = now
        try:
            await self._deliver(
                self.server, self._message(self.server, subject_en, subject_fr, body_en, body_fr)
            )
        except AlertError as exc:
            if self.journal:
                self.journal.append(
                    EventKind.ALERT_FAILED,
                    f"Alert e-mail not sent ({subject_en}): {exc}",
                    details={"alert": key},
                )
            return False
        return True

    async def send_test(self, requested_by: str) -> None:
        """Send a test e-mail now (no cooldown). Raises AlertError with a readable reason."""
        if self.server is None:
            raise AlertError("E-mail alerts are off: set HESTIA_EMAIL_ALERTS=true in .env, then restart")
        site = self.s.site_name
        message = self._message(
            self.server,
            "Test e-mail",
            "E-mail de test",
            f"Alerts from {site} reach this address. Sent from the dashboard by {requested_by}.",
            f"Les alertes de {site} arrivent bien à cette adresse. "
            f"Envoyé depuis le tableau de bord par {requested_by}.",
        )
        await self._deliver(self.server, message)
        if self.journal:
            self.journal.append(
                EventKind.ALERT_TEST,
                f"Test e-mail sent to {len(self.s.alert_recipients)} recipient(s)",
                actor=requested_by,
            )

    def _message(
        self, server: MailServer, subject_en: str, subject_fr: str, body_en: str, body_fr: str
    ) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = f"[Hestia · {self.s.site_name}] {subject_en} / {subject_fr}"
        message["From"] = server.sender
        message["To"] = ", ".join(self.s.alert_recipients)
        message.set_content(f"{body_en}\n\n— — —\n\n{body_fr}\n")
        return message

    async def _deliver(self, server: MailServer, message: EmailMessage) -> None:
        try:
            await asyncio.to_thread(self._send, server, message)
        except (OSError, smtplib.SMTPException) as exc:
            self.last_error = _explain(exc, server)
            log.error("Alert e-mail failed: %s", self.last_error)
            raise AlertError(self.last_error) from exc
        self.last_sent_at = datetime.now(UTC)
        self.last_error = ""

    @staticmethod
    def _send(server: MailServer, message: EmailMessage) -> None:
        context = ssl.create_default_context()
        smtp: smtplib.SMTP
        if server.port == 465:
            smtp = smtplib.SMTP_SSL(server.host, server.port, timeout=15, context=context)
        else:
            smtp = smtplib.SMTP(server.host, server.port, timeout=15)
        with smtp:
            if server.port != 465 and server.starttls:
                smtp.starttls(context=context)
            if server.username and server.password:
                smtp.login(server.username, server.password)
            smtp.send_message(message)
