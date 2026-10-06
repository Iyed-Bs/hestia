"""
Hydrogen storage tank: pressure from mass and temperature, and its limits.

Hydrogen is not an ideal gas at storage pressures. The Abel-Noble equation
of state corrects for the volume of the molecules; it is the equation
commonly used in hydrogen safety engineering (e.g. Molkov, *Fundamentals of
Hydrogen Safety Engineering*) and is close to reference data at the
pressures of building storage:

    p = ρ R T / (1 - b ρ)      R = 4124.2 J/(kg·K), b = 7.691e-3 m³/kg

The tank is defined like a real one: its water volume and its maximum
allowable working pressure (MAWP). Its usable capacity is the mass that
reaches 95 % of the MAWP at the design temperature (50 °C), so that a full
tank warming up in the summer sun does not lift the relief valve. Above
95 % of MAWP production stops (interlock); at MAWP the relief valve vents.
"""

from __future__ import annotations

from dataclasses import dataclass

R_H2 = 4124.2  # J/(kg·K), specific gas constant of hydrogen
ABEL_NOBLE_B = 7.691e-3  # m³/kg (co-volume)
INTERLOCK_FRACTION = 0.95  # production stops at 95 % of MAWP
DESIGN_TEMP_C = 50.0


def pressure_bar(mass_kg: float, volume_m3: float, temp_c: float) -> float:
    if mass_kg <= 0.0 or volume_m3 <= 0.0:
        return 0.0
    rho = mass_kg / volume_m3
    return rho * R_H2 * (temp_c + 273.15) / (1.0 - ABEL_NOBLE_B * rho) / 1e5


def mass_at(pressure: float, volume_m3: float, temp_c: float) -> float:
    """Inverse of pressure_bar: ρ = p / (R T + b p)."""
    p = pressure * 1e5
    return volume_m3 * p / (R_H2 * (temp_c + 273.15) + ABEL_NOBLE_B * p)


@dataclass(frozen=True)
class Tank:
    volume_m3: float
    mawp_bar: float = 30.0  # e.g. a pressurised alkaline stack's delivery pressure, no compressor

    @classmethod
    def for_capacity(cls, capacity_kg: float, mawp_bar: float = 30.0) -> Tank:
        """The tank whose usable capacity is capacity_kg."""
        per_m3 = mass_at(INTERLOCK_FRACTION * mawp_bar, 1.0, DESIGN_TEMP_C)
        return cls(volume_m3=capacity_kg / per_m3, mawp_bar=mawp_bar)

    @property
    def capacity_kg(self) -> float:
        return mass_at(INTERLOCK_FRACTION * self.mawp_bar, self.volume_m3, DESIGN_TEMP_C)

    def pressure(self, mass_kg: float, temp_c: float) -> float:
        return pressure_bar(mass_kg, self.volume_m3, temp_c)

    def relief(self, mass_kg: float, temp_c: float) -> float:
        """Mass the relief valve vents (kg) to bring the tank back to its MAWP."""
        if self.pressure(mass_kg, temp_c) <= self.mawp_bar:
            return 0.0
        return mass_kg - mass_at(self.mawp_bar, self.volume_m3, temp_c)
