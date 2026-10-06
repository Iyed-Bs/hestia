"""
What a controller reports, and what the site around it measures.

Telemetry is the message the ESP32 publishes every control cycle (and the
virtual device in the twin produces the same object). It is validated with
strict bounds when it arrives over MQTT: a malformed or absurd message is
rejected and counted, never fed to the control or trust layers.

SiteContext is everything outside the controller: solar production, the
building's needs, batteries, hydrogen storage, the grid. In the simulator it
is computed by the site model; on a real site without metering it is
estimated from live weather and the PV model, and marked as such. Fields a
site does not have (a bench has no building) are None.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .control import Mode, Phase


class Telemetry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    device_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,32}$")
    seq: int = Field(ge=0)
    ts: datetime
    uptime_s: float = Field(ge=0)
    firmware: str = Field(max_length=32)

    # Sensors (as the device read them)
    electrolyte_c: float = Field(ge=-200, le=200)
    koh_wt_pct: float = Field(ge=0, le=60)
    level_low: bool
    h2_ppm: float = Field(ge=0, le=100_000)
    tank_bar: float = Field(ge=0, le=1_000)
    temp_valid: bool
    koh_valid: bool
    h2_valid: bool
    tank_valid: bool

    # Controller state and decisions
    mode: Mode
    phase: Phase
    reason: str = Field(max_length=200)
    electrolyser: bool
    cooling_pump: bool
    koh_dosing: bool
    water_makeup: bool
    ventilation: bool
    h2_relay_closed: bool
    h2_warning: bool
    h2_alarm_latched: bool
    production_permit: bool

    # Thresholds in force, and the storage the controller protects
    koh_low_pct: float
    koh_high_pct: float
    temp_alert_c: float
    h2_warning_ppm: float
    h2_alarm_ppm: float
    storage_mawp_bar: float = Field(ge=0, le=1_000)


HvacMode = Literal["off", "heat", "cool"]


class SiteContext(BaseModel):
    """Energy, building and storage values around the controller."""

    model_config = ConfigDict(frozen=True)

    source: Literal["twin", "simulation", "estimated", "metered"]
    timestamp: datetime  # UTC
    local_time: str = ""  # "2025-07-10 12:05", in the site's own time zone

    # Weather
    outdoor_c: float
    humidity_pct: float
    irradiance_wpm2: float  # global horizontal

    # Electricity (W). battery_w > 0 charges; grid_w > 0 imports, < 0 exports.
    pv_w: float
    electrolyser_w: float
    available_w: float  # power the stack could use right now (surplus or bench supply)
    load_w: float | None = None  # the building's appliances and lighting
    heat_pump_w: float | None = None
    fuel_cell_w: float | None = None
    battery_w: float | None = None
    battery_soc_pct: float | None = None
    grid_w: float | None = None
    curtailed_w: float | None = None

    # Hydrogen storage
    h2_tank_kg: float | None = None
    h2_tank_pct: float | None = None
    tank_bar: float | None = None
    tank_mawp_bar: float | None = None
    h2_vented_kg: float | None = None  # lost through the relief valve since the start

    # Building
    indoor_c: float | None = None
    hvac_mode: HvacMode | None = None
    hvac_w: float | None = None  # thermal: + heating, - cooling
    fuel_cell_heat_w: float | None = None
    boiler_w: float | None = None
    end_use: Literal["fuel_cell", "boiler"] | None = None

    # Room around the stack (the twin knows the true concentration; a real
    # site only knows what its sensor says)
    room_h2_ppm: float | None = None

    # Where the power went this step, and why (one sentence, simulator only)
    dispatch: str = ""
