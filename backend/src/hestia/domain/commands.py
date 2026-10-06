"""
Operator commands: a closed, typed list of what anyone may ask the controller
to do.

The original prototype accepted free text ("temp_alert=200") from anyone on a
public MQTT broker, which meant a single message could raise the overheat
limit far beyond what the electrolyser survives. Here every command is a
validated object:

- unknown commands are rejected, never ignored silently;
- numeric values must sit inside the design limits in domain/control.py
  (out-of-range requests are rejected, not clamped, so the safety journal
  records exactly what was asked), and related thresholds must stay coherent
  (the KOH band cannot invert, stage 1 stays below stage 2);
- resetting the H₂ alarm requires a written reason, which goes into the
  tamper-evident journal (trust/journal.py);
- who may send which command is decided by roles in security/auth.py.

The same objects travel to the ESP32, signed (runtime/envelope.py), and the
firmware applies the same validation again: the device never assumes the
gateway was right.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

from .control import (
    H2_ALARM_LIMITS,
    H2_WARNING_LIMITS,
    KOH_HIGH_LIMITS,
    KOH_LOW_LIMITS,
    KOH_MIN_BAND,
    TEMP_ALERT_LIMITS,
    ControllerState,
    Mode,
    ThresholdLimits,
)

Actuator = Literal["electrolyser", "cooling_pump", "koh_dosing", "water_makeup", "ventilation"]
ThresholdName = Literal["koh_low_pct", "koh_high_pct", "temp_alert_c", "h2_warning_ppm", "h2_alarm_ppm"]


class SetMode(BaseModel):
    """Switch between AUTO, MANUAL and SAFE_SHUTDOWN."""

    kind: Literal["set_mode"] = "set_mode"
    mode: Mode


class ResetH2Alarm(BaseModel):
    """Clear a latched H₂ alarm after inspecting the installation."""

    kind: Literal["reset_h2_alarm"] = "reset_h2_alarm"
    # What was checked and found. Required: an alarm reset without a reason is
    # exactly what an inspector or insurer asks about first.
    reason: str = Field(min_length=10, max_length=500)


class SetActuator(BaseModel):
    """Drive one actuator by hand (MANUAL mode only)."""

    kind: Literal["set_actuator"] = "set_actuator"
    actuator: Actuator
    on: bool


class SetThreshold(BaseModel):
    """Adjust an operator threshold, inside its design limits."""

    kind: Literal["set_threshold"] = "set_threshold"
    name: ThresholdName
    value: float

    @field_validator("value")
    @classmethod
    def _finite(cls, value: float) -> float:
        if value != value or value in (float("inf"), float("-inf")):  # NaN or infinite
            raise ValueError("value must be a finite number")
        return value


Command = Annotated[
    SetMode | ResetH2Alarm | SetActuator | SetThreshold,
    Field(discriminator="kind"),
]

THRESHOLD_LIMITS: dict[str, ThresholdLimits] = {
    "koh_low_pct": KOH_LOW_LIMITS,
    "koh_high_pct": KOH_HIGH_LIMITS,
    "temp_alert_c": TEMP_ALERT_LIMITS,
    "h2_warning_ppm": H2_WARNING_LIMITS,
    "h2_alarm_ppm": H2_ALARM_LIMITS,
}

_MANUAL_FIELDS = {
    "electrolyser": "manual_electrolyser",
    "cooling_pump": "manual_cooling_pump",
    "koh_dosing": "manual_koh_dosing",
    "water_makeup": "manual_water_makeup",
    "ventilation": "manual_ventilation",
}


class CommandRejected(ValueError):
    """The command is well-formed but not allowed in the current state."""


def _check_coherence(name: str, value: float, state: ControllerState) -> None:
    thr = state.thresholds
    if name == "koh_low_pct" and value > thr.koh_high_pct - KOH_MIN_BAND:
        raise CommandRejected(
            f"koh_low_pct must stay at least {KOH_MIN_BAND:g} below koh_high_pct ({thr.koh_high_pct:g})"
        )
    if name == "koh_high_pct" and value < thr.koh_low_pct + KOH_MIN_BAND:
        raise CommandRejected(
            f"koh_high_pct must stay at least {KOH_MIN_BAND:g} above koh_low_pct ({thr.koh_low_pct:g})"
        )
    if name == "h2_warning_ppm" and value >= thr.h2_alarm_ppm:
        raise CommandRejected(f"h2_warning_ppm must stay below h2_alarm_ppm ({thr.h2_alarm_ppm:g})")
    if name == "h2_alarm_ppm" and value <= thr.h2_warning_ppm:
        raise CommandRejected(f"h2_alarm_ppm must stay above h2_warning_ppm ({thr.h2_warning_ppm:g})")


def apply_command(
    command: SetMode | ResetH2Alarm | SetActuator | SetThreshold, state: ControllerState
) -> str:
    """Apply a validated command to the controller state.

    Returns a short human-readable result; raises CommandRejected when the
    command makes no sense right now (for example a manual actuator request
    outside MANUAL mode).
    """
    match command:
        case SetMode(mode=mode):
            if state.h2_alarm_latched and mode is not Mode.SAFE_SHUTDOWN:
                raise CommandRejected("H₂ alarm latched: reset it before changing mode")
            state.mode = mode
            if mode is not Mode.MANUAL:
                # Leaving manual mode never leaves an actuator latched on.
                for attr in _MANUAL_FIELDS.values():
                    setattr(state, attr, False)
            return f"Mode set to {mode.value}"

        case ResetH2Alarm():
            if not state.h2_alarm_latched:
                raise CommandRejected("No H₂ alarm is latched")
            state.h2_alarm_latched = False
            state.h2_alarm_count = 0
            # Back to a safe, explicit state: the operator chooses AUTO again.
            # The stage-1 warning, if gas is still around, keeps ventilating.
            state.mode = Mode.SAFE_SHUTDOWN
            return "H₂ alarm reset; system held in SAFE_SHUTDOWN until an operator selects a mode"

        case SetActuator(actuator=actuator, on=on):
            if state.mode is not Mode.MANUAL:
                raise CommandRejected("Actuators can only be driven by hand in MANUAL mode")
            setattr(state, _MANUAL_FIELDS[actuator], on)
            return f"{actuator} {'ON' if on else 'OFF'} (manual)"

        case SetThreshold(name=name, value=value):
            limits = THRESHOLD_LIMITS[name]
            if not limits.contains(value):
                raise CommandRejected(f"{name} must be between {limits.minimum:g} and {limits.maximum:g}")
            _check_coherence(name, value, state)
            setattr(state.thresholds, name, value)
            return f"{name} set to {value:g}"
