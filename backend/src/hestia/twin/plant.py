"""
The physical installations, simulated.

BenchPlant   the stack, its KOH loop, the cooling radiator, the room around
             it and the sensors; shared by both models below
BenchModel   the échantillon on its lab power supply (the live view)
SiteModel    a whole building around the stack (the simulator): PV, battery,
             pressurised storage, fuel cell or hydrogen boiler, heat pump,
             grid

A model never decides anything: it receives the controller's outputs and the
energy manager's comfort intent, and answers with physics (hestia/physics,
every equation with its source). Its sensors add realistic noise and the
faults from twin/faults.py.

Site dispatch, every step (all powers in W; the planner uses the same order):
    1. PV covers the building's appliances, then the heat pump
    2. a shortfall is covered by the battery, then hydrogen (if it may be
       used: the fuel cell makes electricity, the hydrogen boiler takes over
       the heat pump's share), then the grid; what is left is unserved
       (heating and cooling are shed first). In a grid outage hydrogen is
       always allowed: keeping the lights on is what it is stored for.
    3. a surplus goes where it does the most good, nature first, then cost
       (domain/merit.py): the battery, the grid (within the export limit and
       the yearly allowance) and the electrolyser, in the site's merit order;
       whatever none of them can take is curtailed
    4. the battery may lift a *running* stack back to its minimum load while
       the sun is up (a cloud), never at night, and never while it charges
Each step also says, in one sentence, where the power went and why.
"""

from __future__ import annotations

import math
import random
from datetime import datetime
from zoneinfo import ZoneInfo

from hestia.domain.control import Outputs, SensorReading
from hestia.domain.energy import HvacCommand
from hestia.domain.telemetry import SiteContext
from hestia.physics.electrolyser import StackPoint, reversible_voltage
from hestia.physics.electrolyte import CONCENTRATE_WT, KohSolution, density_kg_per_l

from .faults import POISONED_SENSITIVITY, FaultInjector
from .params import J_PER_KWH, BenchProfile, SiteProfile
from .weather import WeatherReplay

CLOUD_FLOOR_W = 100.0  # below this much PV it is dusk, not a cloud: the battery stops helping


def solution_cp_j_per_kg_k(wt_pct: float) -> float:
    """Specific heat of aqueous KOH, ≈ 3.0 kJ/(kg·K) at 30 wt%."""
    return 4186.0 - 39.0 * wt_pct


def _local(when: datetime, tz: ZoneInfo) -> datetime:
    return when.astimezone(tz)


