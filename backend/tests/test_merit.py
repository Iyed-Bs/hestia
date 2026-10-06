"""Where surplus sunshine goes: nature first, then cost (domain/merit.py)."""

from __future__ import annotations

from dataclasses import replace

from hestia.domain.energy import EnergyInputs, EnergyManager
from hestia.domain.merit import SiteValues
from hestia.twin.device import VirtualDevice
from hestia.twin.params import SiteProfile
from hestia.twin.plant import SiteModel

TUNIS = SiteValues()
PARIS = SiteValues(grid_kgco2_per_kwh=0.0213, import_price=0.25, export_price=0.04)


def test_a_kwh_through_hydrogen_keeps_about_a_quarter_as_electricity() -> None:
    assert 0.53 < TUNIS.to_h2 < 0.58  # ≈ 60 kWh/kg at rated load
    kg, _ = TUNIS.per_kwh("hydrogen")
    assert kg < TUNIS.per_kwh("export")[0] / 3  # exporting avoids three times more CO₂ in Tunis


def test_the_order_depends_on_the_place() -> None:
    assert TUNIS.order() == ["battery", "export", "hydrogen"]
    assert PARIS.order() == ["battery", "hydrogen", "export"]  # clean grid, dear electricity


def test_with_gas_heating_hydrogen_heat_beats_a_clean_grid_on_co2() -> None:
    gas = replace(PARIS, heating="gas_boiler", gas_price=0.11, end_use="boiler")
    assert gas.per_kwh("hydrogen")[0] > 4 * gas.per_kwh("export")[0]


def test_without_export_hydrogen_comes_right_after_the_battery() -> None:
    assert replace(TUNIS, export_allowed=False).order()[:2] == ["battery", "hydrogen"]


def _summer_noon(profile: SiteProfile) -> tuple[VirtualDevice, SiteModel]:
    model = SiteModel(profile, seed=4)
    device = VirtualDevice(model)
    device.jump_to(7, 10, 6.0)
    model.battery_kwh = 0.3 * profile.battery_kwh
    return device, model


def _run(device: VirtualDevice, profile: SiteProfile, hours: float) -> list:
    manager, log = EnergyManager(), []
    for _ in range(int(hours * 60)):
        telemetry, site = device.cycle(60)
        d = manager.decide(
            EnergyInputs(
                now_s=device.uptime_s,
                available_w=site.available_w,
                stack_min_w=device.plant.p.stack.min_power_w,
                electrolyser_running=bool(telemetry and telemetry.electrolyser),
                h2_sensor_trusted=True,
                h2_tank_pct=site.h2_tank_pct,
                indoor_c=site.indoor_c,
                outdoor_c=site.outdoor_c,
                cooling_available=True,
                h2_use=profile.h2_use,
                otherwise_curtailed=profile.surplus_order()[-1] == "hydrogen",
            )
        )
        device.set_permit(d.permit)
        device.set_hvac(d.hvac)
        log.append(site)
    return log


def test_the_stack_takes_only_what_battery_and_grid_cannot() -> None:
    profile = SiteProfile()
    device, _ = _summer_noon(profile)
    log = _run(device, profile, 10)
    producing = [s for s in log if s.electrolyser_w > 0]
    assert producing, "the stack should run on a summer day with an 8 kWp roof"
    assert all((s.battery_soc_pct or 0) > 99.0 for s in producing)  # the battery filled first
    assert all(s.dispatch for s in log)
    assert any("hydrogen" in s.dispatch for s in producing)


def test_a_grid_outage_unlocks_hydrogen_even_in_summer() -> None:
    profile = SiteProfile()
    model = SiteModel(profile, seed=5)
    device = VirtualDevice(model)
    device.jump_to(7, 10, 21.0)
    model.battery_kwh = profile.battery_reserve * profile.battery_kwh
    model.h2_kg = 0.5 * profile.tank.capacity_kg
    device.set_fault("grid_outage", True)
    log = _run(device, profile, 2)
    assert any((s.fuel_cell_w or 0) > 0 for s in log)
    assert model.totals["unserved_kwh"] < 0.05


def test_the_dispatch_names_the_fuel_cell_when_it_carries_the_house() -> None:
    profile = SiteProfile()
    model = SiteModel(profile, seed=6)
    device = VirtualDevice(model)
    device.jump_to(1, 15, 18.0)  # a winter evening, battery at its reserve
    model.battery_kwh = profile.battery_reserve * profile.battery_kwh
    model.h2_kg = 0.6 * profile.tank.capacity_kg
    model.indoor_c = model.mass_c = 18.5  # the house needs heat
    log = _run(device, profile, 1)
    on_fuel_cell = [s for s in log if (s.fuel_cell_w or 0) > 50]
    assert on_fuel_cell
    assert all("fuel cell" in s.dispatch for s in on_fuel_cell)
    assert not any(s.dispatch.startswith("The sun just covers") for s in on_fuel_cell)
