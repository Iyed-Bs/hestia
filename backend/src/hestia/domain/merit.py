"""
Where should a kWh of surplus sunshine go? The merit order.

When the panels make more than the building uses, each kWh can go to the
battery, to the electrolyser (hydrogen), to the grid, or be curtailed. Our
priorities are nature first, then cost. So each destination is scored by
what it is worth per kWh of surplus:

    value = CO₂ avoided (kg) × carbon price + money saved or earned (€)

The carbon price makes "nature first" concrete. The default is 300 € per
tonne, the climate cost the German Environment Agency recommends for 2024
(UBA, Methodenkonvention 3.2, 1 % pure time preference; 880 €/t with none).
That is several times the price of an emission allowance, so nature decides
whenever the CO₂ difference is real, and cost decides when it is not.

What each destination does with a kWh of surplus:

- battery     gives back η (92 %) tonight, replacing a kWh the building
              would have bought from the grid. It is daily storage: refilled
              by tomorrow's sun.
- grid export replaces a kWh from the grid's power plants (its average
              carbon intensity), and earns the feed-in price. Not wasted.
- hydrogen    keeps 55 % in the gas (stack at rated load), returned months
              later: by the fuel cell as 45 % electricity + 40 % heat, or by
              the boiler as 90 % heat. Its heat replaces what the building's
              heating would have made (heat pump electricity, or gas).
- curtailment is worth nothing: the only true waste.

The result depends on the place. In Tunis (gas-fired grid, heat pump,
cheap power) the grid takes surplus before hydrogen; in Paris (clean grid,
dear power) hydrogen comes first. The battery leads in both, because a kWh
stored for tonight loses only 8 %. Hydrogen is never the first choice for a
kWh the battery or the grid can use: it is the seasonal store, and the place
for surplus the grid cannot take (export caps, outages, no connection).
This is also how the one residential hydrogen product on the market works
(HPS picea: a battery for the day, hydrogen for the season).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from hestia.physics.electrolyser import H2_LHV_KWH_PER_KG, AlkalineStack

Destination = Literal["battery", "hydrogen", "export"]
DESTINATIONS: tuple[Destination, ...] = ("battery", "hydrogen", "export")
GAS_KGCO2_PER_KWH = 0.202  # natural gas burned (IPCC 2006 default)
STACK_TEMP_C = 52.0  # the cooling loop holds 50-55 °C while producing


def _stack_to_h2(rated_w: float = 1000.0) -> float:
    """Share of a kWh that ends up in the gas (LHV) at the stack's rated load."""
    stack = AlkalineStack(rated_w=rated_w)
    point = stack.operate(rated_w, STACK_TEMP_C)
    return point.h2_kg_per_s * 3600.0 * H2_LHV_KWH_PER_KG / (rated_w / 1000.0)


@dataclass(frozen=True)
class SiteValues:
    """What the site's energy is worth: carbon and money, per kWh."""

    grid_kgco2_per_kwh: float = 0.468  # Tunisia 2024
    import_price: float = 0.088  # €/kWh bought
    export_price: float = 0.035  # €/kWh sold
    export_allowed: bool = True
    carbon_price_eur_per_t: float = 300.0  # UBA 2024
    heating: Literal["heat_pump", "gas_boiler"] = "heat_pump"
    heating_cop: float = 3.5  # seasonal, for valuing heat
    gas_price: float = 0.026
    gas_boiler_efficiency: float = 0.90
    battery_round_trip: float = 0.92
    end_use: Literal["fuel_cell", "boiler"] = "fuel_cell"
    fuel_cell_electrical: float = 0.45
    fuel_cell_thermal: float = 0.40
    h2_boiler_efficiency: float = 0.90
    to_h2: float = field(default_factory=_stack_to_h2)

    def _heat(self, kwh_heat: float) -> tuple[float, float]:
        """(kg CO₂, €) avoided when kwh_heat of heat replaces the building's own heating."""
        if self.heating == "heat_pump":
            electricity = kwh_heat / self.heating_cop
            return electricity * self.grid_kgco2_per_kwh, electricity * self.import_price
        gas = kwh_heat / self.gas_boiler_efficiency
        return gas * GAS_KGCO2_PER_KWH, gas * self.gas_price

    def per_kwh(self, destination: Destination) -> tuple[float, float]:
        """(kg CO₂ avoided, € gained) per kWh of surplus sent to a destination."""
        if destination == "battery":
            rt = self.battery_round_trip
            return rt * self.grid_kgco2_per_kwh, rt * self.import_price
        if destination == "export":
            if not self.export_allowed:
                return 0.0, 0.0
            return self.grid_kgco2_per_kwh, self.export_price
        if self.end_use == "fuel_cell":
            el = self.to_h2 * self.fuel_cell_electrical
            heat_kg, heat_eur = self._heat(self.to_h2 * self.fuel_cell_thermal)
            return el * self.grid_kgco2_per_kwh + heat_kg, el * self.import_price + heat_eur
        return self._heat(self.to_h2 * self.h2_boiler_efficiency)

    def score(self, destination: Destination) -> float:
        """€ per kWh, CO₂ counted at the carbon price."""
        kg, eur = self.per_kwh(destination)
        return eur + kg * self.carbon_price_eur_per_t / 1000.0

    def order(self) -> list[Destination]:
        """Destinations from the best to the least good. Curtailment always comes last."""
        return sorted(DESTINATIONS, key=self.score, reverse=True)

    def table(self) -> list[dict[str, float | str]]:
        """For the dashboard and the planner: each destination with its numbers, in order."""
        rows: list[dict[str, float | str]] = []
        for d in self.order():
            kg, eur = self.per_kwh(d)
            rows.append(
                {
                    "destination": d,
                    "kg_co2": round(kg, 3),
                    "eur": round(eur, 3),
                    "score": round(self.score(d), 3),
                }
            )
        return rows
