"""
The KOH electrolyte of an alkaline stack: concentration, volume, conductivity.

Electrolysis consumes water only (2 H₂O → 2 H₂ + O₂, 8.94 kg of water per kg
of H₂); the potassium hydroxide stays. So as the stack works, the solution
gets smaller and more concentrated. Topping it up with pure water restores
both at once, which is why level control is the main electrolyte control.
KOH itself leaves only slowly, as droplets carried away with the gases; that
slow dilution is corrected by dosing a 45 wt% KOH concentrate.

A glass pH probe is the wrong instrument here: 30 wt% KOH is about 6.9 mol/L,
pH above 14, where probes saturate and read low (the "alkaline error"). The
quantity that matters, and that the controller keeps in a band, is the KOH
mass fraction.

Conductivity follows from it (Gilliam et al. 2007, Int. J. Hydrogen Energy
32: 359-364):

    κ (S/cm) = -2.041 M - 0.0028 M² + 0.005332 M T + 207.2 M / T
               + 0.001043 M³ - 0.0000003 M² T²          (M mol/L, T kelvin)

It peaks near 30 wt% at stack temperatures, which is why stacks run there,
and also why a conductivity cell cannot measure the concentration in that
band: 26 and 28 wt% both give 0.626 S/cm at 25 °C. Density can: it rises
steadily, about 0.0097 kg/L per wt%, and inverts in closed form once the
temperature is known. The controller reads the electrolyte through a density
transmitter (firmware/src/sensors.cpp uses the same formula).
"""

from __future__ import annotations

from dataclasses import dataclass

KOH_KG_PER_MOL = 0.05611
WATER_KG_PER_H2_KG = 8.936  # 18.015 / 2.016
CONCENTRATE_WT = 45.0  # commercial KOH solution used for dosing


def density_kg_per_l(wt_pct: float, temp_c: float) -> float:
    """Density of aqueous KOH: linear in concentration (1.29 kg/L at 30 wt%, 25 °C), ~0.05 %/K expansion."""
    return (0.9971 + 0.00973 * wt_pct) * (1.0 - 0.0005 * (temp_c - 25.0))


def wt_pct_from_density(density_kg_l: float, temp_c: float) -> float:
    """What a density transmitter tells the controller: the inverse of density_kg_per_l."""
    return (density_kg_l / (1.0 - 0.0005 * (temp_c - 25.0)) - 0.9971) / 0.00973


def conductivity_s_per_cm(molarity: float, temp_c: float) -> float:
    """Gilliam et al. (2007), valid 0-12 mol/L and 0-100 °C."""
    m, t = max(0.0, molarity), temp_c + 273.15
    return max(
        0.0,
        -2.041 * m
        - 0.0028 * m**2
        + 0.005332 * m * t
        + 207.2 * m / t
        + 0.001043 * m**3
        - 0.0000003 * m**2 * t**2,
    )


@dataclass
class KohSolution:
    koh_kg: float
    water_kg: float

    @classmethod
    def mixed(cls, litres: float, wt_pct: float, temp_c: float = 25.0) -> KohSolution:
        mass = litres * density_kg_per_l(wt_pct, temp_c)
        return cls(koh_kg=mass * wt_pct / 100.0, water_kg=mass * (1.0 - wt_pct / 100.0))

    @property
    def mass_kg(self) -> float:
        return self.koh_kg + self.water_kg

    @property
    def wt_pct(self) -> float:
        return 100.0 * self.koh_kg / self.mass_kg if self.mass_kg > 0 else 0.0

    def litres(self, temp_c: float) -> float:
        return self.mass_kg / density_kg_per_l(self.wt_pct, temp_c)

    def molarity(self, temp_c: float) -> float:
        litres = self.litres(temp_c)
        return (self.koh_kg / KOH_KG_PER_MOL) / litres if litres > 0 else 0.0

    def conductivity(self, temp_c: float) -> float:
        return conductivity_s_per_cm(self.molarity(temp_c), temp_c)

    def electrolyse(self, h2_kg: float, koh_carry_over_g_per_kg_h2: float = 0.5) -> float:
        """Remove the water turned into gas (and the KOH mist it carries). Returns water used (kg)."""
        water = min(self.water_kg, h2_kg * WATER_KG_PER_H2_KG)
        self.water_kg -= water
        self.koh_kg = max(0.0, self.koh_kg - h2_kg * koh_carry_over_g_per_kg_h2 / 1000.0)
        return water

    def add_water(self, kg: float) -> None:
        self.water_kg += max(0.0, kg)

    def add_concentrate(self, kg: float) -> None:
        kg = max(0.0, kg)
        self.koh_kg += kg * CONCENTRATE_WT / 100.0
        self.water_kg += kg * (1.0 - CONCENTRATE_WT / 100.0)
