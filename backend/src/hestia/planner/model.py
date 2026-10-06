"""
The planner: should this building store solar energy as hydrogen, and how much?

Before anyone buys a €100 000 hydrogen system, they deserve an answer
computed from their own building and their own weather, not from a
brochure. The planner simulates a full real weather year, hour by hour,
for four set-ups and compares them on the same terms:

    1. today          grid electricity + the building's own heating and cooling
    2. solar          + rooftop PV
    3. solar+battery  + a battery for day-night shifting
    4. hestia         + an electrolyser, pressurised hydrogen storage and a
                        hydrogen end use: a fuel cell (electricity, with its
                        heat recovered) or a hydrogen boiler (heat). Summer
                        surplus becomes winter comfort.

It uses the simulator's physics (hestia/physics) at an hourly step:
- PV: tilted panels from the horizontal irradiance, with the sun at the
  middle of each weather hour (solar.py);
- building: hourly heating and cooling demand from the same losses and gains
  as the simulator's building, in steady state; appliances on local time
  (building.py);
- the building's own heating and cooling: a reversible heat pump with its COP
  and EER at each hour's outdoor temperature, or a gas boiler with optional
  electric cooling (conversion.py);
- electrolyser: the alkaline stack at its regulated temperature; the
  hydrogen made per kWh depends on the load (electrolyser.py).

Hourly dispatch, in the simulator's order (twin/plant.py):
    1. PV covers the building: appliances, heat pump, cooling.
    2. A shortfall: battery (down to its reserve), then hydrogen when the
       policy allows it, then the grid. The fuel cell makes electricity and
       its heat replaces heat the heat pump or the gas boiler would have
       made; the hydrogen boiler replaces heat that would otherwise come from
       grid electricity or gas.
    3. A surplus goes where it does the most good, nature first, then cost
       (domain/merit.py, the simulator's rule): battery, grid export (within
       the yearly allowance) and electrolyser (above its minimum load, while
       the tank has room) in the site's merit order; the rest is curtailed.

Being hourly, it works with each hour's energy: a stack whose hourly mean is
below its minimum load is not run, as in the simulator, and the fuel cell may
cover a small shortfall by running part of the hour. The year is run twice
and the second pass is reported, so the tank starts January with what a real
autumn would have left in it rather than empty.

Then, for each set-up: energy flows, self-sufficiency, CO₂, capital cost,
yearly cost, simple payback and net present value. For the hydrogen set-up
it also sweeps electrolyser power × tank size and recommends a size.

It is deliberately honest. Electricity → hydrogen → electricity keeps about
a quarter of the energy (half if the heat is used too), and with today's
equipment prices it rarely pays back on money alone. The planner says so in
plain words when it does not, and shows what hydrogen buys instead
(self-sufficiency, winter autonomy, CO₂) and at what price per kWh.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import timedelta, timezone, tzinfo
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hestia.domain.merit import SiteValues
from hestia.physics.building import Building
from hestia.physics.conversion import HeatPump
from hestia.physics.electrolyser import H2_LHV_KWH_PER_KG, AlkalineStack
from hestia.physics.solar import PvArray
from hestia.physics.storage import Tank
from hestia.twin.weather import HourlyYear

GAS_KGCO2_PER_KWH = 0.202
STACK_TEMP_C = 52.0  # the cooling loop holds 50-55 °C while the stack produces (domain/control.py)

# Hydrogen made per kWh at each load, from the stack model. The curve does
# not depend on the stack's size (same cells, same current density at rated
# power), so one table serves every electrolyser in the sweep.
_STACK = AlkalineStack(rated_w=1000.0)
STACK_MIN_LOAD = _STACK.min_load_fraction
_LOADS = np.linspace(STACK_MIN_LOAD, 1.0, 33)
_KG_PER_KWH = np.array([_STACK.operate(f * 1000.0, STACK_TEMP_C).h2_kg_per_s * 3600.0 / f for f in _LOADS])


def h2_kg_per_kwh(load_fraction: float) -> float:
    """Hydrogen made per kWh of electricity at a share of the stack's rated power."""
    return float(np.interp(load_fraction, _LOADS, _KG_PER_KWH))


