"""Trust layer: the journal detects tampering, maintenance rules, sensor integrity."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hestia.storage.db import Database
from hestia.trust.integrity import IntegrityConfig, IntegrityMonitor, Level
from hestia.trust.journal import EventKind, SafetyJournal
from hestia.trust.maintenance import MaintenanceLog, MaintenancePlan
from hestia.twin.device import VirtualDevice
from hestia.twin.faults import FaultName
from hestia.twin.plant import BenchModel

KEY = b"k" * 32


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "hestia.sqlite3")


@pytest.fixture
def journal(db: Database) -> SafetyJournal:
    return SafetyJournal(db, KEY)


# ── Journal ──────────────────────────────────────────────────────────────────


def _fill(journal: SafetyJournal) -> None:
    journal.append(EventKind.SYSTEM_START, "Gateway started")
    journal.append(EventKind.ALARM_LATCHED, "H₂ alarm latched at 690 ppm", actor="device:esp32_1")
    journal.append(
        EventKind.ALARM_RESET, "H₂ alarm reset", actor="alice", details={"reason": "fitting tightened"}
    )


def test_intact_journal_verifies(journal: SafetyJournal) -> None:
    _fill(journal)
    result = journal.verify()
    assert result.ok and result.checked == 3
    assert result.head_hash == journal.head_hash()


@pytest.mark.parametrize(
    ("sql", "problem"),
    [
        ("UPDATE journal SET summary = 'Nothing happened' WHERE id = 2", "modified"),
        ("UPDATE journal SET actor = 'someone-else' WHERE id = 3", "modified"),
        ("DELETE FROM journal WHERE id = 2", "missing"),
    ],
)
def test_tampering_is_detected(journal: SafetyJournal, db: Database, sql: str, problem: str) -> None:
    _fill(journal)
    with db.transaction() as cur:
        cur.execute(sql)
    result = journal.verify()
    assert not result.ok
    assert problem in result.problem


def test_entries_forged_without_the_key_are_detected(db: Database) -> None:
    honest = SafetyJournal(db, KEY)
    _fill(honest)
    forger = SafetyJournal(db, b"x" * 32)  # knows the algorithm, not the key
    forger.append(EventKind.ALARM_RESET, "Forged reset", actor="intruder")
    result = honest.verify()
    assert not result.ok and result.first_broken_id == 4 and "signature" in result.problem


def test_short_key_is_refused(db: Database) -> None:
    with pytest.raises(ValueError):
        SafetyJournal(db, b"short")


# ── Maintenance ──────────────────────────────────────────────────────────────


def test_bump_test_verdict_is_computed_not_declared(journal: SafetyJournal, db: Database) -> None:
    log = MaintenanceLog(db, journal)
    ok = log.record(
        kind="bump_test",
        sensor="h2",
        technician="alice",
        data={"gas_ppm": 1000, "peak_reading_ppm": 820},
        baseline_ppm=55,
    )
    poisoned = log.record(
        kind="bump_test",
        sensor="h2",
        technician="alice",
        data={"gas_ppm": 1000, "peak_reading_ppm": 140},
        baseline_ppm=55,
    )
    assert ok["result"] == "pass" and poisoned["result"] == "fail"
    assert log.h2_sensor_standing()[0] == "untrusted"
    assert journal.verify().ok


def test_overdue_maintenance_degrades_then_untrusts(journal: SafetyJournal, db: Database) -> None:
    log = MaintenanceLog(db, journal, MaintenancePlan(bump_test_days=30, grace_days=14))
    done = datetime(2026, 1, 1, tzinfo=UTC)
    log.record(
        kind="bump_test",
        sensor="h2",
        technician="alice",
        data={"gas_ppm": 1000, "peak_reading_ppm": 900},
        baseline_ppm=55,
        performed_at=done,
    )
    log.record(
        kind="calibration",
        sensor="h2",
        technician="alice",
        data={"span_gas_ppm": 1000, "span_reading_ppm": 1040},
        performed_at=done,
    )
    assert log.h2_sensor_standing(done + timedelta(days=10))[0] == "ok"
    assert log.h2_sensor_standing(done + timedelta(days=35))[0] == "degraded"
    assert log.h2_sensor_standing(done + timedelta(days=50))[0] == "untrusted"


def test_replacing_the_sensor_requires_a_new_bump_test(journal: SafetyJournal, db: Database) -> None:
    log = MaintenanceLog(db, journal)
    t0 = datetime.now(UTC) - timedelta(days=2)
    log.record(
        kind="bump_test",
        sensor="h2",
        technician="alice",
        data={"gas_ppm": 1000, "peak_reading_ppm": 900},
        baseline_ppm=55,
        performed_at=t0,
    )
    log.record(kind="sensor_replaced", sensor="h2", technician="alice", data={})
    bump = next(c for c in log.status() if c.kind == "bump_test")
    assert bump.state == "never"


# ── Sensor integrity, driven by the twin's faults ────────────────────────────


def _watch(fault: FaultName | None, minutes: float, *, running: bool = True) -> IntegrityMonitor:
    device = VirtualDevice(BenchModel(seed=11))
    device.set_permit(running)
    monitor = IntegrityMonitor(IntegrityConfig(expected_period_s=10))
    if fault:
        device.set_fault(fault, True)
    now = 0.0
    for _ in range(int(minutes * 6)):
        device.set_permit(running)  # the gateway refreshes the permit's lease every cycle
        telemetry, _ = device.cycle(10)
        now += 10
        if telemetry:
            monitor.observe(telemetry, now)
    monitor.now = now  # type: ignore[attr-defined]
    return monitor


def test_healthy_sensors_are_trusted() -> None:
    monitor = _watch(None, 30)
    report = monitor.assess(monitor.now)  # type: ignore[attr-defined]
    assert report.h2_trusted, report.as_dict()
    assert report.sensors["h2"].level is Level.OK


@pytest.mark.parametrize(
    ("fault", "minutes", "sensor", "code"),
    [
        ("h2_stuck", 12, "h2", "frozen"),
        ("h2_dead", 7, "h2", "dead"),
        ("h2_drift", 220, "h2", "drift"),
        ("temp_stuck", 20, "temperature", "implausible"),
        ("temp_disconnect", 1, "temperature", "invalid"),
    ],
)
def test_faults_are_caught(fault: FaultName, minutes: float, sensor: str, code: str) -> None:
    monitor = _watch(fault, minutes)
    report = monitor.assess(monitor.now)  # type: ignore[attr-defined]
    codes = [f.code for f in report.sensors[sensor].findings]
    assert code in codes, report.as_dict()
    if sensor == "h2":
        assert not report.h2_trusted


def test_a_regulating_cooling_loop_is_not_mistaken_for_a_stuck_probe() -> None:
    monitor = _watch(None, 6 * 60)  # warm-up, then hours of the loop switching at 55 and 50 °C
    report = monitor.assess(monitor.now)  # type: ignore[attr-defined]
    assert report.sensors["temperature"].findings == [], report.as_dict()


def test_a_real_leak_is_not_mistaken_for_drift() -> None:
    monitor = _watch("leak_small", 15)
    report = monitor.assess(monitor.now)  # type: ignore[attr-defined]
    assert "drift" not in [f.code for f in report.sensors["h2"].findings]


def test_silence_makes_the_link_untrusted() -> None:
    monitor = _watch(None, 5)
    later = monitor.now + 120  # type: ignore[attr-defined]
    report = monitor.assess(later)
    assert not report.h2_trusted
    assert report.sensors["link"].findings[0].code == "stale"


def test_a_poisoned_sensor_is_only_caught_by_a_bump_test(journal: SafetyJournal, db: Database) -> None:
    # In clean air a poisoned sensor looks perfect…
    monitor = _watch("h2_poisoned", 30)
    assert monitor.assess(monitor.now).h2_trusted  # type: ignore[attr-defined]
    # …until a bump test with real gas shows it barely responds.
    device = VirtualDevice(BenchModel(seed=12))
    device.set_fault("h2_poisoned", True)
    peak = device.bump_test(1000)  # what the twin's poisoned sensor shows
    log = MaintenanceLog(db, journal)
    log.record(
        kind="bump_test",
        sensor="h2",
        technician="alice",
        data={"gas_ppm": 1000, "peak_reading_ppm": peak},
        baseline_ppm=55,
    )
    monitor.set_maintenance(*log.h2_sensor_standing())
    report = monitor.assess(monitor.now)  # type: ignore[attr-defined]
    assert not report.h2_trusted and "bump test" in report.h2_why
