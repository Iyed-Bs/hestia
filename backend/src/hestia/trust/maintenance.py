"""
Maintenance of the safety sensors: bump tests, calibrations, inspections.

Gas detectors drift and get poisoned; the only way to know a hydrogen sensor
still responds is to expose it to a known concentration of test gas. Good
practice (gas-detector manufacturers, EIGA Doc 15) is a quick functional
"bump test" between full calibrations, and a calibration every few months.
Small installations rarely have anyone tracking this. Hestia does:

- it schedules each check and shows what is due, soon due or overdue;
- it decides pass/fail itself from the numbers the technician enters (gas
  applied, peak reading), so a result cannot be "declared" a pass;
- every record goes into the safety journal (who, when, values);
- an overdue or failed check lowers the H₂ sensor's trust level
  (trust/integrity.py), which can withdraw the production permit.

Default intervals are conservative starting points; set them in .env to
match the sensor manufacturer's manual and local regulations.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from hestia.storage.db import Database

from .journal import EventKind, SafetyJournal

CheckKind = Literal["bump_test", "calibration", "inspection", "sensor_replaced"]
Sensor = Literal["h2", "temperature", "electrolyte", "installation"]
CheckState = Literal["ok", "due_soon", "overdue", "never", "failed"]

# A bump test passes when the sensor shows at least this share of the test gas.
BUMP_MIN_RESPONSE = 0.5
# A calibration passes when the span reading lands within ±20 % of the gas applied.
CALIBRATION_TOLERANCE = 0.20


@dataclass(frozen=True)
class MaintenancePlan:
    bump_test_days: int = 30
    calibration_days: int = 180
    inspection_days: int = 365
    due_soon_days: int = 7
    grace_days: int = 14  # overdue beyond this → sensor no longer trusted


@dataclass(frozen=True)
class CheckStatus:
    kind: str
    sensor: str
    state: CheckState
    last_at: datetime | None
    next_due: datetime | None
    last_result: str | None
    days_overdue: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "sensor": self.sensor,
            "state": self.state,
            "last_at": self.last_at.isoformat() if self.last_at else None,
            "next_due": self.next_due.isoformat() if self.next_due else None,
            "last_result": self.last_result,
            "days_overdue": self.days_overdue,
        }


def evaluate_bump_test(
    gas_ppm: float, peak_reading_ppm: float, baseline_ppm: float, warning_ppm: float = 500.0
) -> tuple[bool, str]:
    """ISEA: enough test gas to activate the alarm at its lower setting, and a real response.

    Pass when the reading reaches the stage-1 (warning) level, which proves the
    alarm would have triggered, and the sensor rose by at least half of the
    applied concentration. Test gas below the warning level proves nothing.
    """
    if gas_ppm < warning_ppm:
        return (
            False,
            f"test gas ({gas_ppm:.0f} ppm) below the warning level ({warning_ppm:.0f} ppm): "
            "cannot prove the alarm",
        )
    response = max(0.0, peak_reading_ppm - baseline_ppm)
    ratio = response / gas_ppm if gas_ppm > 0 else 0.0
    reached = peak_reading_ppm >= warning_ppm
    passed = ratio >= BUMP_MIN_RESPONSE and reached
    verdict = f"responded with {ratio:.0%} of the {gas_ppm:.0f} ppm test gas"
    if not reached:
        verdict += f", never reaching the {warning_ppm:.0f} ppm warning level"
    return passed, verdict


def evaluate_calibration(span_gas_ppm: float, span_reading_ppm: float) -> tuple[bool, str]:
    error = abs(span_reading_ppm - span_gas_ppm) / span_gas_ppm if span_gas_ppm > 0 else 1.0
    passed = error <= CALIBRATION_TOLERANCE
    return passed, f"span reading within {error:.0%} of the reference gas"


class MaintenanceLog:
    SCHEDULE: tuple[tuple[str, str, str], ...] = (
        ("bump_test", "h2", "bump_test_days"),
        ("calibration", "h2", "calibration_days"),
        ("calibration", "electrolyte", "calibration_days"),
        ("inspection", "installation", "inspection_days"),
    )

    def __init__(self, db: Database, journal: SafetyJournal, plan: MaintenancePlan | None = None) -> None:
        self.db = db
        self.journal = journal
        self.plan = plan or MaintenancePlan()

    def record(
        self,
        *,
        kind: CheckKind,
        sensor: Sensor,
        technician: str,
        data: dict[str, float],
        notes: str = "",
        baseline_ppm: float = 0.0,
        warning_ppm: float = 500.0,
        performed_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Record a check; pass/fail is computed here from the measured values."""
        when = (performed_at or datetime.now(UTC)).astimezone(UTC)
        if kind == "bump_test":
            passed, verdict = evaluate_bump_test(
                data["gas_ppm"], data["peak_reading_ppm"], baseline_ppm, warning_ppm
            )
        elif kind == "calibration" and sensor == "h2":
            passed, verdict = evaluate_calibration(data["span_gas_ppm"], data["span_reading_ppm"])
        elif kind == "calibration" and sensor == "electrolyte":
            # Two KOH standards (e.g. 20 and 30 wt%): both readings within ±1 wt%.
            low = abs(data["reading_low_pct"] - data["std_low_pct"])
            high = abs(data["reading_high_pct"] - data["std_high_pct"])
            passed = low <= 1.0 and high <= 1.0
            verdict = (
                f"both KOH standards within ±1 wt% ({low:.2f} and {high:.2f})"
                if passed
                else f"standard readings out of tolerance ({low:.2f} and {high:.2f} wt% off)"
            )
        else:
            passed, verdict = data.get("passed", 1.0) >= 1.0, "recorded by the technician"
        result = "pass" if passed else "fail"
        event = {
            "bump_test": EventKind.BUMP_TEST,
            "calibration": EventKind.CALIBRATION,
            "inspection": EventKind.INSPECTION,
            "sensor_replaced": EventKind.SENSOR_REPLACED,
        }[kind]
        entry = self.journal.append(
            event,
            f"{kind.replace('_', ' ').capitalize()} of the {sensor} sensor: {result.upper()} ({verdict})"
            if sensor != "installation"
            else f"Installation inspection: {result.upper()}",
            actor=technician,
            details={"sensor": sensor, "result": result, "data": data, "notes": notes},
            severity="info" if passed else "critical",
            ts=when,
        )
        with self.db.transaction() as cur:
            cur.execute(
                "INSERT INTO maintenance"
                " (kind, sensor, performed_at, technician, result, data, notes, journal_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    kind,
                    sensor,
                    when.isoformat(timespec="seconds"),
                    technician,
                    result,
                    json.dumps(data),
                    notes,
                    entry.id,
                ),
            )
        return {"result": result, "verdict": verdict, "journal_id": entry.id}

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM maintenance ORDER BY performed_at DESC, id DESC LIMIT ?", (limit,)
        )
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]

    def _last(self, kind: str, sensor: str) -> tuple[datetime | None, str | None]:
        # A sensor replacement resets every check on that sensor except the
        # new sensor's own bump test, which must still be done.
        rows = self.db.query(
            "SELECT performed_at, result FROM maintenance WHERE kind = ? AND sensor = ?"
            " ORDER BY performed_at DESC, id DESC LIMIT 1",
            (kind, sensor),
        )
        if not rows:
            return None, None
        return datetime.fromisoformat(rows[0]["performed_at"]), rows[0]["result"]

    def status(self, now: datetime | None = None) -> list[CheckStatus]:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        out = []
        for kind, sensor, interval_attr in self.SCHEDULE:
            interval = timedelta(days=getattr(self.plan, interval_attr))
            last_at, last_result = self._last(kind, sensor)
            replaced_at, _ = self._last("sensor_replaced", sensor)
            if last_at is not None and replaced_at is not None and replaced_at > last_at:
                # A new sensor element: earlier tests were about the old one.
                last_at, last_result = None, None
            if last_at is None:
                out.append(CheckStatus(kind, sensor, "never", None, None, None, 0))
                continue
            next_due = last_at + interval
            overdue_days = max(0, (now - next_due).days)
            if last_result == "fail":
                state: CheckState = "failed"
            elif now > next_due:
                state = "overdue"
            elif now > next_due - timedelta(days=self.plan.due_soon_days):
                state = "due_soon"
            else:
                state = "ok"
            out.append(CheckStatus(kind, sensor, state, last_at, next_due, last_result, overdue_days))
        return out

    def h2_sensor_standing(self, now: datetime | None = None) -> tuple[str, str]:
        """What maintenance says about the H₂ sensor: ('ok'|'degraded'|'untrusted', why)."""
        checks = {(c.kind, c.sensor): c for c in self.status(now)}
        bump = checks[("bump_test", "h2")]
        cal = checks[("calibration", "h2")]
        if bump.state == "failed":
            return "untrusted", "last bump test failed: the sensor did not respond to test gas"
        if cal.state == "failed":
            return "untrusted", "last calibration failed"
        if bump.state == "never":
            return "degraded", "no bump test recorded yet"
        if bump.state == "overdue" and bump.days_overdue > self.plan.grace_days:
            return "untrusted", f"bump test overdue by {bump.days_overdue} days"
        if bump.state == "overdue" or cal.state in ("overdue", "never"):
            return (
                "degraded",
                "maintenance overdue" if cal.state != "never" else "no calibration recorded yet",
            )
        return "ok", "maintenance up to date"