class PlannerInputs(BaseModel):
    """Everything the planner needs; every default can be overridden."""

    model_config = ConfigDict(extra="forbid")

    preset: Literal["tunisia", "france", "custom"] = "tunisia"
    latitude: float = Field(36.8, ge=-90, le=90)
    longitude: float = Field(10.2, ge=-180, le=180)
    timezone: str | None = "Africa/Tunis"  # IANA name; None: solar time from the longitude

    # The building (defaults: the simulator's demo building).
    floor_area_m2: float = Field(120, gt=10, le=20_000)
    insulation: Literal["poor", "average", "good"] = "average"
    electricity_kwh_per_m2_year: float = Field(30, ge=5, le=300)
    heat_setpoint_c: float = Field(20, ge=16, le=24)
    cool_setpoint_c: float = Field(26, ge=22, le=30)
    heating: Literal["heat_pump", "gas_boiler"] = "heat_pump"
    cooling: bool = True  # electric cooling (the heat pump, or air conditioners)
    gas_boiler_efficiency: float = Field(0.90, gt=0.5, le=1.0)

    # Equipment.
    pv_kwp: float = Field(8.0, ge=0, le=1000)
    pv_tilt_deg: float = Field(30, ge=0, le=90)
    pv_azimuth_deg: float = Field(0, ge=-180, le=180)  # 0 = facing the equator, -90 east, +90 west
    battery_kwh: float = Field(10.0, ge=0, le=5000)
    battery_round_trip: float = Field(0.92, gt=0, le=1)
    battery_reserve: float = Field(0.20, ge=0, le=0.9)  # never drained below this share
    electrolyser_kw: float = Field(2.0, ge=0, le=1000)
    tank_kg: float = Field(10.0, ge=0, le=10_000)
    tank_mawp_bar: float = Field(30, ge=5, le=700)  # sets the tank's volume, reported with the result
    end_use: Literal["fuel_cell", "boiler"] = "fuel_cell"
    fuel_cell_kw: float = Field(1.5, ge=0, le=1000)
    fuel_cell_electrical_efficiency: float = Field(0.45, gt=0, le=0.7)
    fuel_cell_thermal_efficiency: float = Field(0.40, ge=0, le=0.6)
    h2_boiler_kw: float = Field(3.0, ge=0, le=5000)
    h2_boiler_efficiency: float = Field(0.90, gt=0, le=1.0)
    # auto: hydrogen is kept for the hours that need heat (seasonal storage),
    # or used when the tank is above 80 % to make room; heating_only: heating
    # hours only; grid_backup: any shortfall the battery cannot cover.
    h2_use: Literal["auto", "heating_only", "grid_backup"] = "auto"
    h2_reserve_pct: float = Field(5.0, ge=0, le=50)
    # value: the merit order (nature first, then cost); the others are fixed orders to compare.
    strategy: Literal["value", "battery_first", "hydrogen_first"] = "value"
    carbon_price_eur_per_t: float = Field(300, ge=0, le=2000)  # UBA 2024 climate cost

    # Tariffs.
    import_price: float = Field(0.088, ge=0, le=5)
    export_price: float = Field(0.035, ge=0, le=5)
    export_cap_share: float = Field(0.30, ge=0, le=1)  # of the yearly PV production
    gas_price: float = Field(0.026, ge=0, le=5)
    grid_kgco2_per_kwh: float = Field(0.468, ge=0, le=2)

    # Costs.
    pv_eur_per_kwp: float = Field(800, ge=0)
    battery_eur_per_kwh: float = Field(600, ge=0)
    electrolyser_eur_per_kw: float = Field(4000, ge=0)
    storage_eur_per_kg: float = Field(700, ge=0)
    fuel_cell_eur_per_kw: float = Field(4000, ge=0)
    h2_boiler_eur: float = Field(3500, ge=0)
    installation_share: float = Field(0.15, ge=0, le=1)
    om_share_per_year: float = Field(0.02, ge=0, le=0.2)
    lifetime_years: int = Field(20, ge=5, le=40)
    discount_rate: float = Field(0.06, ge=0, le=0.3)

    # balanced: the knee of the curve, the smallest system giving 80 % of the
    #   achievable self-sufficiency gain over solar + battery (default);
    # self_sufficiency: as independent as possible, cheapest within 2 % of the best;
    # value: the best net present value.
    objective: Literal["balanced", "self_sufficiency", "value"] = "balanced"

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError(f"unknown time zone {value!r}") from exc
        return value

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if self.cool_setpoint_c < self.heat_setpoint_c + 2.0:
            raise ValueError("the cooling setpoint must be at least 2 °C above the heating setpoint")
        if self.fuel_cell_electrical_efficiency + self.fuel_cell_thermal_efficiency > 0.95:
            raise ValueError("a fuel cell cannot return more than 95 % of its fuel as electricity and heat")
        return self

    def zone(self) -> tzinfo:
        if self.timezone:
            return ZoneInfo(self.timezone)
        return timezone(timedelta(hours=round(self.longitude / 15.0)))

    def values(self) -> SiteValues:
        """What a kWh of surplus is worth in each destination here."""
        return SiteValues(
            grid_kgco2_per_kwh=self.grid_kgco2_per_kwh,
            import_price=self.import_price,
            export_price=self.export_price,
            export_allowed=self.export_cap_share > 0,
            carbon_price_eur_per_t=self.carbon_price_eur_per_t,
            heating=self.heating,
            heating_cop=HeatPump().cop_heating(7.0),
            gas_price=self.gas_price,
            gas_boiler_efficiency=self.gas_boiler_efficiency,
            battery_round_trip=self.battery_round_trip,
            end_use=self.end_use,
            fuel_cell_electrical=self.fuel_cell_electrical_efficiency,
            fuel_cell_thermal=self.fuel_cell_thermal_efficiency,
            h2_boiler_efficiency=self.h2_boiler_efficiency,
        )

    def surplus_order(self) -> list[str]:
        if self.strategy == "battery_first":
            return ["battery", "hydrogen", "export"]
        if self.strategy == "hydrogen_first":
            return ["hydrogen", "battery", "export"]
        return list(self.values().order())

    def building(self) -> Building:
        return Building(
            floor_area_m2=self.floor_area_m2,
            insulation=self.insulation,
            heat_setpoint_c=self.heat_setpoint_c,
            cool_setpoint_c=self.cool_setpoint_c,
            electricity_kwh_per_m2_year=self.electricity_kwh_per_m2_year,
        )


