"""The twin and the simulator behave like the physical installation should."""

from __future__ import annotations

from dataclasses import replace

import pytest

from hestia.domain.commands import SetActuator, SetMode
from hestia.domain.control import COOL_AT_C, REGULATE_OFF_C, REGULATE_ON_C, Mode, Phase
from hestia.domain.energy import EnergyInputs, EnergyManager
from hestia.domain.telemetry import SiteContext, Telemetry
from hestia.twin.device import VirtualDevice
from hestia.twin.params import SiteProfile
from hestia.twin.plant import BenchModel, SiteModel

Log = list[tuple[Telemetry | None, SiteContext]]


def drive(device: VirtualDevice, hours: float, dt: float = 30.0) -> Log:
    """Run the device with the energy manager in the loop, as the gateway engine does."""
    manager = EnergyManager()
    model = device.model
    profile = model.p if isinstance(model, SiteModel) else None
    log: Log = []
    for _ in range(int(hours * 3600 / dt)):
        telemetry, site = device.cycle(dt)
        decision = manager.decide(
            EnergyInputs(
                now_s=device.uptime_s,
                available_w=site.available_w,
                stack_min_w=device.plant.p.stack.min_power_w,
                electrolyser_running=bool(telemetry and telemetry.electrolyser),
                h2_sensor_trusted=True,
                h2_tank_pct=site.h2_tank_pct,
                indoor_c=site.indoor_c,
                outdoor_c=site.outdoor_c,
                cooling_available=profile is not None,
                h2_use=profile.h2_use if profile else "never",
            )
        )
        device.set_permit(decision.permit)
        device.set_hvac(decision.hvac)
        log.append((telemetry, site))
    return log


def bench() -> VirtualDevice:
    return VirtualDevice(BenchModel(seed=1))


def site(
    profile: SiteProfile | None = None, *, month: int = 7, day: int = 10, hour: float = 6.0
) -> VirtualDevice:
    device = VirtualDevice(SiteModel(profile, seed=1))
    device.jump_to(month, day, hour)
    return device


def telemetry_of(log: Log) -> list[Telemetry]:
    return [t for t, _ in log if t is not None]


# ── The bench (échantillon) ──────────────────────────────────────────────────


def test_the_cooling_loop_holds_the_stack_while_it_produces() -> None:
    log = telemetry_of(drive(bench(), hours=8))
    warm = [t for t in log if t.uptime_s > 3 * 3600]  # after the warm-up from room temperature
    assert {t.phase for t in log} == {Phase.ELECTROLYSIS}
    assert all(t.electrolyser for t in warm)
    assert all(REGULATE_OFF_C - 1.0 < t.electrolyte_c < REGULATE_ON_C + 1.0 for t in warm)
    assert any(t.cooling_pump for t in warm) and not all(t.cooling_pump for t in warm)


def test_a_failed_cooling_pump_makes_the_stack_stop_and_cool() -> None:
    device = bench()
    device.set_fault("cooling_failure", True)
    log = telemetry_of(drive(device, hours=10))
    assert {t.phase for t in log} == {Phase.ELECTROLYSIS, Phase.COOLING, Phase.CONDITIONING}
    assert COOL_AT_C - 0.5 < max(t.electrolyte_c for t in log) < COOL_AT_C + 2.0
    assert not any(t.electrolyser for t in log if t.phase is Phase.COOLING)


def test_water_makeup_keeps_level_and_koh_strength() -> None:
    device = bench()
    log = telemetry_of(drive(device, hours=30))
    assert any(t.water_makeup for t in log)
    assert all(24.0 < t.koh_wt_pct < 33.0 for t in log)
    assert device.plant.totals["water_l"] > 1.0


def test_small_leak_is_held_below_the_alarm_by_extraction() -> None:
    device = bench()
    device.set_fault("leak_small", True)
    log = telemetry_of(drive(device, hours=1.5, dt=10))
    assert any(t.h2_warning and t.ventilation for t in log)
    assert not any(t.h2_alarm_latched for t in log)


def test_large_leak_latches_the_alarm_and_extraction_keeps_running() -> None:
    device = bench()
    device.set_fault("leak_large", True)
    log = telemetry_of(drive(device, hours=0.5, dt=2))
    latched = [t for t in log if t.h2_alarm_latched]
    assert latched
    assert all(t.ventilation and not t.h2_relay_closed and not t.electrolyser for t in latched)
    assert device.plant.room_h2_ppm < 0.1 * 40_000  # extraction keeps the room below 10 % of the LEL


def test_a_broken_extraction_fan_lets_a_small_leak_reach_the_alarm() -> None:
    device = bench()
    device.set_fault("leak_small", True)
    device.set_fault("fan_failure", True)
    log = telemetry_of(drive(device, hours=1.5, dt=10))
    assert any(t.h2_alarm_latched for t in log)


def test_manual_run_with_a_failed_cooling_pump_hits_the_over_temperature_stop() -> None:
    device = bench()
    device.set_fault("cooling_failure", True)
    device.apply(SetMode(mode=Mode.MANUAL))
    device.apply(SetActuator(actuator="electrolyser", on=True))
    device.apply(SetActuator(actuator="cooling_pump", on=True))
    log = telemetry_of(drive(device, hours=10))
    stopped = [t for t in log if t.electrolyte_c >= t.temp_alert_c]
    assert stopped and not any(t.electrolyser for t in stopped)
    assert max(t.electrolyte_c for t in log) < 72.0


# ── The whole site (simulator) ───────────────────────────────────────────────


