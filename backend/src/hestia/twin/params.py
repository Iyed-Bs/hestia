"""
Equipment profiles for the twin and the simulator.

BenchProfile is the échantillon: the bench prototype as built in the PFA,
an alkaline stack with its 30 wt% KOH loop, a radiator and fan, a reserve
water tank, a KOH dosing pump, an MQ-8 class hydrogen sensor, in a small
technical room. The live view replays it.

SiteProfile is a whole building around a scaled-up stack: PV, battery,
pressurised hydrogen storage, a fuel cell or a hydrogen boiler, a heat pump
that heats and cools, and the grid. The simulator builds one from the
visitor's own choices (sim/config.py).

Every rate is derived from these physical values in SI units, so changing a
profile changes the physics consistently; there are no per-step constants.
The models themselves live in hestia/physics, with their sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from hestia.domain.merit import Destination, SiteValues
from hestia.physics.building import Building
from hestia.physics.conversion import FuelCell, H2Boiler, HeatPump
from hestia.physics.electrolyser import H2_LHV_KWH_PER_KG as H2_LHV_KWH_PER_KG
from hestia.physics.electrolyser import AlkalineStack
from hestia.physics.solar import PvArray
from hestia.physics.storage import Tank

J_PER_KWH = 3.6e6

EndUse = Literal["fuel_cell", "boiler"]
# value: each kWh of surplus goes where it does the most good (domain/merit.py);
# battery_first / hydrogen_first: fixed orders, to compare against.
Strategy = Literal["value", "battery_first", "hydrogen_first"]
# auto: use hydrogen when its heat is useful (heating) or when the tank is
# nearly full (making room for more); always in a grid outage.
H2Use = Literal["auto", "grid_backup", "heating_only", "never"]


@dataclass(frozen=True)
class BenchProfile:
    """The stack, its electrolyte loop, the room around it and its sensors."""

    stack: AlkalineStack = field(default_factory=lambda: AlkalineStack(rated_w=1000.0))
    electrolyte_l: float = 20.0
    electrolyte_wt_pct: float = 30.0
    stack_heat_capacity_j_per_k: float = 12_000.0  # end plates, cells, pipework (~25 kg of steel)
    cooler_ua_w_per_k: float = 60.0  # radiator + 150 mm fan, cooling pump running
    passive_ua_w_per_k: float = 4.0  # natural losses of the loop
    level_low_fraction: float = 0.95  # float switch: 5 % of the solution gone
    makeup_l_per_min: float = 1.0  # reserve pump, deionised water
    koh_dose_l_per_min: float = 0.05  # peristaltic pump, 45 wt% concentrate
    koh_carry_over_g_per_kg_h2: float = 0.5  # KOH mist leaving with the gases
    room_volume_m3: float = 30.0
    natural_ach: float = 0.5  # air changes per hour, fan off
    extraction_ach: float = 10.0  # air changes per hour, extraction fan on
    h2_baseline_ppm: float = 55.0  # MQ-8 clean-air reading after calibration
    h2_noise_ppm: float = 6.0
    supply_w: float = 1000.0  # the bench's lab power supply
    lab_temperature_c: float = 24.0


@dataclass(frozen=True)
class SiteProfile:
    """A building with the whole hydrogen chain."""

    name: str = "Demo building, Tunis"
    latitude: float = 36.8
    longitude: float = 10.2
    timezone: str = "Africa/Tunis"
    # The bench scaled to 2 kW: electrolyte, cooling loop and thermal mass
    # grow with the stack (sim/config.py explains the sizing).
    bench: BenchProfile = field(
        default_factory=lambda: BenchProfile(
            stack=AlkalineStack(rated_w=2000.0),
            electrolyte_l=40.0,
            stack_heat_capacity_j_per_k=24_000.0,
            cooler_ua_w_per_k=120.0,
            passive_ua_w_per_k=8.0,
        )
    )
    pv: PvArray = field(
        default_factory=lambda: PvArray(kwp=8.0)
    )  # a roof that makes more than the house uses
    battery_kwh: float = 10.0
    battery_power_w: float = 3000.0
    battery_round_trip: float = 0.92
    battery_reserve: float = 0.20  # never drained below this share
    # 10 kg (4.8 m³ at 30 bar): enough to carry a summer's surplus into the
    # winter; a 4 kg tank fills in two weeks of summer and then sits full.
    tank: Tank = field(default_factory=lambda: Tank.for_capacity(10.0, mawp_bar=30.0))
    building: Building = field(default_factory=Building)
    end_use: EndUse = "fuel_cell"
    fuel_cell: FuelCell = field(default_factory=FuelCell)
    boiler: H2Boiler = field(default_factory=H2Boiler)
    heat_pump: HeatPump = field(default_factory=HeatPump)
    grid_connected: bool = True
    export_limit_w: float = 3000.0  # 0 = no export allowed
    # Share of the PV production that may be sold, as a running budget:
    # Tunisian self-producers may sell up to 30 % of what they produce.
    export_cap_share: float = 0.30
    strategy: Strategy = "value"
    h2_use: H2Use = "auto"
    h2_reserve_pct: float = 5.0  # kept for emergencies
    # What energy is worth here (merit order): grid carbon, tariffs, carbon price.
    grid_kgco2_per_kwh: float = 0.468  # Tunisia 2024
    import_price: float = 0.088  # €/kWh
    export_price: float = 0.035  # €/kWh
    carbon_price_eur_per_t: float = 300.0  # UBA 2024

    def values(self) -> SiteValues:
        return SiteValues(
            grid_kgco2_per_kwh=self.grid_kgco2_per_kwh,
            import_price=self.import_price,
            export_price=self.export_price,
            export_allowed=self.grid_connected and self.export_limit_w > 0 and self.export_cap_share > 0,
            carbon_price_eur_per_t=self.carbon_price_eur_per_t,
            heating="heat_pump",
            heating_cop=self.heat_pump.cop_heating(7.0),
            battery_round_trip=self.battery_round_trip,
            end_use=self.end_use,
            fuel_cell_electrical=self.fuel_cell.electrical_efficiency,
            fuel_cell_thermal=self.fuel_cell.thermal_efficiency,
            h2_boiler_efficiency=self.boiler.efficiency,
        )

    def surplus_order(self) -> list[Destination]:
        """Where surplus goes, best first (curtailment always last)."""
        if self.strategy == "battery_first":
            return ["battery", "hydrogen", "export"]
        if self.strategy == "hydrogen_first":
            return ["hydrogen", "battery", "export"]
        return self.values().order()
