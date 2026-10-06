"""
The simulator's site: what a visitor can configure.

The live view shows the échantillon, the bench prototype, exactly as it runs.
The simulator is a whole building around a scaled-up version of it, set up
by the visitor: where it stands, how big it is, what equipment it has and
how hydrogen may be used. Each signed-in session gets a private one
(sim/manager.py), so experiments never touch the live installation or
someone else's run.

Equipment that grows with the stack grows with it here: the electrolyte
volume (20 L per kW, as on the bench), the cooling loop (sized to hold the
stack at 55 °C with 45 °C outside) and the stack's own heat capacity.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hestia.physics.building import Building
from hestia.physics.conversion import FuelCell, H2Boiler, HeatPump
from hestia.physics.electrolyser import AlkalineStack
from hestia.physics.solar import PvArray
from hestia.physics.storage import Tank
from hestia.twin.params import BenchProfile, SiteProfile

LOCATIONS: dict[str, tuple[str, float, float, str]] = {
    "tunis": ("Tunis", 36.8, 10.2, "Africa/Tunis"),
    "paris": ("Paris", 48.85, 2.35, "Europe/Paris"),
}

# What energy is worth in each place (same sources as planner/presets.py):
# grid carbon intensity (kg CO₂/kWh), price bought and sold (€/kWh), and the
# share of production that may be sold (Tunisian self-producers: 30 %).
GRID: dict[str, tuple[float, float, float, float]] = {
    "tunis": (0.468, 0.088, 0.035, 0.30),
    "paris": (0.0213, 0.25, 0.04, 1.0),
}


class SimConfig(BaseModel):
    """A site for the simulator. Defaults: the demo building in Tunis."""

    model_config = ConfigDict(extra="forbid")

    location: Literal["tunis", "paris"] = "tunis"

    # Building.
    floor_area_m2: float = Field(120, ge=40, le=1000)
    insulation: Literal["poor", "average", "good"] = "average"
    heat_setpoint_c: float = Field(20, ge=16, le=24)
    cool_setpoint_c: float = Field(26, ge=22, le=30)
    heat_pump_kw: float = Field(4.0, ge=1, le=30)  # heating and cooling capacity

    # Electricity.
    pv_kwp: float = Field(8.0, ge=0, le=50)
    pv_tilt_deg: float = Field(30, ge=0, le=90)
    pv_azimuth_deg: float = Field(0, ge=-180, le=180)
    battery_kwh: float = Field(10.0, ge=0, le=100)
    battery_kw: float = Field(3.0, ge=0.5, le=30)
    grid_connected: bool = True
    export_limit_kw: float = Field(3.0, ge=0, le=50)

    # Hydrogen.
    stack_kw: float = Field(2.0, ge=0.5, le=10)
    tank_kg: float = Field(10.0, ge=0.5, le=50)
    # A pressurised alkaline stack delivers up to about 30 bar without a
    # compressor; higher pressures would need one, which is not modelled.
    tank_mawp_bar: float = Field(30, ge=10, le=30)
    end_use: Literal["fuel_cell", "boiler"] = "fuel_cell"
    fuel_cell_kw: float = Field(1.5, ge=0.5, le=10)
    boiler_kw: float = Field(3.0, ge=1, le=30)
    strategy: Literal["value", "battery_first", "hydrogen_first"] = "value"
    h2_use: Literal["auto", "grid_backup", "heating_only", "never"] = "auto"
    h2_reserve_pct: float = Field(5.0, ge=0, le=50)

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if self.cool_setpoint_c < self.heat_setpoint_c + 2.0:
            raise ValueError("the cooling setpoint must be at least 2 °C above the heating setpoint")
        return self

    @property
    def place(self) -> tuple[str, float, float, str]:
        """(name, latitude, longitude, time zone)."""
        return LOCATIONS[self.location]

    def profile(self) -> SiteProfile:
        name, lat, lon, tz = self.place
        kw = self.stack_kw
        return SiteProfile(
            name=f"Simulated building, {name}",
            latitude=lat,
            longitude=lon,
            timezone=tz,
            bench=BenchProfile(
                stack=AlkalineStack(rated_w=1000.0 * kw),
                electrolyte_l=20.0 * kw,
                stack_heat_capacity_j_per_k=12_000.0 * kw,
                cooler_ua_w_per_k=60.0 * kw,
                passive_ua_w_per_k=4.0 * kw,
                supply_w=0.0,  # powered by the site's surplus, not a lab supply
            ),
            pv=PvArray(kwp=self.pv_kwp, tilt_deg=self.pv_tilt_deg, azimuth_deg=self.pv_azimuth_deg),
            battery_kwh=self.battery_kwh,
            battery_power_w=1000.0 * self.battery_kw,
            tank=Tank.for_capacity(self.tank_kg, mawp_bar=self.tank_mawp_bar),
            building=Building(
                floor_area_m2=self.floor_area_m2,
                insulation=self.insulation,
                heat_setpoint_c=self.heat_setpoint_c,
                cool_setpoint_c=self.cool_setpoint_c,
            ),
            end_use=self.end_use,
            fuel_cell=FuelCell(rated_w=1000.0 * self.fuel_cell_kw),
            boiler=H2Boiler(rated_w=1000.0 * self.boiler_kw),
            heat_pump=HeatPump(rated_heat_w=1000.0 * self.heat_pump_kw),
            grid_connected=self.grid_connected,
            export_limit_w=1000.0 * self.export_limit_kw,
            grid_kgco2_per_kwh=GRID[self.location][0],
            import_price=GRID[self.location][1],
            export_price=GRID[self.location][2],
            export_cap_share=GRID[self.location][3],
            strategy=self.strategy,
            h2_use=self.h2_use,
            h2_reserve_pct=self.h2_reserve_pct,
        )
