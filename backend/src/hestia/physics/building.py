"""
The building as one thermal zone with two nodes: the air and the structure.

A single temperature for a whole house is too blunt: the air responds to a
heater in minutes, the walls and floors over a day. The simple hourly method
of ISO 13790 (now EN ISO 52016-1) separates them; this is its two-node
reduction:

    C_air  · dT_air/dt  = H_ao (T_out - T_air) + H_am (T_mass - T_air) + f·Q_int + Q_hvac
    C_mass · dT_mass/dt = H_mo (T_out - T_mass) + H_am (T_air - T_mass) + (1-f)·Q_int + Q_solar

- UA (W/K), total envelope and ventilation losses, from the floor area and
  the insulation class. 40 % (windows, ventilation) acts on the air, 60 %
  (opaque walls, roof, floor) through the structure; H_mo is chosen so the
  steady-state loss stays exactly UA.
- H_am = 9.1 W/(m²·K) × A_m, A_m = 2.5 × floor area: the air-to-structure
  coupling of ISO 13790's "medium" class.
- C_mass = 165 kJ/(m²·K) of floor ("medium", a typical masonry house);
  C_air = 15 kJ/(m²·K): the air plus furniture.
- Q_int: people and appliances, 3 W/m² averaged over the day, half convective.
  Q_solar: gains through glazing (an effective 1.5 % of the floor receiving
  the horizontal irradiance), absorbed by floors and walls.
- Heating and cooling act on the air (fan coils, convectors).

Comfort band 20-26 °C: the heating and cooling setpoints of EN 16798-1
category II for residential buildings. The planner uses the same losses and
gains in steady state (no thermal mass) for the hourly demand.
"""

from __future__ import annotations

from dataclasses import dataclass

INSULATION_W_PER_K_PER_M2 = {"poor": 2.5, "average": 1.5, "good": 0.8}

# Typical residential daily shape of electricity use (appliances and
# lighting, HVAC excluded), one share per local hour; sums to 1.
_SHAPE = (2.4, 2.1, 2.0, 2.0, 2.1, 2.8, 4.2, 5.4, 4.8, 4.0, 3.8, 3.9,
          4.3, 4.2, 3.9, 3.8, 4.2, 5.3, 6.6, 7.2, 6.9, 5.8, 4.5, 3.1)  # fmt: skip
LOAD_SHAPE = tuple(v / sum(_SHAPE) for v in _SHAPE)
MASS_J_PER_K_PER_M2 = 165_000.0
AIR_J_PER_K_PER_M2 = 15_000.0
COUPLING_W_PER_K_PER_M2 = 9.1 * 2.5  # h_ms × A_m / A_floor (ISO 13790, medium class)
AIR_SIDE_SHARE = 0.4  # of UA: windows and ventilation
INTERNAL_GAINS_W_PER_M2 = 3.0
CONVECTIVE_SHARE = 0.5
GLAZING_EFFECTIVE_SHARE = 0.015


@dataclass(frozen=True)
class Building:
    floor_area_m2: float = 120.0
    insulation: str = "average"
    heat_setpoint_c: float = 20.0
    cool_setpoint_c: float = 26.0
    electricity_kwh_per_m2_year: float = 30.0  # appliances and lighting, HVAC excluded

    @property
    def ua_w_per_k(self) -> float:
        return INSULATION_W_PER_K_PER_M2.get(self.insulation, 1.5) * self.floor_area_m2

    @property
    def h_air_out(self) -> float:
        return AIR_SIDE_SHARE * self.ua_w_per_k

    @property
    def h_air_mass(self) -> float:
        return COUPLING_W_PER_K_PER_M2 * self.floor_area_m2

    @property
    def h_mass_out(self) -> float:
        # In series with h_air_mass it must carry the remaining share of UA.
        rest = (1.0 - AIR_SIDE_SHARE) * self.ua_w_per_k
        return rest * self.h_air_mass / (self.h_air_mass - rest)

    @property
    def capacity_j_per_k(self) -> float:
        return (MASS_J_PER_K_PER_M2 + AIR_J_PER_K_PER_M2) * self.floor_area_m2

    def electric_load_w(self, local_hour: int) -> float:
        """Appliances and lighting (W, mean over the local hour)."""
        daily_kwh = self.electricity_kwh_per_m2_year * self.floor_area_m2 / 365.0
        return daily_kwh * LOAD_SHAPE[local_hour % 24] * 1000.0

    def internal_gains_w(self) -> float:
        return INTERNAL_GAINS_W_PER_M2 * self.floor_area_m2

    def solar_gains_w(self, ghi: float) -> float:
        return GLAZING_EFFECTIVE_SHARE * self.floor_area_m2 * ghi

    def gains_w(self, ghi: float) -> float:
        return self.internal_gains_w() + self.solar_gains_w(ghi)

    def air_free_flow(self, air_c: float, mass_c: float, outdoor_c: float) -> float:
        """Heat flowing into the air node without heating or cooling (W)."""
        return (
            self.h_air_out * (outdoor_c - air_c)
            + self.h_air_mass * (mass_c - air_c)
            + CONVECTIVE_SHARE * self.internal_gains_w()
        )

    def step(
        self, air_c: float, mass_c: float, outdoor_c: float, ghi: float, hvac_w: float, dt: float
    ) -> tuple[float, float]:
        """(air, structure) temperatures after dt seconds; hvac_w > 0 heats, < 0 cools."""
        area = self.floor_area_m2
        air_flow = self.air_free_flow(air_c, mass_c, outdoor_c) + hvac_w
        mass_flow = (
            self.h_mass_out * (outdoor_c - mass_c)
            + self.h_air_mass * (air_c - mass_c)
            + (1.0 - CONVECTIVE_SHARE) * self.internal_gains_w()
            + self.solar_gains_w(ghi)
        )
        return (
            air_c + air_flow * dt / (AIR_J_PER_K_PER_M2 * area),
            mass_c + mass_flow * dt / (MASS_J_PER_K_PER_M2 * area),
        )

    def steady_demand(self, outdoor_c: float, ghi: float) -> tuple[float, float]:
        """(heating W, cooling W) to hold the comfort band in steady state (planner)."""
        heat = self.ua_w_per_k * (self.heat_setpoint_c - outdoor_c) - self.gains_w(ghi)
        cool = self.ua_w_per_k * (outdoor_c - self.cool_setpoint_c) + self.gains_w(ghi)
        return max(0.0, heat), max(0.0, cool)
