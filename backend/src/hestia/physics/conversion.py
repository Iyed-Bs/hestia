"""
Turning stored hydrogen into comfort: fuel cell, hydrogen boiler, heat pump.

Fuel cell (PEM, combined heat and power). Published studies of 1 kW
residential PEM micro-CHP systems report about 35-38 % electrical and 75-91 %
total efficiency (LHV) on reformed natural gas (e.g. Politecnico di Torino,
iris.polito.it/handle/11583/2536690; Aalborg University HT-PEMFC micro-CHP
studies). Fed directly with hydrogen the reformer losses disappear, so 45 %
electrical and 40 % recovered heat (LHV) are the defaults, and both are
settings. Its electricity runs the heat pump (heating or cooling) and the
building; its heat goes to space heating when heating is needed.

Hydrogen boiler: 90 % of the LHV as heat, heat only.

Heat pump (air to water): the COP is a fraction of the Carnot COP between
the refrigerant's condensing and evaporating temperatures (water side ± 3 K,
air side ± 5-8 K). A fraction of 0.45 gives COP ≈ 3.9 at the EN 14511 heating
rating point A7/W35 and EER ≈ 3.0 at the cooling point A35/W7, in line with
the rated values of current residential units.
"""

from __future__ import annotations

from dataclasses import dataclass

from .electrolyser import H2_LHV_KWH_PER_KG

J_PER_KWH = 3.6e6


def h2_energy_w_to_kg_per_s(power_w: float) -> float:
    """Hydrogen flow carrying power_w of chemical energy (LHV)."""
    return power_w / (H2_LHV_KWH_PER_KG * J_PER_KWH)


@dataclass(frozen=True)
class FuelCell:
    rated_w: float = 1500.0  # electrical output
    electrical_efficiency: float = 0.45
    thermal_efficiency: float = 0.40
    min_load_fraction: float = 0.10

    def run(self, electric_w: float) -> tuple[float, float]:
        """(hydrogen kg/s, recovered heat W) to deliver electric_w."""
        if electric_w <= 0.0:
            return 0.0, 0.0
        fuel_w = electric_w / self.electrical_efficiency
        return h2_energy_w_to_kg_per_s(fuel_w), fuel_w * self.thermal_efficiency


@dataclass(frozen=True)
class H2Boiler:
    rated_w: float = 3000.0  # heat output
    efficiency: float = 0.90

    def run(self, heat_w: float) -> float:
        """Hydrogen kg/s to deliver heat_w."""
        return h2_energy_w_to_kg_per_s(max(0.0, heat_w) / self.efficiency)


@dataclass(frozen=True)
class HeatPump:
    rated_heat_w: float = 4000.0  # thermal output, heating or cooling
    carnot_fraction: float = 0.45
    supply_heating_c: float = 35.0  # low-temperature emitters (floor, fan coils)
    supply_cooling_c: float = 7.0  # chilled water to fan coils

    def cop_heating(self, outdoor_c: float) -> float:
        condensing = self.supply_heating_c + 3.0 + 273.15
        evaporating = outdoor_c - 5.0 + 273.15
        return min(6.0, max(1.5, self.carnot_fraction * condensing / max(1.0, condensing - evaporating)))

    def eer_cooling(self, outdoor_c: float) -> float:
        evaporating = self.supply_cooling_c - 3.0 + 273.15
        condensing = outdoor_c + 8.0 + 273.15
        return min(6.0, max(1.5, self.carnot_fraction * evaporating / max(1.0, condensing - evaporating)))