def _balance(s: SiteContext) -> float:
    """Sources minus uses of electricity for one step (W)."""
    assert s.battery_w is not None and s.grid_w is not None
    sources = s.pv_w + (s.fuel_cell_w or 0) + max(0.0, s.grid_w) + max(0.0, -s.battery_w)
    uses = (
        (s.load_w or 0)
        + (s.heat_pump_w or 0)
        + s.electrolyser_w
        + max(0.0, s.battery_w)
        + max(0.0, -s.grid_w)
        + (s.curtailed_w or 0)
    )
    return sources - uses


def test_summer_day_fills_storage_and_every_watt_is_accounted_for() -> None:
    device = site(hour=5.0)
    start = device.model.h2_kg  # type: ignore[attr-defined]
    log = drive(device, hours=14)
    assert device.model.h2_kg > start + 0.05  # type: ignore[attr-defined]
    assert all(abs(_balance(s)) < 1.0 for _, s in log)


def test_production_stops_near_the_tank_pressure_limit_and_the_relief_valve_vents_above_it() -> None:
    device = site(hour=11.0)
    model = device.model
    assert isinstance(model, SiteModel)
    device.cycle(10)  # the tank takes the outdoor temperature
    model.h2_kg = 1.08 * model.p.tank.capacity_kg  # ~29 bar on a hot day
    telemetry, _ = device.cycle(10)
    assert telemetry is not None and not telemetry.electrolyser and "storage at" in telemetry.reason
    model.h2_kg = 1.2 * model.p.tank.capacity_kg  # above the maximum working pressure
    _, s = device.cycle(10)
    assert s.h2_vented_kg and s.h2_vented_kg > 0
    assert s.tank_bar is not None and s.tank_bar <= model.p.tank.mawp_bar + 0.05


def test_winter_night_heats_with_the_fuel_cell() -> None:
    device = site(month=1, day=15, hour=21.0)
    model = device.model
    assert isinstance(model, SiteModel)
    model.indoor_c = model.mass_c = 17.0
    model.battery_kwh = model.p.battery_reserve * model.p.battery_kwh  # battery already at its reserve
    model.h2_kg = 0.5 * model.p.tank.capacity_kg
    log = drive(device, hours=4)
    assert any((s.fuel_cell_w or 0) > 0 for _, s in log)
    assert model.totals["h2_used_kg"] > 0
    assert model.indoor_c > 19.0


def test_the_boiler_end_use_burns_hydrogen_for_heat() -> None:
    device = site(SiteProfile(end_use="boiler"), month=1, day=15, hour=21.0)
    model = device.model
    assert isinstance(model, SiteModel)
    model.indoor_c = model.mass_c = 17.0
    model.battery_kwh = model.p.battery_reserve * model.p.battery_kwh
    model.h2_kg = 0.5 * model.p.tank.capacity_kg
    log = drive(device, hours=3)
    assert any((s.boiler_w or 0) > 0 for _, s in log)
    assert all((s.fuel_cell_w or 0) == 0 for _, s in log)
    assert model.indoor_c > 19.0


def test_the_boiler_leaves_sunny_hours_to_the_heat_pump() -> None:
    device = site(SiteProfile(end_use="boiler"), month=1, day=15, hour=11.0)
    model = device.model
    assert isinstance(model, SiteModel)
    model.indoor_c = model.mass_c = 18.0
    model.battery_kwh = model.p.battery_kwh  # full battery, winter noon
    model.h2_kg = 0.5 * model.p.tank.capacity_kg
    log = drive(device, hours=1)
    assert any(s.hvac_mode == "heat" for _, s in log)
    assert all((s.boiler_w or 0) == 0 for _, s in log)


def test_a_heat_wave_is_cooled_by_the_heat_pump() -> None:
    device = site(month=7, day=28, hour=13.0)
    model = device.model
    assert isinstance(model, SiteModel)
    model.indoor_c = model.mass_c = 29.0
    log = drive(device, hours=3)
    assert any(s.hvac_mode == "cool" and (s.heat_pump_w or 0) > 0 for _, s in log)
    assert model.indoor_c < 27.5  # the air follows within the hour; hot walls slow the rest


def test_a_grid_outage_islands_the_building() -> None:
    device = site(replace(SiteProfile(), h2_use="never"), month=1, day=15, hour=21.0)
    model = device.model
    assert isinstance(model, SiteModel)
    model.battery_kwh = model.p.battery_reserve * model.p.battery_kwh
    device.set_fault("grid_outage", True)
    log = drive(device, hours=2)
    assert all(s.grid_w == 0 for _, s in log)
    assert model.totals["unserved_kwh"] > 0


@pytest.mark.parametrize("month", [1, 4, 7, 10])
def test_values_stay_physical_over_three_days(month: int) -> None:
    device = site(month=month, day=5, hour=0.0)
    for t, s in drive(device, hours=72, dt=60):
        assert s.indoor_c is not None and 5.0 < s.indoor_c < 40.0
        assert s.battery_soc_pct is not None and -0.01 <= s.battery_soc_pct <= 100.01
        assert s.tank_bar is not None and 0.0 <= s.tank_bar <= 30.5
        if t is not None:
            assert 0.0 < t.electrolyte_c < 75.0
            assert 20.0 < t.koh_wt_pct < 36.0


def test_dropout_hides_telemetry_but_the_plant_keeps_going() -> None:
    device = site(hour=10.0)
    device.set_fault("dropout", True)
    telemetry, s = device.cycle(10)
    assert telemetry is None and s.pv_w >= 0.0