class BenchPlant:
    """Stack, electrolyte loop, room and sensors."""

    def __init__(self, profile: BenchProfile, rng: random.Random) -> None:
        self.p = profile
        self.rng = rng
        self.electrolyte_c = 22.0
        self.solution = KohSolution.mixed(profile.electrolyte_l, profile.electrolyte_wt_pct)
        self.nominal_kg = self.solution.mass_kg
        self.room_h2_ppm = 0.0  # true concentration from leaks (the sensor adds its own baseline)
        self.h2_sensor_baseline = profile.h2_baseline_ppm
        self._last_h2 = profile.h2_baseline_ppm
        self._last_temp = self.electrolyte_c
        self.point = StackPoint(0.0, 0.0, reversible_voltage(22.0), 0.0, 0.0, 0.0)
        self.totals = {
            "electrolyser_kwh": 0.0,
            "h2_produced_kg": 0.0,
            "water_l": 0.0,
            "electrolyser_hours": 0.0,
        }

    @property
    def level_low(self) -> bool:
        return self.solution.mass_kg < self.p.level_low_fraction * self.nominal_kg

    def h2_reading(self, faults: FaultInjector, now_s: float, gas_ppm: float) -> float:
        """What the MQ-8 reads for a given true concentration at the sensor."""
        if faults.is_on("h2_dead"):
            return max(0.0, self.rng.gauss(0.0, 0.02))
        if faults.is_on("h2_stuck"):
            return self._last_h2
        sensitivity = POISONED_SENSITIVITY if faults.is_on("h2_poisoned") else 1.0
        return max(
            0.0,
            self.h2_sensor_baseline
            + faults.drift_ppm(now_s)
            + sensitivity * gas_ppm
            + self.rng.gauss(0.0, self.p.h2_noise_ppm),
        )

    def read_sensors(
        self, faults: FaultInjector, now_s: float, tank_bar: float = 0.0, tank_valid: bool = True
    ) -> SensorReading:
        g = self.rng.gauss
        # Electrolyte temperature (DS18B20 class: ±0.06 °C, -127 when unplugged).
        temp_valid = not faults.is_on("temp_disconnect")
        if not temp_valid:
            temp = -127.0
        elif faults.is_on("temp_stuck"):
            temp = self._last_temp
        else:
            temp = self.electrolyte_c + g(0.0, 0.06)
            self._last_temp = temp
        # KOH strength from a density transmitter (±0.0005 kg/L ≈ ±0.05 wt%).
        koh_valid = not faults.is_on("koh_probe_fail")
        koh = self.solution.wt_pct + g(0.0, 0.05) if koh_valid else 0.0
        h2 = self.h2_reading(faults, now_s, self.room_h2_ppm)
        if not faults.is_on("h2_stuck"):
            self._last_h2 = h2
        return SensorReading(
            electrolyte_c=round(temp, 2),
            koh_wt_pct=round(max(0.0, koh), 2),
            level_low=self.level_low,
            h2_ppm=round(h2, 1),
            t_s=now_s,
            tank_bar=round(tank_bar, 2),
            temp_valid=temp_valid,
            koh_valid=koh_valid,
            h2_valid=True,
            tank_valid=tank_valid,
        )

    def advance(
        self, out: Outputs, available_w: float, ambient_c: float, dt: float, faults: FaultInjector
    ) -> StackPoint:
        p = self.p
        stack_w = 0.0
        if out.electrolyser and out.h2_relay_closed:
            power = min(available_w, p.stack.rated_w)
            if power >= p.stack.min_power_w:  # below its minimum the stack's supply cuts out
                stack_w = power
        point = p.stack.operate(stack_w, self.electrolyte_c)
        h2_kg = point.h2_kg_per_s * dt

        # Electrolyte: electrolysis takes water; make-up and dosing put it back.
        water_used = self.solution.electrolyse(h2_kg, p.koh_carry_over_g_per_kg_h2)
        if out.water_makeup and self.solution.mass_kg < 1.05 * self.nominal_kg:
            self.solution.add_water(p.makeup_l_per_min / 60.0 * dt)  # 1 kg per litre
        if out.koh_dosing:
            self.solution.add_concentrate(
                p.koh_dose_l_per_min / 60.0 * dt * density_kg_per_l(CONCENTRATE_WT, 25.0)
            )

        # Heat: from the stack, out through the radiator (if water flows) and the walls.
        capacity = (
            self.solution.mass_kg * solution_cp_j_per_kg_k(self.solution.wt_pct)
            + p.stack_heat_capacity_j_per_k
        )
        ua = p.passive_ua_w_per_k
        if out.cooling_pump and not faults.is_on("cooling_failure"):
            ua += p.cooler_ua_w_per_k
        self.electrolyte_c += (point.heat_w - ua * (self.electrolyte_c - ambient_c)) * dt / capacity

        # Room: V·dC/dt = leak - C·Q, solved exactly over the step.
        ach = p.natural_ach + (
            p.extraction_ach if out.ventilation and not faults.is_on("fan_failure") else 0.0
        )
        flow = ach * p.room_volume_m3 / 3600.0  # m³/s of fresh air
        leak = faults.leak_m3_per_s()
        c = self.room_h2_ppm / 1e6
        steady = leak / flow
        c = steady + (c - steady) * math.exp(-flow * dt / p.room_volume_m3)
        self.room_h2_ppm = max(0.0, c * 1e6)

        t = self.totals
        t["electrolyser_kwh"] += point.power_w * dt / J_PER_KWH
        t["h2_produced_kg"] += h2_kg
        t["water_l"] += water_used
        t["electrolyser_hours"] += dt / 3600.0 if point.power_w > 0 else 0.0
        self.point = point
        return point


