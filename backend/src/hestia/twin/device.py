"""
The virtual controller: an ESP32 that exists only in software.

It runs the exact safety controller the firmware runs (domain/control.py,
checked against spec/controller_vectors.json), applies commands with the same
validation, and drives a simulated installation: the bench (live view) or a
whole site (simulator). To the rest of the gateway it looks like a real
device: it produces the same Telemetry objects, so every layer above (energy
manager, trust layer, journal, dashboard) runs unchanged on simulated and
real hardware.
"""

from __future__ import annotations

from datetime import timedelta, tzinfo
from typing import Protocol

from hestia.domain.commands import Command, apply_command
from hestia.domain.control import ControllerState, Outputs, SensorReading, compute_outputs
from hestia.domain.energy import HvacCommand
from hestia.domain.telemetry import SiteContext, Telemetry

from .faults import FaultInjector, FaultName
from .plant import BenchPlant
from .weather import WeatherReplay

FIRMWARE_LABEL = "twin-2.1.0"
PERMIT_LEASE_S = 120.0  # the firmware's lease (HESTIA_PERMIT_VALID_S): an unrefreshed permit lapses


class Model(Protocol):
    kind: str
    plant: BenchPlant
    weather: WeatherReplay
    elapsed_s: float

    @property
    def tz(self) -> tzinfo: ...

    @property
    def storage_mawp_bar(self) -> float: ...
    def read(self, faults: FaultInjector, now_s: float) -> SensorReading: ...
    def advance(self, out: Outputs, hvac: HvacCommand, dt: float, faults: FaultInjector) -> SiteContext: ...


class VirtualDevice:
    def __init__(self, model: Model, *, device_id: str = "twin_1") -> None:
        self.model = model
        self.device_id = device_id
        self.state = ControllerState(storage_mawp_bar=model.storage_mawp_bar)
        self.faults = FaultInjector()
        # Two clocks. model.elapsed_s is the position in the weather year:
        # scenario jumps move it. uptime_s only ever moves forward: it is the
        # clock the gateway measures durations with (anti short-cycling,
        # sensor windows, fault onset, ventilation purge), so a jump to
        # "winter night" cannot break a timer.
        self.uptime_s = 0.0
        self.seq = 0
        self.last_outputs = Outputs()
        self.hvac = HvacCommand()
        self._permit = False
        self._permit_until = 0.0

    @property
    def plant(self) -> BenchPlant:
        return self.model.plant

    # ── What the gateway sends ───────────────────────────────────────────────

    def apply(self, command: Command) -> str:
        """Same validation as the firmware: raises CommandRejected if refused."""
        return apply_command(command, self.state)

    def set_permit(self, permit: bool, valid_for_s: float = PERMIT_LEASE_S) -> None:
        """A lease, like the firmware's: without a refresh it lapses and production stops."""
        self._permit = permit
        self._permit_until = self.uptime_s + valid_for_s
        self.state.production_permit = permit

    def set_hvac(self, hvac: HvacCommand) -> None:
        self.hvac = hvac

    def set_fault(self, name: FaultName, on: bool) -> None:
        self.faults.set(name, on, self.uptime_s)

    def jump_to(self, month: int, day: int, hour: float) -> None:
        """Move the weather position to a local date and hour ('winter night', 'summer noon'…)."""
        self.model.elapsed_s = self.model.weather.offset_of(month, day, hour, self.model.tz)

    # ── One control cycle ────────────────────────────────────────────────────

    def cycle(self, dt_s: float) -> tuple[Telemetry | None, SiteContext]:
        """Read sensors, run the controller, advance the physics by dt_s.

        Returns (telemetry, site). Telemetry is None during a simulated
        communication dropout: the plant keeps running, the gateway just
        stops hearing about it, exactly like a real Wi-Fi failure.
        """
        self.state.production_permit = self._permit and self.uptime_s < self._permit_until
        reading = self.model.read(self.faults, self.uptime_s)
        outputs = compute_outputs(reading, self.state)
        site = self.model.advance(outputs, self.hvac, dt_s, self.faults)
        self.last_outputs = outputs
        self.uptime_s += dt_s

        if self.faults.is_on("dropout"):
            return None, site

        self.seq += 1
        thr = self.state.thresholds
        telemetry = Telemetry(
            device_id=self.device_id,
            seq=self.seq,
            ts=site.timestamp + timedelta(seconds=0),
            uptime_s=round(self.uptime_s, 1),
            firmware=FIRMWARE_LABEL,
            electrolyte_c=reading.electrolyte_c,
            koh_wt_pct=reading.koh_wt_pct,
            level_low=reading.level_low,
            h2_ppm=reading.h2_ppm,
            tank_bar=reading.tank_bar,
            temp_valid=reading.temp_valid,
            koh_valid=reading.koh_valid,
            h2_valid=reading.h2_valid,
            tank_valid=reading.tank_valid,
            mode=self.state.mode,
            phase=outputs.phase,
            reason=outputs.reason[:200],
            electrolyser=outputs.electrolyser,
            cooling_pump=outputs.cooling_pump,
            koh_dosing=outputs.koh_dosing,
            water_makeup=outputs.water_makeup,
            ventilation=outputs.ventilation,
            h2_relay_closed=outputs.h2_relay_closed,
            h2_warning=self.state.h2_warning,
            h2_alarm_latched=self.state.h2_alarm_latched,
            production_permit=self.state.production_permit,
            koh_low_pct=thr.koh_low_pct,
            koh_high_pct=thr.koh_high_pct,
            temp_alert_c=thr.temp_alert_c,
            h2_warning_ppm=thr.h2_warning_ppm,
            h2_alarm_ppm=thr.h2_alarm_ppm,
            storage_mawp_bar=self.state.storage_mawp_bar,
        )
        return telemetry, site

    def bump_test(self, gas_ppm: float) -> float:
        """Expose the simulated H₂ sensor to test gas; returns its peak reading."""
        return round(self.plant.h2_reading(self.faults, self.uptime_s, gas_ppm * 0.95), 1)