@dataclass(frozen=True)
class Hours:
    """The weather year turned into the building's hourly needs (kWh in each hour)."""

    pv_per_kwp: list[float]
    load: list[float]  # appliances and lighting
    heat: list[float]  # space heating demand (heat)
    cool: list[float]  # space cooling demand (heat removed)
    cop: list[float]  # heat pump, heating
    eer: list[float]  # heat pump, cooling
    month: list[int]  # local month, 0-11


def hourly_needs(x: PlannerInputs, weather: HourlyYear) -> Hours:
    n = len(weather)
    zone = x.zone()
    array = PvArray(kwp=1.0, tilt_deg=x.pv_tilt_deg, azimuth_deg=x.pv_azimuth_deg)
    building = x.building()
    hp = HeatPump()
    temps, ghis = weather.temperature_c, weather.irradiance_wpm2
    pv, load, heat, cool, cop, eer, month = [], [], [], [], [], [], []
    for i in range(n):
        # A weather hour is labelled at its end: its irradiance is the mean of
        # the preceding hour, so the middle of that hour stands for it.
        mid = weather.start + timedelta(hours=i - 0.5)
        outdoor = (temps[i - 1] + temps[i]) / 2.0 if i > 0 else temps[0]
        ghi = ghis[i]
        local = mid.astimezone(zone)
        heat_w, cool_w = building.steady_demand(outdoor, ghi)
        pv.append(array.output(ghi, outdoor, mid, x.latitude, x.longitude) / 1000.0)
        load.append(building.electric_load_w(local.hour) / 1000.0)
        heat.append(heat_w / 1000.0)
        cool.append(cool_w / 1000.0 if x.cooling else 0.0)
        cop.append(hp.cop_heating(outdoor))
        eer.append(hp.eer_cooling(outdoor))
        month.append(local.month - 1)
    return Hours(pv, load, heat, cool, cop, eer, month)


