"""
Sensor integrity: can each safety sensor still vouch for what it reports?

The problem. A hydrogen detector that has quietly failed is worse than no
detector: everyone believes the building is watched. Cheap metal-oxide
sensors (MQ-8 class) drift as they age, freeze when a wire or ADC fails,
read near zero when the heater dies, and lose sensitivity when poisoned.
None of this trips an alarm on its own; most monitoring systems only check
that a value arrives and sits inside a range.

What this module does. It watches every reading stream and asks questions a
careful technician would ask, continuously:

H₂ sensor
  frozen     – a working MQ-8 always fluctuates; no movement for 10 minutes
               means the value is not being measured any more
  dead       – clean air reads ~55 ppm on a calibrated MQ-8; a reading near
               zero for 5 minutes means the element or its heater failed
  drift      – the clean-air reading has moved away from the baseline set at
               the last calibration (aging layer → false alarms, lost range)
  maintenance– bump test or calibration failed, overdue or never done
               (a poisoned sensor looks perfect in clean air: only a bump
               test with real gas can reveal it)
Electrolyte temperature probe
  invalid    – the device reports it unplugged / out of range
  implausible– the stack has been running for 15 minutes with its cooling
               loop off, yet the electrolyte did not warm up: the probe is
               stuck or not in the liquid
  frozen     – the stack is running (heating or being cooled) and the reading
               has not moved at all for 10 minutes
  spike      – an impossible jump between two consecutive readings
Electrolyte probe (KOH strength, from a density transmitter)
  invalid / frozen
Link to the controller
  stale      – no telemetry for longer than the expected period allows
  sequence   – message gaps and controller reboots are counted

Every sensor gets a level (ok / degraded / untrusted), a 0–100 score and
plain-language findings. The H₂ sensor's level matters most: when it is
untrusted, the energy manager withdraws the production permit at once, and
the change is written to the safety journal. Hestia would rather stop
producing hydrogen than produce it without a detector it can believe in.
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from hestia.domain.telemetry import Telemetry


class Level(IntEnum):
    OK = 0
    DEGRADED = 1
    UNTRUSTED = 2

    @property
    def label(self) -> str:
        return self.name.lower()


@dataclass(frozen=True)
class Finding:
    level: Level
    code: str
    message: str


@dataclass
class SensorHealth:
    name: str
    level: Level = Level.OK
    findings: list[Finding] = field(default_factory=list)
    value: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def score(self) -> int:
        penalty = sum(60 if f.level is Level.UNTRUSTED else 25 for f in self.findings)
        return max(0, 100 - penalty)

    def add(self, level: Level, code: str, message: str) -> None:
        self.findings.append(Finding(level, code, message))
        self.level = max(self.level, level)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "level": self.level.label,
            "score": self.score,
            "value": self.value,
            "findings": [
                {"level": f.level.label, "code": f.code, "message": f.message} for f in self.findings
            ],
            **self.extra,
        }


@dataclass(frozen=True)
class IntegrityReport:
    sensors: dict[str, SensorHealth]
    overall: Level
    h2_trusted: bool
    h2_why: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "overall": self.overall.label,
            "h2_trusted": self.h2_trusted,
            "h2_why": self.h2_why,
            "sensors": {k: v.as_dict() for k, v in self.sensors.items()},
        }


@dataclass(frozen=True)
class IntegrityConfig:
    expected_period_s: float = 2.0  # how often the controller reports
    stale_after_periods: float = 5.0
    h2_frozen_window_s: float = 600.0
    h2_frozen_min_span_ppm: float = 0.5
    h2_dead_window_s: float = 300.0
    h2_dead_below_ppm: float = 5.0
    h2_drift_window_s: float = 600.0
    h2_drift_degraded_ppm: float = 40.0
    h2_drift_untrusted_ppm: float = 120.0
    temp_heating_window_s: float = 900.0
    temp_heating_min_rise_c: float = 0.3
    temp_frozen_window_s: float = 600.0
    temp_frozen_min_span_c: float = 0.01
    temp_spike_c: float = 5.0
    electrolyte_frozen_window_s: float = 1800.0
    electrolyte_frozen_min_span: float = 0.01  # wt%
    min_samples: int = 20


GAS_SETTLE_S = 1800.0  # after a gas event, drift readings wait half an hour


class _Window:
    """A time window of (t, value) samples."""

    def __init__(self, span_s: float) -> None:
        self.span_s = span_s
        self.samples: deque[tuple[float, float]] = deque()

    def add(self, t: float, v: float) -> None:
        self.samples.append((t, v))
        while self.samples and t - self.samples[0][0] > self.span_s:
            self.samples.popleft()

    def covers(self, now: float, fraction: float = 0.9) -> bool:
        return bool(self.samples) and now - self.samples[0][0] >= self.span_s * fraction

    def values(self) -> list[float]:
        return [v for _, v in self.samples]

    def clear(self) -> None:
        self.samples.clear()


class IntegrityMonitor:
    def __init__(self, config: IntegrityConfig | None = None, *, h2_baseline_ppm: float = 55.0) -> None:
        self.cfg = c = config or IntegrityConfig()
        self.h2_baseline_ppm = h2_baseline_ppm  # updated by every passing calibration
        self._h2_frozen = _Window(c.h2_frozen_window_s)
        self._h2_dead = _Window(c.h2_dead_window_s)
        self._h2_drift = _Window(c.h2_drift_window_s)
        self._temp_heating = _Window(c.temp_heating_window_s)
        self._temp_running = _Window(c.temp_frozen_window_s)
        self._koh = _Window(c.electrolyte_frozen_window_s)
        self._last_temp: tuple[float, float] | None = None
        self._gas_seen_at = -1e12  # last time real gas was around (warning, alarm, extraction)
        self._last_seen: float | None = None
        self._last_seq: int | None = None
        self.gaps = 0
        self.reboots = 0
        self.received = 0
        self.maintenance_standing: tuple[str, str] = ("ok", "")
        self._latest: Telemetry | None = None

    # ── Inputs ───────────────────────────────────────────────────────────────

    def calibrated(self, baseline_ppm: float) -> None:
        """A calibration passed: new clean-air reference, fresh history."""
        self.h2_baseline_ppm = baseline_ppm
        for w in (self._h2_frozen, self._h2_dead, self._h2_drift):
            w.clear()

    def sensor_replaced(self) -> None:
        for w in (self._h2_frozen, self._h2_dead, self._h2_drift):
            w.clear()

    def set_maintenance(self, standing: str, why: str) -> None:
        self.maintenance_standing = (standing, why)

    def observe(self, t: Telemetry, now_s: float) -> list[str]:
        """Feed one telemetry message. Returns link events ('rebooted', 'gap')."""
        events: list[str] = []
        self.received += 1
        if self._last_seq is not None:
            if t.seq < self._last_seq:
                self.reboots += 1
                events.append("rebooted")
            elif t.seq > self._last_seq + 1:
                self.gaps += t.seq - self._last_seq - 1
                events.append("gap")
        self._last_seq = t.seq
        self._last_seen = now_s
        self._latest = t

        if t.h2_warning or t.h2_alarm_latched or t.ventilation:
            # Real gas is around: readings now and for the next half hour say
            # nothing about drift, and the ones just before may already
            # include the leak building up.
            self._gas_seen_at = now_s
            self._h2_drift.clear()
        if t.h2_valid:
            self._h2_frozen.add(now_s, t.h2_ppm)
            self._h2_dead.add(now_s, t.h2_ppm)
            # Drift is judged on clean-air readings only: a real leak is the
            # alarms' business, not evidence of sensor drift.
            if t.h2_ppm < 0.8 * t.h2_warning_ppm and now_s - self._gas_seen_at > GAS_SETTLE_S:
                self._h2_drift.add(now_s, t.h2_ppm)
        if t.temp_valid:
            if self._last_temp is not None and now_s - self._last_temp[0] <= 10 * self.cfg.expected_period_s:
                jump = abs(t.electrolyte_c - self._last_temp[1])
                if jump > self.cfg.temp_spike_c:
                    events.append("temp_spike")
            self._last_temp = (now_s, t.electrolyte_c)
            # A running stack heats its electrolyte unless the cooling loop is
            # on: only those stretches can prove that the probe follows it.
            if t.electrolyser and not t.cooling_pump:
                self._temp_heating.add(now_s, t.electrolyte_c)
            else:
                self._temp_heating.clear()
            if t.electrolyser:
                self._temp_running.add(now_s, t.electrolyte_c)
            else:
                self._temp_running.clear()
        if t.koh_valid:
            self._koh.add(now_s, t.koh_wt_pct)
        return events

    # ── Assessment ───────────────────────────────────────────────────────────

    def assess(self, now_s: float) -> IntegrityReport:
        c = self.cfg
        t = self._latest
        link = SensorHealth("link")
        h2 = SensorHealth("h2", extra={"baseline_ppm": round(self.h2_baseline_ppm, 1)})
        temp = SensorHealth("temperature")
        koh = SensorHealth("electrolyte")
        link.extra = {"received": self.received, "gaps": self.gaps, "reboots": self.reboots}

        if t is None or self._last_seen is None:
            link.add(Level.UNTRUSTED, "no_data", "No telemetry received from the controller yet")
            return self._report({"link": link, "h2": h2, "temperature": temp, "electrolyte": koh})

        age = now_s - self._last_seen
        link.value = round(age, 1)
        if age > c.stale_after_periods * c.expected_period_s:
            link.add(Level.UNTRUSTED, "stale", f"No telemetry for {age:.0f} s: controller unreachable")

        # ── H₂ sensor ────────────────────────────────────────────────────────
        h2.value = t.h2_ppm
        if not t.h2_valid:
            h2.add(Level.UNTRUSTED, "invalid", "The controller reports the H₂ sensor as unavailable")
        else:
            frozen = self._h2_frozen.values()
            if self._h2_frozen.covers(now_s) and len(frozen) >= c.min_samples:
                span = max(frozen) - min(frozen)
                if span < c.h2_frozen_min_span_ppm:
                    h2.add(
                        Level.UNTRUSTED,
                        "frozen",
                        f"Reading has not moved for {c.h2_frozen_window_s / 60:.0f} min "
                        f"(span {span:.2f} ppm): a working sensor always fluctuates",
                    )
            dead = self._h2_dead.values()
            if self._h2_dead.covers(now_s) and len(dead) >= c.min_samples:
                median = statistics.median(dead)
                if median < c.h2_dead_below_ppm:
                    h2.add(
                        Level.UNTRUSTED,
                        "dead",
                        f"Reading near zero ({median:.1f} ppm) where clean air reads "
                        f"~{self.h2_baseline_ppm:.0f} ppm: sensing element or heater failure",
                    )
            clean = self._h2_drift.values()
            if self._h2_drift.covers(now_s) and len(clean) >= c.min_samples:
                drift = statistics.median(clean) - self.h2_baseline_ppm
                h2.extra["drift_ppm"] = round(drift, 1)
                if abs(drift) >= c.h2_drift_untrusted_ppm:
                    h2.add(
                        Level.UNTRUSTED,
                        "drift",
                        f"Clean-air reading {drift:+.0f} ppm away from its calibration: "
                        "recalibrate before relying on it",
                    )
                elif abs(drift) >= c.h2_drift_degraded_ppm:
                    h2.add(
                        Level.DEGRADED,
                        "drift",
                        f"Clean-air reading drifting ({drift:+.0f} ppm since calibration): "
                        "schedule a calibration",
                    )
        standing, why = self.maintenance_standing
        if standing == "untrusted":
            h2.add(Level.UNTRUSTED, "maintenance", why[:1].upper() + why[1:])
        elif standing == "degraded":
            h2.add(Level.DEGRADED, "maintenance", why[:1].upper() + why[1:])

        # ── Electrolyte temperature probe ───────────────────────────────────
        temp.value = t.electrolyte_c if t.temp_valid else None
        if not t.temp_valid:
            temp.add(Level.UNTRUSTED, "invalid", "Temperature probe unplugged or out of range")
        else:
            heating = self._temp_heating.values()
            if self._temp_heating.covers(now_s) and len(heating) >= c.min_samples:
                rise = heating[-1] - heating[0]
                if rise < c.temp_heating_min_rise_c:
                    temp.add(
                        Level.DEGRADED,
                        "implausible",
                        f"Stack running for {c.temp_heating_window_s / 60:.0f} min without cooling but the "
                        f"electrolyte moved {rise:+.2f} °C: probe stuck or out of the liquid",
                    )
            running = self._temp_running.values()
            if (
                self._temp_running.covers(now_s)
                and len(running) >= c.min_samples
                and max(running) - min(running) < c.temp_frozen_min_span_c
            ):
                temp.add(
                    Level.DEGRADED,
                    "frozen",
                    f"Electrolyte reading has not moved for {c.temp_frozen_window_s / 60:.0f} min "
                    "while the stack runs: probe stuck",
                )

        # ── Electrolyte probe ───────────────────────────────────────────────
        koh.value = t.koh_wt_pct if t.koh_valid else None
        if not t.koh_valid:
            koh.add(Level.UNTRUSTED, "invalid", "Electrolyte probe unavailable: KOH strength unknown")
        else:
            values = self._koh.values()
            if (
                self._koh.covers(now_s)
                and len(values) >= c.min_samples
                and max(values) - min(values) < c.electrolyte_frozen_min_span
            ):
                koh.add(
                    Level.DEGRADED,
                    "frozen",
                    "Electrolyte reading frozen: transmitter fouled, blocked or disconnected",
                )

        return self._report({"link": link, "h2": h2, "temperature": temp, "electrolyte": koh})

    @staticmethod
    def _report(sensors: dict[str, SensorHealth]) -> IntegrityReport:
        overall = max(s.level for s in sensors.values())
        h2, link = sensors["h2"], sensors["link"]
        if link.level is Level.UNTRUSTED:
            return IntegrityReport(sensors, overall, False, link.findings[0].message)
        if h2.level is Level.UNTRUSTED:
            worst = next(f for f in h2.findings if f.level is Level.UNTRUSTED)
            return IntegrityReport(sensors, overall, False, worst.message)
        return IntegrityReport(sensors, overall, True, "")
