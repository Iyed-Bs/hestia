"""
Faults that can be switched on in the digital twin and the simulator.

They exist to show (and test) what the controller and the trust layer catch.
Each one is a failure that happens to real installations:

- leak_small      : 2 L/min of hydrogen escaping into the room (a loose fitting)
- leak_large      : 20 L/min (a cracked line); the room concentration follows
                    a mass balance with the ventilation, so a small leak with
                    the extraction fan running settles below the alarm, a
                    large one does not
- h2_stuck        : the H₂ reading freezes (failed ADC, broken wire held by noise)
- h2_dead         : the sensing element fails and reads ~0 while looking valid
- h2_drift        : the clean-air baseline creeps up as the SnO₂ layer ages
- h2_poisoned     : sensitivity collapses (silicone or sulphur poisoning);
                    clean-air readings look perfect but a leak barely shows,
                    which only a bump test with calibration gas can reveal
- temp_disconnect : the electrolyte temperature probe is unplugged
- temp_stuck      : the temperature probe freezes
- koh_probe_fail  : the electrolyte (conductivity) probe gives no valid reading
- cooling_failure : the cooling pump runs but nothing flows (air lock, seized
                    impeller): the stack heats up until the over-temperature
                    stop
- fan_failure     : the extraction fan is commanded but does not turn
- tank_sensor_fail: the storage pressure transmitter fails (production stops)
- grid_outage     : the building is cut off from the grid (simulator only)
- dropout         : the controller stops reporting (Wi-Fi, power, crash)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

FaultName = Literal[
    "leak_small",
    "leak_large",
    "h2_stuck",
    "h2_dead",
    "h2_drift",
    "h2_poisoned",
    "temp_disconnect",
    "temp_stuck",
    "koh_probe_fail",
    "cooling_failure",
    "fan_failure",
    "tank_sensor_fail",
    "grid_outage",
    "dropout",
]
ALL_FAULTS: tuple[FaultName, ...] = (
    "leak_small",
    "leak_large",
    "h2_stuck",
    "h2_dead",
    "h2_drift",
    "h2_poisoned",
    "temp_disconnect",
    "temp_stuck",
    "koh_probe_fail",
    "cooling_failure",
    "fan_failure",
    "tank_sensor_fail",
    "grid_outage",
    "dropout",
)
BENCH_FAULTS: tuple[FaultName, ...] = tuple(
    f for f in ALL_FAULTS if f not in ("tank_sensor_fail", "grid_outage")
)

LEAK_L_PER_MIN = {"leak_small": 2.0, "leak_large": 20.0}
DRIFT_PPM_PER_HOUR = 40.0  # baseline creep while h2_drift is active (fast, for demos)
POISONED_SENSITIVITY = 0.08  # the sensor sees 8 % of the real concentration


@dataclass
class FaultInjector:
    """Which faults are active, and since when (device seconds)."""

    active: dict[str, float] = field(default_factory=dict)

    def set(self, name: FaultName, on: bool, now_s: float) -> None:
        if on:
            self.active.setdefault(name, now_s)
        else:
            self.active.pop(name, None)

    def is_on(self, name: FaultName) -> bool:
        return name in self.active

    def leak_m3_per_s(self) -> float:
        litres = sum(rate for name, rate in LEAK_L_PER_MIN.items() if name in self.active)
        return litres / 1000.0 / 60.0

    def drift_ppm(self, now_s: float) -> float:
        start = self.active.get("h2_drift")
        return 0.0 if start is None else DRIFT_PPM_PER_HOUR * (now_s - start) / 3600.0

    def clear(self) -> None:
        self.active.clear()