@dataclass
class Flows:
    pv_kwh: float = 0.0
    load_kwh: float = 0.0  # appliances and lighting
    heat_kwh: float = 0.0  # heating demand
    cool_kwh: float = 0.0  # cooling demand
    hvac_kwh: float = 0.0  # electricity for heating and cooling
    direct_kwh: float = 0.0  # PV used by the building as it is produced
    battery_in_kwh: float = 0.0
    battery_out_kwh: float = 0.0
    electrolyser_kwh: float = 0.0
    export_kwh: float = 0.0
    curtailed_kwh: float = 0.0
    import_kwh: float = 0.0
    heat_pump_heat_kwh: float = 0.0  # heat from the heat pump
    gas_heat_kwh: float = 0.0  # heat from the gas boiler
    h2_produced_kg: float = 0.0
    h2_used_kg: float = 0.0
    fuel_cell_kwh: float = 0.0  # electricity from the fuel cell
    h2_heat_kwh: float = 0.0  # heat from hydrogen: recovered from the fuel cell, or from the boiler
    monthly_tank_kg: list[float] = field(default_factory=list)
    monthly_h2_kwh: list[float] = field(default_factory=list)  # useful energy from hydrogen
    monthly_heat_kwh: list[float] = field(default_factory=list)
    monthly_cool_kwh: list[float] = field(default_factory=list)
    monthly_pv_kwh: list[float] = field(default_factory=list)
    monthly_import_kwh: list[float] = field(default_factory=list)

    @property
    def bought_kwh(self) -> float:
        """Energy bought from outside: grid electricity and gas heat."""
        return self.import_kwh + self.gas_heat_kwh

    @property
    def h2_useful_kwh(self) -> float:
        return self.fuel_cell_kwh + self.h2_heat_kwh


@dataclass(frozen=True)
class Sizes:
    pv_kwp: float
    battery_kwh: float
    electrolyser_kw: float
    tank_kg: float

    @property
    def has_h2(self) -> bool:
        return self.electrolyser_kw > 0 and self.tank_kg > 0


@dataclass(frozen=True)
class Scenario:
    sizes: Sizes
    flows: Flows
    economics: dict[str, float]

    def as_dict(self) -> dict[str, object]:
        return {"sizes": self.sizes.__dict__, "flows": _round(self.flows), "economics": self.economics}


def simulate(x: PlannerInputs, s: Sizes, hours: Hours) -> Flows:
    """One year in its periodic steady state: run twice, report the second pass."""
    _, tank, soc = _year(x, s, hours, tank=0.0, soc=0.5 * s.battery_kwh)
    flows, _, _ = _year(x, s, hours, tank=tank, soc=soc)
    return flows


