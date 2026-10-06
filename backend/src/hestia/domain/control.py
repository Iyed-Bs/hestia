"""
The safety controller: the rules that decide, every control cycle, what each
actuator does.

This module is the single specification of the controller. The ESP32
firmware (firmware/lib/hestia_core/src/hestia_controller.cpp) implements the
same rules, and both are checked against the same scenario file
(spec/controller_vectors.json) in CI, so "the digital twin behaves like the
hardware" is a tested fact rather than a promise.

Layering (see docs/ARCHITECTURE.md):

    ┌──────────────────────────────┐   production permit (yes / no)
    │ Energy manager (gateway)     │ ─────────────────────────────┐
    │ PV, tank level, ML advice    │                              │
    └──────────────────────────────┘                              ▼
    ┌──────────────────────────────────────────────────────────────────┐
    │ Safety controller (this file = firmware/lib/hestia_core)          │
    │ thermal cycle, electrolyte, gas detection, interlocks, manual     │
    └──────────────────────────────────────────────────────────────────┘

The controller never trusts the layer above it for safety: the permit can
only *stop* production, never force it, and every interlock below applies in
every mode, manual included.

The thermal cycle of the alkaline stack (30 wt% KOH, 15-70 °C):

    ELECTROLYSIS ──(electrolyte ≥ 60 °C)──▶ COOLING
    ELECTROLYSIS ──(KOH below its band)──▶ CONDITIONING
    COOLING ──(electrolyte ≤ 50 °C)──▶ CONDITIONING
    CONDITIONING ──(KOH strength in band and level OK)──▶ ELECTROLYSIS

While producing, the cooling loop holds the electrolyte between 50 and 55 °C
(pump on at 55, off at 50), as a real stack is run: at a steady temperature,
without stopping. The stack stops to cool only when the loop cannot keep up
(a heat wave, a failed pump) and the electrolyte reaches 60 °C; it restarts
at 50 °C. The PFA bench stopped at every 60 °C and waited for 40 °C: on a
40 °C summer day it could never have restarted.

Electrolyte. Electrolysis consumes water only, so the KOH solution gets
smaller and stronger as the stack works; topping up with pure water restores
both (physics/electrolyte.py). The controlled quantity is the KOH mass
fraction, not pH: at 30 wt% the pH is above 14, where glass probes saturate.

Hydrogen detection, in two stages (lower explosive limit = 40 000 ppm):

    stage 1 "warning"  > 500 ppm   stop producing, run the extraction fan,
                                   alert; clears by itself once the air has
                                   been clean for 5 minutes (purge)
    stage 2 "alarm"    > 2 000 ppm latch: every load off and the emergency
                                   relay open, but the extraction fan keeps
                                   running (it is the only thing that removes
                                   the gas); reset by an operator with a
                                   written reason

Both stages need three consecutive readings (one noisy reading never stops a
building). Practice for electrolyser rooms is the same layering: an early
ppm-level stage that starts ventilation, and a %LEL stage that shuts down.

Pressurised storage: production stops when the tank reaches 95 % of its
maximum allowable working pressure, or when its pressure cannot be read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Mode(StrEnum):
    """Operating mode chosen by an operator."""

    AUTO = "AUTO"  # the phase cycle below decides
    MANUAL = "MANUAL"  # an operator drives the actuators (interlocks still apply)
    SAFE_SHUTDOWN = "SAFE_SHUTDOWN"  # everything off until an operator says otherwise


class Phase(StrEnum):
    """Where the stack is in its thermal cycle."""

    ELECTROLYSIS = "ELECTROLYSIS"
    COOLING = "COOLING"
    CONDITIONING = "CONDITIONING"


# ── Fixed design limits ──────────────────────────────────────────────────────
# They come from the hardware and from gas-safety practice, not from
# preferences, so no command can move them. Stack: alkaline, 30 wt% KOH,
# 15-70 °C (LBE-series datasheet; Zeng & Zhang 2010).
ELECTROLYTE_MAX_C = 70.0  # absolute maximum of the stack
REGULATE_ON_C = 55.0  # cooling loop on while producing
REGULATE_OFF_C = 50.0  # cooling loop off while producing
COOL_AT_C = 60.0  # the loop could not hold: stop and cool, 10 °C below the maximum
RESUME_AT_C = 50.0  # cool enough to run again (reachable on the hottest days)
H2_LEL_PPM = 40_000.0  # lower explosive limit of hydrogen in air (4 % vol)
H2_DEBOUNCE_READINGS = 3  # consecutive readings above a level before it counts
VENTILATION_PURGE_S = 300.0  # extraction keeps running 5 min after the gas has gone
STORAGE_INTERLOCK_FRACTION = 0.95  # of the tank's maximum allowable working pressure


@dataclass(frozen=True)
class ThresholdLimits:
    """Allowed range for an operator-adjustable threshold."""

    minimum: float
    maximum: float

    def contains(self, value: float) -> bool:
        return self.minimum <= value <= self.maximum


# Operator-adjustable thresholds and the only ranges they may take. A request
# outside these ranges is refused (domain/commands.py), never clamped
# silently, so the journal always shows exactly what an operator asked for.
KOH_LOW_LIMITS = ThresholdLimits(20.0, 30.0)  # wt%
KOH_HIGH_LIMITS = ThresholdLimits(28.0, 35.0)  # wt%
KOH_MIN_BAND = 2.0  # the band must stay at least this wide
TEMP_ALERT_LIMITS = ThresholdLimits(COOL_AT_C + 2.0, ELECTROLYTE_MAX_C)  # stricter, never looser
H2_WARNING_LIMITS = ThresholdLimits(100.0, 1_000.0)  # ppm (0.25-2.5 % of the LEL)
H2_ALARM_LIMITS = ThresholdLimits(500.0, 4_000.0)  # ppm, at most 10 % of the LEL


@dataclass
class Thresholds:
    koh_low_pct: float = 25.0  # below: dose KOH concentrate before the next run
    koh_high_pct: float = 32.0  # above: add pure water
    temp_alert_c: float = ELECTROLYTE_MAX_C  # emergency stop at or above this
    h2_warning_ppm: float = 500.0  # stage 1
    h2_alarm_ppm: float = 2_000.0  # stage 2


@dataclass(frozen=True)
class SensorReading:
    """One instant of sensor data, as reported by the controller hardware.

    The *_valid flags are the device's own plausibility checks (sensor
    unplugged, value out of range, H₂ sensor still warming up). The deeper
    integrity checks (drift, frozen sensor, ...) run on the gateway in
    trust/integrity.py and act through the production permit.
    """

    electrolyte_c: float
    koh_wt_pct: float
    level_low: bool
    h2_ppm: float
    t_s: float = 0.0  # monotonic seconds (device uptime), for the ventilation purge
    tank_bar: float = 0.0  # storage pressure; ignored when there is no pressurised storage
    temp_valid: bool = True
    koh_valid: bool = True
    h2_valid: bool = True
    tank_valid: bool = True


@dataclass
class ControllerState:
    """Everything the controller remembers between two cycles."""

    mode: Mode = Mode.AUTO
    phase: Phase = Phase.ELECTROLYSIS
    # Granted by the energy manager. Starts False: the controller produces
    # nothing until the gateway has positively allowed it (fail-safe).
    production_permit: bool = False
    thresholds: Thresholds = field(default_factory=Thresholds)
    # Installation: maximum allowable working pressure of the storage tank,
    # 0 when the gas is not stored under pressure (the bench vents it).
    storage_mawp_bar: float = 0.0
    # Gas detection.
    h2_warning: bool = False
    h2_warning_count: int = 0
    h2_alarm_latched: bool = False
    h2_alarm_count: int = 0
    gas_seen_s: float = 0.0  # last time a warning-level concentration was read
    # Cooling loop while producing (hysteresis between REGULATE_OFF_C and REGULATE_ON_C).
    cooling_on: bool = False
    # Manual-mode requests (only read in MANUAL mode).
    manual_electrolyser: bool = False
    manual_cooling_pump: bool = False
    manual_koh_dosing: bool = False
    manual_water_makeup: bool = False
    manual_ventilation: bool = False


@dataclass(frozen=True)
class Outputs:
    """What the controller decided for each actuator, and why."""

    electrolyser: bool = False
    cooling_pump: bool = False
    koh_dosing: bool = False  # peristaltic pump, 45 wt% KOH concentrate
    water_makeup: bool = False  # deionised water from the reserve tank
    ventilation: bool = False  # extraction fan (ATEX-rated in a real installation)
    # True = the emergency relay is closed (normal). False = tripped: it cuts
    # the electrolyser power bus in hardware, independently of software.
    h2_relay_closed: bool = True
    phase: Phase = Phase.ELECTROLYSIS
    reason: str = ""


def update_gas_detection(reading: SensorReading, state: ControllerState) -> bool:
    """Run both detection stages. Returns True while the extraction fan must run."""
    thr = state.thresholds
    valid = reading.h2_valid

    # Stage 2: latch, and stay latched until a reasoned reset.
    if valid and reading.h2_ppm > thr.h2_alarm_ppm:
        state.h2_alarm_count += 1
        if state.h2_alarm_count >= H2_DEBOUNCE_READINGS and not state.h2_alarm_latched:
            state.h2_alarm_latched = True
            state.mode = Mode.SAFE_SHUTDOWN
    elif not state.h2_alarm_latched:
        state.h2_alarm_count = 0

    # Stage 1: on after three readings, off after a clean purge period.
    if valid and reading.h2_ppm > thr.h2_warning_ppm:
        state.h2_warning_count += 1
        if state.h2_warning_count >= H2_DEBOUNCE_READINGS:
            state.h2_warning = True
        if state.h2_warning:
            state.gas_seen_s = reading.t_s
    else:
        state.h2_warning_count = 0
        # An unreadable sensor never ends a warning: only clean, valid air does.
        if state.h2_warning and valid and reading.t_s - state.gas_seen_s >= VENTILATION_PURGE_S:
            state.h2_warning = False
    return state.h2_warning or state.h2_alarm_latched


def _advance_phase(reading: SensorReading, state: ControllerState) -> None:
    """Move through the thermal cycle (AUTO mode only)."""
    thr = state.thresholds
    if state.phase is Phase.ELECTROLYSIS and reading.electrolyte_c >= COOL_AT_C:
        state.phase = Phase.COOLING
    elif state.phase is Phase.ELECTROLYSIS and reading.koh_wt_pct < thr.koh_low_pct:
        # Weak electrolyte: stop, dose concentrate, then run again. Concentrate
        # is never dosed into a running stack.
        state.phase = Phase.CONDITIONING
    elif state.phase is Phase.COOLING and reading.electrolyte_c <= RESUME_AT_C:
        state.phase = Phase.CONDITIONING
    elif (
        state.phase is Phase.CONDITIONING
        and thr.koh_low_pct <= reading.koh_wt_pct <= thr.koh_high_pct
        and not reading.level_low
    ):
        state.phase = Phase.ELECTROLYSIS


def _interlocks(reading: SensorReading, state: ControllerState, overheated: bool) -> list[str]:
    """Reasons the stack may not run, whatever the mode."""
    blockers = []
    if overheated:
        blockers.append("electrolyte over-temperature")
    if reading.level_low:
        blockers.append("electrolyte level low")
    if not reading.h2_valid:
        # A leak that cannot be detected cannot be allowed to happen.
        blockers.append("H₂ sensor unavailable")
    if state.h2_warning:
        blockers.append("hydrogen detected in the room")
    if state.storage_mawp_bar > 0.0:
        if not reading.tank_valid:
            blockers.append("storage pressure unknown")
        elif reading.tank_bar >= STORAGE_INTERLOCK_FRACTION * state.storage_mawp_bar:
            blockers.append(f"storage at {reading.tank_bar:.1f} bar (limit {state.storage_mawp_bar:.0f} bar)")
    return blockers


def compute_outputs(reading: SensorReading, state: ControllerState) -> Outputs:
    """Decide the actuators for one control cycle. Mutates `state`.

    Rules are applied in priority order; the first one that applies wins.
    """
    thr = state.thresholds
    ventilate = update_gas_detection(reading, state)

    # 1. Stage-2 hydrogen alarm: everything off, relay tripped, extraction on.
    if state.h2_alarm_latched:
        return Outputs(
            ventilation=True,
            h2_relay_closed=False,
            phase=state.phase,
            reason=f"H₂ alarm latched ({reading.h2_ppm:.0f} ppm): power cut, extraction running; "
            "inspect, then reset",
        )

    # 2. Temperature or electrolyte unreadable: nothing can be run safely.
    if not reading.temp_valid or not reading.koh_valid:
        return Outputs(
            ventilation=ventilate,
            phase=state.phase,
            reason="Temperature or electrolyte sensor fault: cannot trust readings",
        )

    # 3. Operator shutdown.
    if state.mode is Mode.SAFE_SHUTDOWN:
        return Outputs(
            ventilation=ventilate, phase=state.phase, reason="Safe shutdown requested by an operator"
        )

    overheated = reading.electrolyte_c >= thr.temp_alert_c
    blockers = _interlocks(reading, state, overheated)

    # 4. Manual mode: the operator drives, but every interlock still applies.
    if state.mode is Mode.MANUAL:
        electrolyser = state.manual_electrolyser and not blockers
        reason = "Manual control"
        if state.manual_electrolyser and blockers:
            reason = "Manual control: electrolyser blocked (" + ", ".join(blockers) + ")"
        return Outputs(
            electrolyser=electrolyser,
            # Cooling can always be forced on in an overheat, never forced off.
            cooling_pump=state.manual_cooling_pump or overheated,
            koh_dosing=state.manual_koh_dosing and not overheated,
            water_makeup=state.manual_water_makeup and not overheated,
            # Ventilation can be switched on by hand, never off while gas is present.
            ventilation=state.manual_ventilation or ventilate,
            phase=state.phase,
            reason=reason,
        )

    # 5. AUTO: thermal cycle.
    _advance_phase(reading, state)
    # Topping up with water keeps both the level and the KOH strength.
    water = reading.level_low or reading.koh_wt_pct > thr.koh_high_pct
    # Concentrate is only dosed while the stack is stopped.
    koh = state.phase is not Phase.ELECTROLYSIS and reading.koh_wt_pct < thr.koh_low_pct

    if state.phase is Phase.ELECTROLYSIS:
        if reading.electrolyte_c >= REGULATE_ON_C:
            state.cooling_on = True
        elif reading.electrolyte_c <= REGULATE_OFF_C:
            state.cooling_on = False
        reasons = list(blockers)
        if not state.production_permit:
            reasons.append("no production permit from the energy manager")
        electrolyser = not reasons
        if electrolyser:
            reason = "Producing H₂, cooling loop on" if state.cooling_on else "Producing H₂"
        else:
            reason = "Electrolyser off: " + ", ".join(reasons)
        out = Outputs(
            electrolyser=electrolyser,
            cooling_pump=state.cooling_on,
            water_makeup=water,
            ventilation=ventilate,
            phase=state.phase,
            reason=reason,
        )
    elif state.phase is Phase.COOLING:
        state.cooling_on = False  # the loop restarts from its own hysteresis after the stop
        out = Outputs(
            cooling_pump=True,
            koh_dosing=koh,
            water_makeup=water,
            ventilation=ventilate,
            phase=state.phase,
            reason="Cooling the electrolyte",
        )
    else:  # CONDITIONING
        state.cooling_on = False
        out = Outputs(
            koh_dosing=koh,
            water_makeup=water,
            ventilation=ventilate,
            phase=state.phase,
            reason="Bringing the electrolyte back into its band before the next run",
        )

    # 6. Over-temperature override: stop production and dosing, but keep the
    #    cooling pump running (the prototype stopped it too, which removed the
    #    one thing that brings the temperature down).
    if overheated:
        return Outputs(
            cooling_pump=True,
            ventilation=ventilate,
            phase=state.phase,
            reason=f"Over-temperature ({reading.electrolyte_c:.1f} °C ≥ {thr.temp_alert_c:.0f} °C): "
            "electrolyser stopped, cooling forced on",
        )
    return out
