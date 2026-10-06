"""
Alkaline electrolyser stack: from electric power to hydrogen and heat.

Cell voltage with the empirical model of Ulleberg (2003), "Modeling of
advanced alkaline electrolyzers: a system simulation approach", Int. J.
Hydrogen Energy 28: 21-33, widely used in system simulation:

    U = U_rev(T) + (r1 + r2·T)·j + s·log10((t1 + t2/T + t3/T²)·j + 1)

with j the current density (A/m²) and T the electrolyte temperature (°C).
Faraday efficiency (share of the current that really makes hydrogen):

    η_F = (j²/(f1 + j²))·f2        with j in mA/cm²

Parameters: Table I of "Differential algebraic modeling of an alkaline
electrolyzer plant" (arXiv:2311.09882). Checked in tests: about 1.9 V per
cell at 0.2 A/cm² and 60 °C, typical of alkaline stacks.

From the operating point:
    H₂ (mol/s) = η_F · N · I / (2F)                       (Faraday's law)
    heat (W)   = N · I · (U - η_F · U_tn)                (energy balance;
                 U_tn = 1.481 V, the thermoneutral voltage: below it the
                 reaction would cool the stack, above it the excess is heat)

Auxiliaries (lye circulation pump, controls) draw a fixed 4 % of the rated
power whenever the stack runs, and the rectifier loses 5 %.

Why a minimum load: at low current density the Faraday efficiency falls
(≈ 0.82 at 10 % of rated current, ≈ 0.55 at 5 % here), the fixed auxiliary
load weighs more on every kilogram, and hydrogen crosses the diaphragm into
the oxygen side. Alkaline stacks are therefore run above 10-40 % of their
rating (IRENA, *Green hydrogen cost reduction*, 2020); 20 % is the default.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

FARADAY = 96485.332  # C/mol
H2_KG_PER_MOL = 2.01588e-3
U_THERMONEUTRAL = 1.481  # V, liquid water, higher-heating-value basis
H2_LHV_KWH_PER_KG = 33.33  # US DOE Hydrogen Program
H2_HHV_KWH_PER_KG = 39.41


def reversible_voltage(temp_c: float) -> float:
    """Open-circuit (reversible) cell voltage, -0.846 mV/K around 1.229 V at 25 °C (LeRoy 1983)."""
    return 1.229 - 0.000846 * (temp_c - 25.0)


@dataclass(frozen=True)
class StackPoint:
    """One operating point of the stack."""

    power_w: float  # electric input from the AC side
    current_a: float
    cell_voltage: float
    faraday_efficiency: float
    h2_kg_per_s: float
    heat_w: float  # released into the electrolyte

    @property
    def kwh_per_kg(self) -> float:
        return (self.power_w / 1000.0) / (self.h2_kg_per_s * 3600.0) if self.h2_kg_per_s > 0 else math.inf

    @property
    def efficiency_lhv(self) -> float:
        return H2_LHV_KWH_PER_KG / self.kwh_per_kg if self.h2_kg_per_s > 0 else 0.0


@dataclass(frozen=True)
class AlkalineStack:
    rated_w: float = 1000.0  # AC input at rated current density
    min_load_fraction: float = 0.20
    cell_area_m2: float = 0.02  # 200 cm² cells
    rated_current_density: float = 2500.0  # A/m² (0.25 A/cm²)
    rectifier_efficiency: float = 0.95  # AC → DC
    auxiliary_fraction: float = 0.04  # lye pump and controls, drawn whenever the stack runs
    # Ulleberg-model coefficients (arXiv:2311.09882, Table I)
    r1: float = 2.18e-4  # Ω·m²
    r2: float = -4.25e-7  # Ω·m²/°C
    s: float = 0.11793  # V
    t1: float = -0.14529  # m²/A
    t2: float = 11.794  # m²·°C/A
    t3: float = 395.68  # m²·°C²/A
    f1: float = 120.0  # (mA/cm²)²
    f2: float = 0.98
    _cells: int = field(default=0, compare=False)

    def __post_init__(self) -> None:
        # As many cells as it takes to absorb the rated power at the rated
        # current density, at a 60 °C reference temperature.
        per_cell = (
            self.cell_voltage(self.rated_current_density, 60.0)
            * self.rated_current_density
            * self.cell_area_m2
        )
        object.__setattr__(self, "_cells", max(1, round(self.stack_dc_w(self.rated_w) / per_cell)))

    @property
    def cells(self) -> int:
        return self._cells

    @property
    def min_power_w(self) -> float:
        return self.min_load_fraction * self.rated_w

    def stack_dc_w(self, power_w: float) -> float:
        """DC power reaching the cells once auxiliaries and the rectifier have taken their share."""
        return max(0.0, power_w - self.auxiliary_fraction * self.rated_w) * self.rectifier_efficiency

    def cell_voltage(self, current_density: float, temp_c: float) -> float:
        t = min(80.0, max(10.0, temp_c))  # the fitted range of the model
        ohmic = (self.r1 + self.r2 * t) * current_density
        activation = self.s * math.log10((self.t1 + self.t2 / t + self.t3 / t**2) * current_density + 1.0)
        return reversible_voltage(t) + ohmic + activation

    def faraday_efficiency(self, current_density: float) -> float:
        j = current_density / 10.0  # A/m² → mA/cm²
        return (j**2 / (self.f1 + j**2)) * self.f2 if j > 0 else 0.0

    def operate(self, power_w: float, temp_c: float) -> StackPoint:
        """Operating point for an electric input (W, AC side) at a temperature."""
        if power_w <= 0.0:
            return StackPoint(0.0, 0.0, reversible_voltage(temp_c), 0.0, 0.0, 0.0)
        power_w = min(power_w, self.rated_w * 1.1)
        dc = self.stack_dc_w(power_w)
        if dc <= 0.0:
            return StackPoint(power_w, 0.0, reversible_voltage(temp_c), 0.0, 0.0, 0.0)
        n, area = self.cells, self.cell_area_m2

        def stack_power(current: float) -> float:
            return n * self.cell_voltage(current / area, temp_c) * current

        # Power rises monotonically with current: bisection is robust.
        lo, hi = 0.0, 2.0 * self.rated_current_density * area
        for _ in range(60):
            mid = (lo + hi) / 2.0
            if stack_power(mid) < dc:
                lo = mid
            else:
                hi = mid
        current = (lo + hi) / 2.0
        j = current / area
        voltage = self.cell_voltage(j, temp_c)
        eta_f = self.faraday_efficiency(j)
        mol_per_s = eta_f * n * current / (2.0 * FARADAY)
        heat = n * current * (voltage - eta_f * U_THERMONEUTRAL)
        return StackPoint(power_w, current, voltage, eta_f, mol_per_s * H2_KG_PER_MOL, max(0.0, heat))