def _year(x: PlannerInputs, s: Sizes, hours: Hours, *, tank: float, soc: float) -> tuple[Flows, float, float]:
    f = Flows()
    hp_heats = x.heating == "heat_pump"
    eff = math.sqrt(x.battery_round_trip)
    batt_power = s.battery_kwh / 2.0  # a 2-hour battery
    batt_floor = x.battery_reserve * s.battery_kwh
    soc = max(soc, batt_floor)
    reserve_kg = x.h2_reserve_pct / 100.0 * s.tank_kg
    min_stack = STACK_MIN_LOAD * s.electrolyser_kw
    fc_kw = x.fuel_cell_kw if s.has_h2 and x.end_use == "fuel_cell" else 0.0
    boiler_kw = x.h2_boiler_kw if s.has_h2 and x.end_use == "boiler" else 0.0
    fc_el, fc_th = x.fuel_cell_electrical_efficiency, x.fuel_cell_thermal_efficiency
    ratio = fc_th / fc_el  # recovered heat per kWh of electricity
    order = x.surplus_order()
    export_budget = 0.0
    m_tank, m_h2, m_heat, m_cool, m_pv, m_import = ([0.0] * 12 for _ in range(6))

    for h in range(len(hours.load)):
        pv = hours.pv_per_kwp[h] * s.pv_kwp
        load, q, c = hours.load[h], hours.heat[h], hours.cool[h]
        cop, eer = hours.cop[h], hours.eer[h]
        m = hours.month[h]

        heat_hp = q if hp_heats else 0.0  # heat still to be made by the heat pump
        gas_heat = 0.0 if hp_heats else q  # heat still to be made by the gas boiler
        demand = load + heat_hp / cop + c / eer
        direct = min(pv, demand)
        surplus, deficit = pv - direct, demand - direct

        # ── Shortfall: battery, hydrogen, grid ───────────────────────────────
        if deficit > 0.0 and s.battery_kwh > 0.0:
            out = min(deficit, batt_power, max(0.0, soc - batt_floor) * eff)
            soc -= out / eff
            deficit -= out
            f.battery_out_kwh += out

        h2_free = tank - reserve_kg
        spill = x.h2_use == "auto" and tank >= 0.8 * s.tank_kg  # make room for more
        if h2_free > 0.0 and (x.h2_use == "grid_backup" or q > 0.0 or spill):
            if fc_kw > 0.0 and deficit > 0.0:
                # Electricity e with heat ratio·e: when the heat pump heats,
                # that heat also saves heat_hp/cop of its electricity.
                if hp_heats and heat_hp > 0.0:
                    e = deficit / (1.0 + ratio / cop)
                    if ratio * e > heat_hp:
                        e = deficit - heat_hp / cop
                else:
                    e = deficit
                e = min(e, fc_kw, h2_free * H2_LHV_KWH_PER_KG * fc_el)
                recovered = min(ratio * e, heat_hp if hp_heats else gas_heat)
                kg = e / (fc_el * H2_LHV_KWH_PER_KG)
                tank -= kg
                f.h2_used_kg += kg
                f.fuel_cell_kwh += e
                f.h2_heat_kwh += recovered
                m_h2[m] += e + recovered
                deficit = max(0.0, deficit - e)
                if hp_heats:
                    heat_hp -= recovered
                    deficit = max(0.0, deficit - recovered / cop)
                else:
                    gas_heat -= recovered
            elif boiler_kw > 0.0 and q > 0.0:
                # With a heat pump: only the heat whose electricity would come from the grid.
                want = min(deficit, heat_hp / cop) * cop if hp_heats else gas_heat
                b = min(want, boiler_kw, h2_free * H2_LHV_KWH_PER_KG * x.h2_boiler_efficiency)
                kg = b / (x.h2_boiler_efficiency * H2_LHV_KWH_PER_KG)
                tank -= kg
                f.h2_used_kg += kg
                f.h2_heat_kwh += b
                m_h2[m] += b
                if hp_heats:
                    heat_hp -= b
                    deficit = max(0.0, deficit - b / cop)
                else:
                    gas_heat -= b
        f.import_kwh += deficit
        m_import[m] += deficit

        # ── Surplus: in the merit order, then curtailment ────────────────────
        export_budget += x.export_cap_share * pv
        if surplus > 0.0:
            for destination in order:
                if destination == "battery" and s.battery_kwh > 0.0:
                    charge = min(surplus, batt_power, (s.battery_kwh - soc) / eff)
                    soc += charge * eff
                    surplus -= charge
                    f.battery_in_kwh += charge
                elif destination == "hydrogen" and s.has_h2 and surplus >= min_stack and tank < s.tank_kg:
                    power = min(surplus, s.electrolyser_kw)
                    made = power * h2_kg_per_kwh(power / s.electrolyser_kw)
                    if made > s.tank_kg - tank:  # the tank fills during this hour
                        power *= (s.tank_kg - tank) / made
                        made = s.tank_kg - tank
                    tank += made
                    surplus -= power
                    f.electrolyser_kwh += power
                    f.h2_produced_kg += made
                elif destination == "export":
                    export = min(surplus, max(0.0, export_budget))
                    export_budget -= export
                    surplus -= export
                    f.export_kwh += export
            f.curtailed_kwh += surplus

        f.pv_kwh += pv
        f.direct_kwh += direct
        f.load_kwh += load
        f.heat_kwh += q
        f.cool_kwh += c
        f.hvac_kwh += heat_hp / cop + c / eer
        f.heat_pump_heat_kwh += heat_hp
        f.gas_heat_kwh += gas_heat
        m_tank[m] = tank
        m_heat[m] += q
        m_cool[m] += c
        m_pv[m] += pv

    f.monthly_tank_kg = [round(v, 2) for v in m_tank]
    f.monthly_h2_kwh = [round(v, 1) for v in m_h2]
    f.monthly_heat_kwh = [round(v, 1) for v in m_heat]
    f.monthly_cool_kwh = [round(v, 1) for v in m_cool]
    f.monthly_pv_kwh = [round(v, 1) for v in m_pv]
    f.monthly_import_kwh = [round(v, 1) for v in m_import]
    return f, tank, soc


