"""Operator commands: validation and state effects beyond the shared vectors."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from hestia.domain.commands import Command, CommandRejected, apply_command
from hestia.domain.control import ControllerState, Mode

COMMAND = TypeAdapter(Command)


def test_unknown_command_kind_is_rejected() -> None:
    with pytest.raises(ValidationError):
        COMMAND.validate_python({"kind": "open_valve"})


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_threshold_must_be_finite(value: float) -> None:
    with pytest.raises(ValidationError):
        COMMAND.validate_python({"kind": "set_threshold", "name": "koh_low_pct", "value": value})


def test_reset_without_alarm_is_rejected() -> None:
    with pytest.raises(CommandRejected):
        apply_command(
            COMMAND.validate_python({"kind": "reset_h2_alarm", "reason": "routine check done"}),
            ControllerState(),
        )


def test_leaving_manual_clears_every_manual_request() -> None:
    state = ControllerState(mode=Mode.MANUAL, manual_electrolyser=True, manual_cooling_pump=True)
    apply_command(COMMAND.validate_python({"kind": "set_mode", "mode": "AUTO"}), state)
    assert not (state.manual_electrolyser or state.manual_cooling_pump)


def test_safe_shutdown_is_always_allowed_even_with_alarm() -> None:
    state = ControllerState(h2_alarm_latched=True, mode=Mode.SAFE_SHUTDOWN)
    result = apply_command(COMMAND.validate_python({"kind": "set_mode", "mode": "SAFE_SHUTDOWN"}), state)
    assert "SAFE_SHUTDOWN" in result
