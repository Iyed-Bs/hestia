"""
A believable maintenance history, for the demo gateway and every simulator.

Five monthly bump tests, a recent calibration of each sensor and an annual
inspection: a twin or a simulator starts with a healthy, documented H₂
detector, like a well-run installation, and the trust layer has a real
history to judge.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hestia.trust.maintenance import MaintenanceLog


def seed_maintenance(maintenance: MaintenanceLog, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    for months_ago in (5, 4, 3, 2, 1):
        maintenance.record(
            kind="bump_test",
            sensor="h2",
            technician="demo-technician",
            baseline_ppm=55,
            data={"gas_ppm": 1000, "peak_reading_ppm": 900 - 10 * months_ago},
            performed_at=now - timedelta(days=30 * months_ago - 3),
            notes="Monthly bump test with 1000 ppm H₂ in air",
        )
    maintenance.record(
        kind="calibration",
        sensor="h2",
        technician="demo-technician",
        performed_at=now - timedelta(days=62),
        data={"span_gas_ppm": 1000, "span_reading_ppm": 1012, "clean_air_ppm": 55},
    )
    maintenance.record(
        kind="calibration",
        sensor="electrolyte",
        technician="demo-technician",
        performed_at=now - timedelta(days=62),
        data={"std_low_pct": 20.0, "reading_low_pct": 20.3, "std_high_pct": 30.0, "reading_high_pct": 29.8},
        notes="Density transmitter checked in 20 and 30 wt% KOH standards",
    )
    maintenance.record(
        kind="inspection",
        sensor="installation",
        technician="demo-inspector",
        performed_at=now - timedelta(days=140),
        data={"passed": 1},
        notes="Annual inspection: ventilation, fittings, relay trip test",
    )