def h2_capex(x: PlannerInputs, s: Sizes) -> float:
    """Hydrogen hardware, installed."""
    if not s.has_h2:
        return 0.0
    end_use = x.fuel_cell_kw * x.fuel_cell_eur_per_kw if x.end_use == "fuel_cell" else x.h2_boiler_eur
    equipment = s.electrolyser_kw * x.electrolyser_eur_per_kw + s.tank_kg * x.storage_eur_per_kg + end_use
    return equipment * (1 + x.installation_share)


def economics(x: PlannerInputs, s: Sizes, f: Flows, today: Flows) -> dict[str, float]:
    capex = (s.pv_kwp * x.pv_eur_per_kwp + s.battery_kwh * x.battery_eur_per_kwh) * (
        1 + x.installation_share
    ) + h2_capex(x, s)
    cost, co2 = _running(x, f)
    base_cost, base_co2 = _running(x, today)
    yearly = cost + capex * x.om_share_per_year
    savings = base_cost - yearly
    npv = savings * _annuity(x.discount_rate, x.lifetime_years) - capex
    need = today.bought_kwh
    used_on_site = f.direct_kwh + f.battery_in_kwh + f.electrolyser_kwh
    return {
        "capex": round(capex),
        "yearly_cost": round(yearly),
        "yearly_savings": round(savings),
        "payback_years": round(capex / savings, 1)
        if savings > 0 and capex > 0
        else (0.0 if capex == 0 else -1.0),
        "npv": round(npv),
        "co2_kg": round(co2),
        "co2_avoided_kg": round(base_co2 - co2),
        # Share of what the building used to buy (electricity and gas heat)
        # that its own sun now provides, directly or through storage.
        "self_sufficiency": round(max(0.0, 1.0 - f.bought_kwh / need), 4) if need > 0 else 0.0,
        "self_consumption": round(used_on_site / f.pv_kwh, 4) if f.pv_kwh > 0 else 0.0,
    }


def _running(x: PlannerInputs, f: Flows) -> tuple[float, float]:
    """Yearly energy bill and CO₂ (kg) for a set of flows."""
    gas = f.gas_heat_kwh / x.gas_boiler_efficiency
    cost = f.import_kwh * x.import_price - f.export_kwh * x.export_price + gas * x.gas_price
    co2 = f.import_kwh * x.grid_kgco2_per_kwh + gas * GAS_KGCO2_PER_KWH
    return cost, co2


def _annuity(rate: float, years: int) -> float:
    return years if rate == 0 else (1 - (1 + rate) ** -years) / rate


def chain(x: PlannerInputs) -> dict[str, float]:
    """What one kWh of surplus electricity becomes, at the stack's rated power."""
    to_h2 = h2_kg_per_kwh(1.0) * H2_LHV_KWH_PER_KG
    if x.end_use == "fuel_cell":
        electricity = to_h2 * x.fuel_cell_electrical_efficiency
        heat = to_h2 * x.fuel_cell_thermal_efficiency
    else:
        electricity, heat = 0.0, to_h2 * x.h2_boiler_efficiency
    return {
        "stack_kwh_per_kg": round(1.0 / h2_kg_per_kwh(1.0), 1),
        "to_hydrogen": round(to_h2, 3),
        "electricity": round(electricity, 3),
        "heat": round(heat, 3),
        "useful": round(electricity + heat, 3),
    }


