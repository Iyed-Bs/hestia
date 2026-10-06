"""
The safety report: what an inspector, an insurer or a building owner asks
for, assembled from the journal and the maintenance log in one click.

It answers, for a chosen period:
- Is the safety record intact? (journal verification + head fingerprint)
- What happened? (alarms with who reset them and why, over-temperatures,
  sensors that lost trust, controller outages, rejected commands)
- Were the detectors looked after? (bump tests, calibrations, inspections,
  with their results and what is due next)
- What is the state right now? (sensor trust levels and findings)

The dashboard renders it as a printable page (save as PDF from the browser),
and the JSON form can be archived as is. Generating a report is itself
journaled, with the fingerprint it printed, so the paper copy and the
journal can always be matched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .integrity import IntegrityReport
from .journal import EventKind, SafetyJournal
from .maintenance import MaintenanceLog

INCIDENT_KINDS = [
    EventKind.ALARM_LATCHED,
    EventKind.ALARM_RESET,
    EventKind.OVER_TEMPERATURE,
    EventKind.SENSOR_TRUST_CHANGED,
    EventKind.PERMIT_WITHDRAWN_FOR_SAFETY,
    EventKind.DEVICE_OFFLINE,
    EventKind.DEVICE_ONLINE,
    EventKind.DEVICE_REBOOTED,
    EventKind.COMMAND_REJECTED,
    EventKind.TELEMETRY_REJECTED,
    EventKind.MODEL_INTEGRITY,
    EventKind.FAULT_INJECTED,
    EventKind.H2_WARNING,
    EventKind.PRESSURE_RELIEF,
]
CHANGE_KINDS = [EventKind.MODE_CHANGED, EventKind.THRESHOLD_CHANGED, EventKind.MANUAL_ACTUATOR]


def build_report(
    *,
    journal: SafetyJournal,
    maintenance: MaintenanceLog,
    trust: IntegrityReport | None,
    site_name: str,
    mode: str,
    days: int,
    requested_by: str,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    verification = journal.verify()

    def fetch(kinds: list[EventKind]) -> list[dict[str, Any]]:
        entries = journal.entries(since=since, kinds=[k.value for k in kinds], limit=1000)
        return [e.as_dict() for e in reversed(entries)]

    incidents = fetch(INCIDENT_KINDS)
    alarms = [e for e in incidents if e["kind"] == EventKind.ALARM_LATCHED]
    resets = [e for e in incidents if e["kind"] == EventKind.ALARM_RESET]
    checks = maintenance.status(now)
    records = [
        r for r in maintenance.history(limit=500) if r["performed_at"] >= since.isoformat(timespec="seconds")
    ]

    findings: list[str] = []
    for check in checks:
        if check.state in ("overdue", "failed", "never"):
            label = f"{check.kind.replace('_', ' ')} ({check.sensor})"
            findings.append(
                {
                    "overdue": f"{label} is overdue by {check.days_overdue} days",
                    "failed": f"{label}: the last one failed",
                    "never": f"{label} has never been recorded",
                }[check.state]
            )
    unexplained = [r for r in resets if not r["details"].get("reason")]
    if unexplained:
        findings.append(
            f"{len(unexplained)} alarm reset(s) without a written reason (controller-side resets)"
        )
    if not verification.ok:
        findings.append(
            f"Journal integrity check FAILED at entry {verification.first_broken_id}: {verification.problem}"
        )
    if mode == "twin":
        findings.append("This report comes from the digital twin (simulation), not from a real installation")

    report = {
        "title": "Hydrogen installation safety report",
        "site_name": site_name,
        "mode": mode,
        "generated_at": now.isoformat(timespec="seconds"),
        "generated_by": requested_by,
        "period": {
            "from": since.isoformat(timespec="seconds"),
            "to": now.isoformat(timespec="seconds"),
            "days": days,
        },
        "integrity": {
            "ok": verification.ok,
            "entries_checked": verification.checked,
            "head_hash": verification.head_hash,
            "first_broken_id": verification.first_broken_id,
            "problem": verification.problem,
        },
        "summary": {
            "alarms": len(alarms),
            "alarm_resets": len(resets),
            "over_temperatures": sum(1 for e in incidents if e["kind"] == EventKind.OVER_TEMPERATURE),
            "sensor_trust_changes": sum(1 for e in incidents if e["kind"] == EventKind.SENSOR_TRUST_CHANGED),
            "outages": sum(1 for e in incidents if e["kind"] == EventKind.DEVICE_OFFLINE),
            "maintenance_records": len(records),
        },
        "findings": findings,
        "incidents": incidents,
        "changes": fetch(CHANGE_KINDS),
        "maintenance": {"status": [c.as_dict() for c in checks], "records": records},
        "current_state": trust.as_dict() if trust else None,
    }
    journal.append(
        EventKind.REPORT_GENERATED,
        f"Safety report generated for the last {days} days",
        actor=requested_by,
        details={"head_hash": verification.head_hash, "journal_ok": verification.ok, "days": days},
    )
    return report