class BenchModel:
    """The échantillon: the bench prototype on its lab supply."""

    kind = "bench"

    def __init__(
        self, profile: BenchProfile | None = None, *, seed: int = 42, weather: WeatherReplay | None = None
    ) -> None:
        self.p = profile or BenchProfile()
        self.rng = random.Random(seed)  # nosec B311  # simulation noise, not security
        self.plant = BenchPlant(self.p, self.rng)
        self.weather = weather or WeatherReplay()
        self.tz = ZoneInfo("Africa/Tunis")
        self.elapsed_s = self.weather.offset_of(3, 20, 8.5, self.tz)

    @property
    def storage_mawp_bar(self) -> float:
        return 0.0  # the bench vents its gas outdoors: no pressurised storage

    def read(self, faults: FaultInjector, now_s: float) -> SensorReading:
        return self.plant.read_sensors(faults, now_s)

    def advance(self, out: Outputs, hvac: HvacCommand, dt: float, faults: FaultInjector) -> SiteContext:
        w = self.weather.at(self.elapsed_s)
        point = self.plant.advance(out, self.p.supply_w, self.p.lab_temperature_c, dt, faults)
        self.elapsed_s += dt
        return SiteContext(
            source="twin",
            timestamp=w.timestamp,
            local_time=_local(w.timestamp, self.tz).strftime("%Y-%m-%d %H:%M"),
            outdoor_c=round(w.temperature_c, 1),
            humidity_pct=round(w.humidity_pct, 1),
            irradiance_wpm2=round(w.irradiance_wpm2, 1),
            pv_w=0.0,
            electrolyser_w=round(point.power_w, 1),
            available_w=self.p.supply_w,
            room_h2_ppm=round(self.plant.room_h2_ppm, 1),
        )