def plan(x: PlannerInputs, weather: HourlyYear, weather_source: str) -> dict[str, object]:
    hours = hourly_needs(x, weather)
    today_flows = simulate(x, Sizes(0, 0, 0, 0), hours)

    def run(sizes: Sizes) -> Scenario:
        flows = simulate(x, sizes, hours)
        return Scenario(sizes, flows, economics(x, sizes, flows, today_flows))

    chosen = Sizes(x.pv_kwp, x.battery_kwh, x.electrolyser_kw, x.tank_kg)
    scenarios = {
        "today": Scenario(
            Sizes(0, 0, 0, 0), today_flows, economics(x, Sizes(0, 0, 0, 0), today_flows, today_flows)
        ),
        "solar": run(Sizes(x.pv_kwp, 0, 0, 0)),
        "solar_battery": run(Sizes(x.pv_kwp, x.battery_kwh, 0, 0)),
        "hestia": run(chosen),
    }

    # Size sweep for the hydrogen set-up (same PV and battery).
    grid_kw = sorted({0.5, 1.0, 2.0, 3.0, 5.0, x.electrolyser_kw} - {0.0})
    grid_kg = sorted({2.0, 5.0, 10.0, 20.0, 40.0, x.tank_kg} - {0.0})
    sweep = []
    for kw in grid_kw:
        for kg in grid_kg:
            sizes = Sizes(x.pv_kwp, x.battery_kwh, kw, kg)
            flows = simulate(x, sizes, hours)
            sweep.append(
                {
                    "electrolyser_kw": kw,
                    "tank_kg": kg,
                    **economics(x, sizes, flows, today_flows),
                    "h2_useful_kwh": round(flows.h2_useful_kwh),
                }
            )
    recommended = _recommend(sweep, x.objective, scenarios["solar_battery"].economics["self_sufficiency"])
    recommended["tank_volume_m3"] = round(
        Tank.for_capacity(recommended["tank_kg"], x.tank_mawp_bar).volume_m3, 2
    )

    return {
        "inputs": x.model_dump(),
        "weather_source": weather_source,
        "scenarios": {name: sc.as_dict() for name, sc in scenarios.items()},
        "sweep": sweep,
        "recommended": recommended,
        "tank_volume_m3": round(Tank.for_capacity(x.tank_kg, x.tank_mawp_bar).volume_m3, 2)
        if x.tank_kg > 0
        else 0.0,
        "pv_yield_kwh_per_kwp": round(sum(hours.pv_per_kwp)),
        "h2_cost_per_kwh": _h2_cost(x, scenarios["hestia"]),
        "grid_price_per_kwh": x.import_price,
        "chain": chain(x),
        "chain_efficiency": chain(x)["useful"],
        "merit": x.values().table(),
        "surplus_order": x.surplus_order(),
        "verdict": _verdict(x, scenarios, recommended),
    }


def _recommend(sweep: list[dict[str, float]], objective: str, without_h2: float) -> dict[str, float]:
    if objective == "value":
        return dict(max(sweep, key=lambda r: r["npv"]))
    best = max(r["self_sufficiency"] for r in sweep)
    if objective == "self_sufficiency":
        # The cheapest size within 98 % of the best: past that, more hardware buys almost nothing.
        good = [r for r in sweep if r["self_sufficiency"] >= 0.98 * best]
    else:
        target = without_h2 + 0.80 * (best - without_h2)
        good = [r for r in sweep if r["self_sufficiency"] >= target]
    return dict(min(good, key=lambda r: r["capex"]))


