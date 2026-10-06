"""
The safety journal: an append-only, tamper-evident record of everything that
matters for safety.

Why it exists. A building owner, an inspector or an insurer will ask the same
questions after any incident, and long before one: when did an alarm go off,
who reset it and why, were the gas sensors tested on time, did anyone change
a threshold? On a small installation those answers usually live in nobody's
head. Here they are written automatically, as the events happen.

How it resists tampering. Each entry stores:

    hash = SHA-256( previous entry's hash ‖ canonical JSON of this entry )
    mac  = HMAC-SHA256( journal key, hash )

Changing, deleting or reordering any past entry breaks every hash after it,
and without the journal key (HESTIA_JOURNAL_KEY, kept only on the gateway)
nobody can forge new valid MACs. Every safety report prints the current head
hash: keep it (paper, e-mail) and you can prove later that the history you
are shown is the one that existed that day.

Use:
    journal.append(EventKind.ALARM_RESET, "H₂ alarm reset", actor="operator",
                   details={"reason": "fitting re-tightened, leak test passed"})
    journal.verify()   # → Verification(ok=True, checked=…, head_hash=…)
    hestia verify-journal   (the same check from the command line)
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from hestia.storage.db import Database

GENESIS = "0" * 64


class EventKind(StrEnum):
    SYSTEM_START = "system_start"
    ALARM_LATCHED = "alarm_latched"
    ALARM_RESET = "alarm_reset"
    OVER_TEMPERATURE = "over_temperature"
    MODE_CHANGED = "mode_changed"
    THRESHOLD_CHANGED = "threshold_changed"
    MANUAL_ACTUATOR = "manual_actuator"
    COMMAND_REJECTED = "command_rejected"
    SENSOR_TRUST_CHANGED = "sensor_trust_changed"
    PERMIT_WITHDRAWN_FOR_SAFETY = "permit_withdrawn_for_safety"
    DEVICE_OFFLINE = "device_offline"
    DEVICE_ONLINE = "device_online"
    DEVICE_REBOOTED = "device_rebooted"
    TELEMETRY_REJECTED = "telemetry_rejected"
    BUMP_TEST = "bump_test"
    CALIBRATION = "calibration"
    INSPECTION = "inspection"
    SENSOR_REPLACED = "sensor_replaced"
    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    USER_CHANGED = "user_changed"
    MODEL_INTEGRITY = "model_integrity"
    REPORT_GENERATED = "report_generated"
    FAULT_INJECTED = "fault_injected"  # simulation only, clearly labelled in reports
    ALERT_FAILED = "alert_failed"  # an alert e-mail could not be sent
    H2_WARNING = "h2_warning"  # stage-1 hydrogen detection, on or cleared
    PRESSURE_RELIEF = "pressure_relief"  # the storage relief valve vented hydrogen
    ALERT_TEST = "alert_test"
    SCENARIO = "scenario"  # simulator only: a scenario was loaded


SEVERITY: dict[EventKind, str] = {
    EventKind.ALARM_LATCHED: "critical",
    EventKind.OVER_TEMPERATURE: "critical",
    EventKind.PERMIT_WITHDRAWN_FOR_SAFETY: "warning",
    EventKind.SENSOR_TRUST_CHANGED: "warning",
    EventKind.DEVICE_OFFLINE: "warning",
    EventKind.COMMAND_REJECTED: "warning",
    EventKind.TELEMETRY_REJECTED: "warning",
    EventKind.LOGIN_FAILED: "warning",
    EventKind.MODEL_INTEGRITY: "critical",
    EventKind.ALARM_RESET: "warning",
    EventKind.ALERT_FAILED: "warning",
    EventKind.H2_WARNING: "warning",
    EventKind.PRESSURE_RELIEF: "critical",
}


@dataclass(frozen=True)
class Entry:
    id: int
    ts: datetime
    kind: str
    severity: str
    actor: str
    summary: str
    details: dict[str, Any]
    hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts.isoformat(),
            "kind": self.kind,
            "severity": self.severity,
            "actor": self.actor,
            "summary": self.summary,
            "details": self.details,
            "hash": self.hash,
        }


@dataclass(frozen=True)
class Verification:
    ok: bool
    checked: int
    head_hash: str
    first_broken_id: int | None = None
    problem: str = ""


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


class SafetyJournal:
    def __init__(self, db: Database, key: bytes) -> None:
        if len(key) < 32:
            raise ValueError("The journal key must be at least 32 bytes (HESTIA_JOURNAL_KEY)")
        self.db = db
        self._key = key

    def _digest(
        self,
        prev_hash: str,
        entry_id: int,
        ts: str,
        kind: str,
        severity: str,
        actor: str,
        summary: str,
        details: str,
    ) -> tuple[str, str]:
        body = _canonical(
            {
                "id": entry_id,
                "ts": ts,
                "kind": kind,
                "severity": severity,
                "actor": actor,
                "summary": summary,
                "details": details,
            }
        )
        digest = hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()
        mac = hmac.new(self._key, digest.encode("ascii"), hashlib.sha256).hexdigest()
        return digest, mac

    def append(
        self,
        kind: EventKind,
        summary: str,
        *,
        actor: str = "system",
        details: dict[str, Any] | None = None,
        ts: datetime | None = None,
        severity: str | None = None,
    ) -> Entry:
        when = (ts or datetime.now(UTC)).astimezone(UTC)
        ts_text = when.isoformat(timespec="seconds")
        level = severity or SEVERITY.get(kind, "info")
        details_text = _canonical(details or {})
        with self.db.transaction() as cur:
            row = cur.execute("SELECT id, hash FROM journal ORDER BY id DESC LIMIT 1").fetchone()
            prev_hash = row["hash"] if row else GENESIS
            entry_id = (row["id"] + 1) if row else 1
            digest, mac = self._digest(
                prev_hash, entry_id, ts_text, kind.value, level, actor, summary, details_text
            )
            cur.execute(
                "INSERT INTO journal (id, ts, kind, severity, actor, summary, details, prev_hash, hash, mac)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (entry_id, ts_text, kind.value, level, actor, summary, details_text, prev_hash, digest, mac),
            )
        return Entry(entry_id, when, kind.value, level, actor, summary, details or {}, digest)

    def entries(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        kinds: list[str] | None = None,
        limit: int = 200,
        before_id: int | None = None,
    ) -> list[Entry]:
        clauses: list[str] = []
        params: list[str | int] = []
        if since:
            clauses.append("ts >= ?")
            params.append(since.astimezone(UTC).isoformat(timespec="seconds"))
        if until:
            clauses.append("ts <= ?")
            params.append(until.astimezone(UTC).isoformat(timespec="seconds"))
        if kinds:
            clauses.append(f"kind IN ({','.join('?' * len(kinds))})")
            params.extend(kinds)
        if before_id:
            clauses.append("id < ?")
            params.append(before_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        # Only fixed clause strings are joined into the SQL; every value is a bound parameter.
        sql = "SELECT * FROM journal " + where + " ORDER BY id DESC LIMIT ?"  # noqa: S608  # nosec B608
        rows = self.db.query(sql, (*params, max(1, min(limit, 1000))))
        return [
            Entry(
                r["id"],
                datetime.fromisoformat(r["ts"]),
                r["kind"],
                r["severity"],
                r["actor"],
                r["summary"],
                json.loads(r["details"]),
                r["hash"],
            )
            for r in rows
        ]

    def head_hash(self) -> str:
        rows = self.db.query("SELECT hash FROM journal ORDER BY id DESC LIMIT 1")
        return rows[0]["hash"] if rows else GENESIS

    def verify(self) -> Verification:
        """Re-compute every hash and MAC from the first entry to the last."""
        prev, expected_id, count = GENESIS, 1, 0
        for r in self.db.query("SELECT * FROM journal ORDER BY id ASC"):
            if r["id"] != expected_id:
                return Verification(False, count, prev, r["id"], f"entry {expected_id} is missing")
            if r["prev_hash"] != prev:
                return Verification(
                    False, count, prev, r["id"], "chain link broken (an earlier entry changed)"
                )
            digest, mac = self._digest(
                prev, r["id"], r["ts"], r["kind"], r["severity"], r["actor"], r["summary"], r["details"]
            )
            if digest != r["hash"]:
                return Verification(False, count, prev, r["id"], "entry content was modified")
            if not hmac.compare_digest(mac, r["mac"]):
                return Verification(
                    False, count, prev, r["id"], "signature invalid (written without the journal key)"
                )
            prev, expected_id, count = digest, expected_id + 1, count + 1
        return Verification(True, count, prev)
