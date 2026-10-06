"""Run the shared controller specification (spec/controller_vectors.json).

The firmware runs the same file (firmware/test/test_core), so a change in
behaviour on either side fails CI until both agree again.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from hestia.domain.commands import Command, CommandRejected, apply_command
from hestia.domain.control import ControllerState, Mode, Phase, SensorReading, compute_outputs

SPEC = json.loads(
    (Path(__file__).resolve().parents[2] / "spec" / "controller_vectors.json").read_text(encoding="utf-8")
)
COMMAND = TypeAdapter(Command)


def _initial_state(overrides: dict[str, Any]) -> ControllerState:
    merged = {**SPEC["defaults"]["initial"], **overrides}
    return ControllerState(
        mode=Mode(merged["mode"]),
        phase=Phase(merged["phase"]),
        production_permit=merged["production_permit"],
        storage_mawp_bar=float(merged["storage_mawp_bar"]),
    )


def test_the_spec_is_version_2() -> None:
    assert SPEC["version"] == 2


@pytest.mark.parametrize("scenario", SPEC["scenarios"], ids=lambda s: s["name"])
def test_scenario(scenario: dict[str, Any]) -> None:
    state = _initial_state(scenario.get("initial", {}))
    clock = 0.0
    for index, step in enumerate(scenario["steps"]):
        where = f"{scenario['name']!r}, step {index + 1}"
        if "command" in step:
            try:
                apply_command(COMMAND.validate_python(step["command"]), state)
                accepted = True
            except (ValidationError, CommandRejected):
                accepted = False
            assert accepted == step["accepted"], f"{where}: command acceptance"
            continue

        clock += float(step.get("dt", SPEC["defaults"]["dt"]))
        reading = SensorReading(**{**SPEC["defaults"]["reading"], **step["reading"], "t_s": clock})
        out = compute_outputs(reading, state)
        observed = {
            "electrolyser": out.electrolyser,
            "cooling_pump": out.cooling_pump,
            "koh_dosing": out.koh_dosing,
            "water_makeup": out.water_makeup,
            "ventilation": out.ventilation,
            "h2_relay_closed": out.h2_relay_closed,
            "phase": out.phase.value,
            "mode": state.mode.value,
            "h2_warning": state.h2_warning,
            "h2_alarm_latched": state.h2_alarm_latched,
        }
        for key, expected in step.get("expect", {}).items():
            assert observed[key] == expected, f"{where}: {key} (reason: {out.reason})"