def _h2_cost(x: PlannerInputs, hestia: Scenario) -> float | None:
    """What each useful kWh from hydrogen costs, all hydrogen hardware included."""
    useful = hestia.flows.h2_useful_kwh
    if useful <= 0:
        return None
    capex = h2_capex(x, hestia.sizes)
    yearly = capex / _annuity(x.discount_rate, x.lifetime_years) + capex * x.om_share_per_year
    # The electricity it used would otherwise have been exported.
    yearly += hestia.flows.electrolyser_kwh * x.export_price
    return round(yearly / useful, 3)


def _verdict(x: PlannerInputs, scenarios: dict[str, Scenario], rec: dict[str, float]) -> list[str]:
    out = []
    solar, sb, h = (scenarios[n].economics for n in ("solar", "solar_battery", "hestia"))
    hf = scenarios["hestia"].flows
    names = {"battery": "the battery", "export": "the grid", "hydrogen": "hydrogen"}
    rows = {r["destination"]: r for r in x.values().table()}
    order = x.surplus_order()
    out.append(
        "Surplus goes first to " + ", then to ".join(names[d] for d in order) + ", nature first, then cost: "
        f"here a kWh sold to the grid avoids {rows['export']['kg_co2']:.2f} kg of CO₂ and earns "
        f"{rows['export']['eur']:.3f} €; through hydrogen it avoids {rows['hydrogen']['kg_co2']:.2f} kg "
        f"and is worth {rows['hydrogen']['eur']:.3f} €."
    )
    if solar["payback_years"] > 0:
        out.append(f"Solar alone pays back in about {solar['payback_years']:.0f} years.")
    out.append(
        f"Hydrogen storage raises self-sufficiency from {sb['self_sufficiency']:.0%} (solar + battery) "
        f"to {h['self_sufficiency']:.0%}, and avoids "
        f"{(h['co2_avoided_kg'] - sb['co2_avoided_kg']) / 1000:.1f} t of CO₂ a year more."
    )
    if x.end_use == "fuel_cell" and hf.fuel_cell_kwh > 0:
        out.append(
            f"The fuel cell returns {hf.fuel_cell_kwh:,.0f} kWh of electricity and "
            f"{hf.h2_heat_kwh:,.0f} kWh of heat a year from {hf.h2_used_kg:.1f} kg of hydrogen."
        )
    if x.end_use == "boiler" and x.heating == "heat_pump":
        out.append(
            "With a heat pump, a hydrogen boiler is the weaker end use: each kWh of hydrogen heat "
            "replaces a heat pump that would have made it from a third of a kWh of electricity. "
            "The fuel cell keeps the electricity and runs the heat pump with it."
        )
    extra = h["npv"] - sb["npv"]
    if extra < 0:
        out.append(
            f"On money alone, adding hydrogen costs {-extra:,.0f} € more over {x.lifetime_years} years than "
            "solar + battery at these prices: the conversion chain loses most of the energy and kW-scale "
            "electrolysers and fuel cells are still expensive. It makes sense when winter autonomy, CO₂ or "
            "independence from the grid matter more than payback."
        )
    else:
        out.append(
            f"At these prices hydrogen also pays: it adds {extra:,.0f} € of value "
            f"over {x.lifetime_years} years."
        )
    cost = _h2_cost(x, scenarios["hestia"])
    if cost is not None and x.import_price > 0:
        out.append(
            f"Each useful kWh from hydrogen costs about {cost:.2f} €, hardware included, against "
            f"{x.import_price:.3f} € for a kWh from the grid ({cost / x.import_price:.0f}× more)."
        )
    out.append(
        f"Recommended size: {rec['electrolyser_kw']:g} kW electrolyser with {rec['tank_kg']:g} kg of storage "
        f"({rec['tank_volume_m3']:g} m³ at {x.tank_mawp_bar:g} bar; {rec['self_sufficiency']:.0%} "
        f"self-sufficient, {rec['capex']:,.0f} € installed)."
    )
    return out


def _round(f: Flows) -> dict[str, object]:
    out: dict[str, object] = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in f.__dict__.items()}
    out["bought_kwh"] = round(f.bought_kwh, 2)
    out["h2_useful_kwh"] = round(f.h2_useful_kwh, 2)
    return out