class SiteModel:
    """A building with the whole hydrogen chain."""

    kind = "site"

    def __init__(
        self,
        profile: SiteProfile | None = None,
        *,
        seed: int = 42,
        weather: WeatherReplay | None = None,
        start_offset_s: float | None = None,
    ) -> None:
        self.p = p = profile or SiteProfile()
        self.rng = random.Random(seed)  # nosec B311  # simulation noise, not security
        self.plant = BenchPlant(p.bench, self.rng)
        self.weather = weather or WeatherReplay()
        self.tz = ZoneInfo(p.timezone)
        self.elapsed_s = (
            start_offset_s if start_offset_s is not None else self.weather.offset_of(3, 20, 8.5, self.tz)
        )
        self.h2_kg = 0.25 * p.tank.capacity_kg
        self.battery_kwh = 0.5 * p.battery_kwh
        self.indoor_c = 21.0  # air
        self.mass_c = 21.0  # walls, floors, furniture
        self.tank_c = 20.0
        self.available_w = 0.0
        self.export_budget_kwh = 0.0  # what may still be sold (export_cap_share of production)
        self.totals = {
            "pv_kwh": 0.0,
            "load_kwh": 0.0,
            "heat_pump_kwh": 0.0,
            "fuel_cell_kwh": 0.0,
            "grid_import_kwh": 0.0,
            "grid_export_kwh": 0.0,
            "curtailed_kwh": 0.0,
            "unserved_kwh": 0.0,
            "heating_kwh": 0.0,
            "cooling_kwh": 0.0,
            "h2_used_kg": 0.0,
            "h2_vented_kg": 0.0,
        }

    @property
    def storage_mawp_bar(self) -> float:
        return self.p.tank.mawp_bar

    @property
    def tank_bar(self) -> float:
        return self.p.tank.pressure(self.h2_kg, self.tank_c)

    @property
    def tank_pct(self) -> float:
        return 100.0 * self.h2_kg / self.p.tank.capacity_kg

    def read(self, faults: FaultInjector, now_s: float) -> SensorReading:
        valid = not faults.is_on("tank_sensor_fail")
        bar = self.tank_bar + self.rng.gauss(0.0, 0.03) if valid else 0.0
        return self.plant.read_sensors(faults, now_s, max(0.0, bar), valid)

    def advance(self, out: Outputs, hvac: HvacCommand, dt: float, faults: FaultInjector) -> SiteContext:
        p, b, hp = self.p, self.p.building, self.p.heat_pump
        w = self.weather.at(self.elapsed_s)
        local = _local(w.timestamp, self.tz)
        outdoor, ghi = w.temperature_c, w.irradiance_wpm2
        self.tank_c = outdoor  # the tank stands outside, in the sun and the cold
        grid_ok = p.grid_connected and not faults.is_on("grid_outage")
        outage = p.grid_connected and not grid_ok

        pv = p.pv.output(ghi, outdoor, w.timestamp, p.latitude, p.longitude)
        load = b.electric_load_w(local.hour)

        # ── Comfort: thermal power to hold the setpoint ───────────────────────
        # Feed-forward (what the air is losing or gaining right now) plus a
        # proportional term that reaches the heat pump's full capacity 1 K
        # away from the setpoint, like a thermostat during a recovery.
        free = b.air_free_flow(self.indoor_c, self.mass_c, outdoor)
        gain = hp.rated_heat_w  # W per kelvin of error
        need = 0.0
        if hvac.mode == "heat":
            need = max(0.0, -free + gain * (hvac.setpoint_c - self.indoor_c))
        elif hvac.mode == "cool":
            need = max(0.0, free + gain * (self.indoor_c - hvac.setpoint_c))
        reserve_pct = 1.0 if hvac.emergency else p.h2_reserve_pct
        h2_free = max(0.0, self.h2_kg - reserve_pct / 100.0 * p.tank.capacity_kg)
        use_h2 = (hvac.allow_h2 or (outage and p.h2_use != "never")) and h2_free > 0.0

        cop = hp.cop_heating(outdoor) if hvac.mode == "heat" else hp.eer_cooling(outdoor)

        # Battery limits for this step (output side).
        eff = math.sqrt(p.battery_round_trip)
        reserve_kwh = p.battery_reserve * p.battery_kwh
        battery_out_max = min(
            p.battery_power_w, max(0.0, self.battery_kwh - reserve_kwh) * J_PER_KWH * eff / dt
        )
        battery_in_max = min(
            p.battery_power_w, max(0.0, p.battery_kwh - self.battery_kwh) * J_PER_KWH / eff / dt
        )

        # The hydrogen boiler takes over only the heat the heat pump could not
        # make from the building's own PV and battery: burning hydrogen while
        # the sun could run the heat pump at a COP of 3-4 would waste it. It
        # adds to a heat pump at full power only in emergency heating, not to
        # warm up faster.
        boiler_heat = 0.0
        if hvac.mode == "heat" and p.end_use == "boiler" and use_h2:
            own_w = max(0.0, pv + battery_out_max - load)
            hp_part = min(need, hp.rated_heat_w)
            from_grid = max(0.0, hp_part / cop - own_w) * cop
            if hvac.emergency:
                from_grid += need - hp_part
            boiler_heat = min(from_grid, p.boiler.rated_w)
        need = min(need, hp.rated_heat_w + boiler_heat)

        # Fuel cell sized to the shortfall the battery cannot cover; its heat
        # (when heating) lowers what the heat pump has to deliver.
        fc_el = fc_heat = 0.0
        hp_th = need - boiler_heat
        demand = load
        for _ in range(3):
            hp_th = max(0.0, need - boiler_heat - (fc_heat if hvac.mode == "heat" else 0.0))
            demand = load + (hp_th / cop if hvac.mode != "off" else 0.0)
            if use_h2 and p.end_use == "fuel_cell":
                shortfall = demand - pv - battery_out_max
                fc_el = min(p.fuel_cell.rated_w, max(0.0, shortfall))
                if fc_el < p.fuel_cell.min_load_fraction * p.fuel_cell.rated_w:
                    fc_el = 0.0
            fc_heat = p.fuel_cell.run(fc_el)[1] if hvac.mode == "heat" else 0.0
        # Settle the heat pump on the fuel cell's final heat, and the demand with it.
        hp_th = max(0.0, need - boiler_heat - (fc_heat if hvac.mode == "heat" else 0.0))
        hp_el = hp_th / cop if hvac.mode != "off" else 0.0
        demand = load + hp_el

        # Hydrogen drawn by the fuel cell and the boiler, within what is free.
        fc_h2 = p.fuel_cell.run(fc_el)[0] * dt
        boiler_h2 = p.boiler.run(boiler_heat) * dt
        if fc_h2 + boiler_h2 > h2_free > 0.0:
            scale = h2_free / (fc_h2 + boiler_h2)
            fc_el, fc_heat, boiler_heat = fc_el * scale, fc_heat * scale, boiler_heat * scale
            fc_h2, boiler_h2 = fc_h2 * scale, boiler_h2 * scale

        # ── Electricity balance ──────────────────────────────────────────────
        net = pv + fc_el - demand
        battery_in = battery_out = grid = curtailed = unserved = 0.0
        stack_point = StackPoint(0.0, 0.0, reversible_voltage(self.plant.electrolyte_c), 0.0, 0.0, 0.0)
        running = out.electrolyser and out.h2_relay_closed
        stack_min = p.bench.stack.min_power_w
        self.export_budget_kwh += p.export_cap_share * pv * dt / J_PER_KWH
        skipped: list[str] = []
        if net >= 0.0:
            surplus = net
            for destination in p.surplus_order():
                if destination == "battery":
                    battery_in = min(surplus, battery_in_max)
                    surplus -= battery_in
                    if battery_in_max < 1.0 and p.battery_kwh > 0:
                        skipped.append("battery full")
                elif destination == "export":
                    allowance = self.export_budget_kwh * J_PER_KWH / dt
                    exported = min(surplus, p.export_limit_w, allowance) if grid_ok else 0.0
                    grid = -exported
                    surplus -= exported
                    self.export_budget_kwh -= exported * dt / J_PER_KWH
                    if surplus > 1.0 and grid_ok and allowance <= p.export_limit_w:
                        # Sales may not pass a share of what the panels made so far.
                        skipped.append(f"grid share of {p.export_cap_share:.0%} of production reached")
                    elif surplus > 1.0 and grid_ok:
                        skipped.append(f"export limit {p.export_limit_w / 1000:g} kW reached")
                    elif not grid_ok and p.grid_connected:
                        skipped.append("grid down")
                else:
                    # A cloud: lift a running stack back to its minimum from
                    # the battery (never at night, never while it charges).
                    support = 0.0
                    if running and pv >= CLOUD_FLOOR_W and surplus < stack_min and battery_in == 0.0:
                        support = min(battery_out_max, stack_min - surplus)
                    available = surplus + support
                    stack_point = self.plant.advance(out, available, outdoor, dt, faults)
                    used = stack_point.power_w
                    battery_out += max(0.0, used - surplus)
                    surplus = max(0.0, surplus - used)
                    self.available_w = available  # what the stack could draw this step
                    if used == 0.0 and available > 1.0:
                        skipped.append(self._stack_idle_reason(out, available, stack_min))
            curtailed = surplus
        else:
            deficit = -net
            battery_out = min(deficit, battery_out_max)
            deficit -= battery_out
            if grid_ok:
                grid = deficit
            else:
                unserved = deficit
            self.plant.advance(out, 0.0, outdoor, dt, faults)  # no power for the stack
            self.available_w = 0.0

        # Unserved electricity sheds heating and cooling first.
        hvac_delivered = hp_th
        if unserved > 0.0 and hp_el > 0.0:
            shed = min(hp_el, unserved)
            hvac_delivered = hp_th * (1.0 - shed / hp_el)

        # ── Storage ──────────────────────────────────────────────────────────
        self.battery_kwh += (battery_in * eff - battery_out / eff) * dt / J_PER_KWH
        self.battery_kwh = min(p.battery_kwh, max(0.0, self.battery_kwh))
        produced = stack_point.h2_kg_per_s * dt
        self.h2_kg = max(0.0, self.h2_kg + produced - fc_h2 - boiler_h2)
        vented = p.tank.relief(self.h2_kg, self.tank_c)
        self.h2_kg -= vented

        # ── Building ─────────────────────────────────────────────────────────
        if hvac.mode == "heat":
            hvac_w = hvac_delivered + boiler_heat + fc_heat
        elif hvac.mode == "cool":
            hvac_w = -hvac_delivered
        else:
            hvac_w = 0.0
        self.indoor_c, self.mass_c = b.step(self.indoor_c, self.mass_c, outdoor, ghi, hvac_w, dt)
        self.elapsed_s += dt

        # ── Totals ───────────────────────────────────────────────────────────
        t, k = self.totals, dt / J_PER_KWH
        t["pv_kwh"] += pv * k
        t["load_kwh"] += load * k
        t["heat_pump_kwh"] += hp_el * k
        t["fuel_cell_kwh"] += fc_el * k
        t["grid_import_kwh"] += max(0.0, grid) * k
        t["grid_export_kwh"] += max(0.0, -grid) * k
        t["curtailed_kwh"] += curtailed * k
        t["unserved_kwh"] += unserved * k
        t["heating_kwh"] += max(0.0, hvac_w) * k
        t["cooling_kwh"] += max(0.0, -hvac_w) * k
        t["h2_used_kg"] += fc_h2 + boiler_h2
        t["h2_vented_kg"] += vented

        return SiteContext(
            source="simulation",
            timestamp=w.timestamp,
            local_time=local.strftime("%Y-%m-%d %H:%M"),
            outdoor_c=round(outdoor, 1),
            humidity_pct=round(w.humidity_pct, 1),
            irradiance_wpm2=round(ghi, 1),
            pv_w=round(pv, 1),
            electrolyser_w=round(stack_point.power_w, 1),
            available_w=round(self.available_w, 1),
            load_w=round(load, 1),
            heat_pump_w=round(hp_el, 1),
            fuel_cell_w=round(fc_el, 1),
            battery_w=round(battery_in - battery_out, 1),
            battery_soc_pct=round(100.0 * self.battery_kwh / p.battery_kwh, 1) if p.battery_kwh > 0 else None,
            grid_w=round(grid, 1),
            curtailed_w=round(curtailed, 1),
            h2_tank_kg=round(self.h2_kg, 4),
            h2_tank_pct=round(self.tank_pct, 2),
            tank_bar=round(self.tank_bar, 2),
            tank_mawp_bar=p.tank.mawp_bar,
            h2_vented_kg=round(t["h2_vented_kg"], 4),
            indoor_c=round(self.indoor_c, 2),
            hvac_mode=hvac.mode,
            hvac_w=round(hvac_w, 1),
            fuel_cell_heat_w=round(fc_heat, 1),
            boiler_w=round(boiler_heat, 1),
            end_use=p.end_use,
            room_h2_ppm=round(self.plant.room_h2_ppm, 1),
            dispatch=self._explain(
                net,
                battery_in,
                battery_out,
                grid,
                stack_point.power_w,
                fc_el,
                boiler_heat,
                curtailed,
                unserved,
                skipped,
                hvac,
                use_h2,
            ),
        )

    # ── Saying what happened ─────────────────────────────────────────────────

    def _stack_idle_reason(self, out: Outputs, available: float, stack_min: float) -> str:
        if self.tank_pct >= 98.0 or "storage at" in out.reason:
            return "hydrogen tank full"
        if not out.electrolyser:
            if available < 1.2 * stack_min:
                return f"too little left for the stack (it needs {1.2 * stack_min / 1000:.1f} kW to start)"
            return out.reason[:1].lower() + out.reason[1:] if out.reason else "stack not running"
        return f"below the stack's minimum ({stack_min / 1000:.1f} kW)"

    def _explain(
        self,
        net: float,
        battery_in: float,
        battery_out: float,
        grid: float,
        stack_w: float,
        fc_el: float,
        boiler_heat: float,
        curtailed: float,
        unserved: float,
        skipped: list[str],
        hvac: HvacCommand,
        use_h2: bool,
    ) -> str:
        """One sentence: where the power went this step, and why."""

        def kw(w: float) -> str:
            return f"{w / 1000:.1f} kW"

        # net counts the fuel cell, which follows the shortfall: when it runs and
        # nothing is left over, the building is short, not balanced.
        if net >= 0.0 and not (fc_el > 1.0 and net < 50.0):
            if net < 50.0 and stack_w < 1.0:
                return "The sun just covers the building: nothing to store."
            parts = []
            for destination in self.p.surplus_order():
                if destination == "battery" and battery_in > 1.0:
                    parts.append(f"battery {kw(battery_in)}")
                elif destination == "export" and grid < -1.0:
                    parts.append(f"grid {kw(-grid)}")
                elif destination == "hydrogen" and stack_w > 1.0:
                    parts.append(f"hydrogen {kw(stack_w)}")
            if curtailed > 1.0:
                parts.append(f"curtailed {kw(curtailed)}")
            sentence = (
                f"Surplus {kw(net + battery_out)}: " + ", ".join(parts) if parts else f"Surplus {kw(net)}"
            )
            if skipped:
                sentence += " (" + "; ".join(dict.fromkeys(skipped)) + ")"
            return sentence + "."
        parts = []
        if battery_out > 1.0:
            parts.append(f"battery {kw(battery_out)}")
        if fc_el > 1.0:
            parts.append(f"fuel cell {kw(fc_el)}")
        if boiler_heat > 1.0:
            parts.append(f"hydrogen boiler {kw(boiler_heat)} of heat")
        if grid > 1.0:
            parts.append(f"grid {kw(grid)}")
        if unserved > 1.0:
            parts.append(f"not served {kw(unserved)}")
        sentence = f"Shortfall {kw(-net + fc_el)}: " + ", ".join(parts)
        if not use_h2 and self.h2_kg > 0.0 and grid > 1.0:
            if self.p.h2_use == "never":
                sentence += " (hydrogen use is switched off)"
            elif hvac.mode != "heat":
                sentence += " (hydrogen kept for the heating season)"
        return sentence + "."
