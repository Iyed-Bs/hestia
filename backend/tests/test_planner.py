"""The planner gives consistent, honest numbers, with the simulator's physics."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hestia.planner.model import PlannerInputs, hourly_needs, plan
from hestia.planner.presets import PRESETS
from hestia.twin.weather import HourlyYear, bundled_year


@pytest.fixture(scope="module")
def result() -> dict:
    return plan(PlannerInputs(), bundled_year(), "bundled")


def test_energy_balances_close(result: dict) -> None:
    for name, scenario in result["scenarios"].items():
        f = scenario["flows"]
        pv_out = (
            f["direct_kwh"]
            + f["battery_in_kwh"]
            + f["electrolyser_kwh"]
            + f["export_kwh"]
            + f["curtailed_kwh"]
        )
        assert pv_out == pytest.approx(f["pv_kwh"], abs=0.1), name  # each flow is rounded to 0.01
        used = f["load_kwh"] + f["hvac_kwh"]
        supplied = f["direct_kwh"] + f["battery_out_kwh"] + f["fuel_cell_kwh"] + f["import_kwh"]
        assert used == pytest.approx(supplied, abs=0.1), name
        heat = f["heat_pump_heat_kwh"] + f["gas_heat_kwh"] + f["h2_heat_kwh"]
        assert heat == pytest.approx(f["heat_kwh"], abs=0.1), name


def test_a_steady_year_uses_what_it_makes(result: dict) -> None:
    f = result["scenarios"]["hestia"]["flows"]
    assert f["h2_used_kg"] == pytest.approx(f["h2_produced_kg"], rel=0.05)
    assert all(0.0 <= kg <= 10.0 + 1e-9 for kg in f["monthly_tank_kg"])


def test_pv_yield_is_the_simulators() -> None:
    # Same panels, same sun, same losses as physics/solar.py (≈ 1 530 kWh/kWp in Tunis at 30°).
    hours = hourly_needs(PlannerInputs(), bundled_year())
    assert 1450 < sum(hours.pv_per_kwp) < 1600


def test_appliances_follow_local_time() -> None:
    # Two days from midnight UTC. The evening peak is 19:00-20:00 local time:
    # 18:00-19:00 UTC in Tunis (UTC+1), the weather hour labelled 19:00 UTC.
    day = HourlyYear(datetime(2026, 1, 15, tzinfo=UTC), (12.0,) * 48, (70.0,) * 48, (0.0,) * 48)
    tunis = hourly_needs(PlannerInputs(), day).load[:24]
    assert tunis.index(max(tunis)) == 19
    # Paris in summer is UTC+2: the same local peak comes one weather hour earlier.
    summer = HourlyYear(datetime(2026, 7, 15, tzinfo=UTC), (25.0,) * 48, (50.0,) * 48, (0.0,) * 48)
    paris = hourly_needs(PlannerInputs(timezone="Europe/Paris"), summer).load[:24]
    assert paris.index(max(paris)) == 18


def test_more_equipment_never_lowers_self_sufficiency(result: dict) -> None:
    s = {k: v["economics"]["self_sufficiency"] for k, v in result["scenarios"].items()}
    assert s["today"] <= s["solar"] <= s["solar_battery"] <= s["hestia"]


def test_hydrogen_shifts_summer_energy_to_winter(result: dict) -> None:
    f = result["scenarios"]["hestia"]["flows"]
    tank, used = f["monthly_tank_kg"], f["monthly_h2_kwh"]
    assert max(tank[5:9]) > min(tank[0], tank[1], tank[11])
    # Kept for the heating season: summer use only when the tank is nearly full.
    assert sum(used[0:3]) + sum(used[10:12]) > 5 * sum(used[5:9])


def test_with_a_heat_pump_the_fuel_cell_beats_the_hydrogen_boiler(result: dict) -> None:
    boiler = plan(PlannerInputs(end_use="boiler"), bundled_year(), "bundled")
    fc = result["scenarios"]["hestia"]["economics"]["self_sufficiency"]
    assert boiler["scenarios"]["hestia"]["economics"]["self_sufficiency"] < fc
    assert "weaker end use" in " ".join(boiler["verdict"])


def test_hydrogen_replaces_gas_in_a_gas_heated_building() -> None:
    x = PlannerInputs(heating="gas_boiler", end_use="boiler", cooling=False)
    r = plan(x, bundled_year(), "bundled")
    today, h = r["scenarios"]["today"]["flows"], r["scenarios"]["hestia"]["flows"]
    assert h["h2_heat_kwh"] > 0
    assert h["gas_heat_kwh"] == pytest.approx(today["gas_heat_kwh"] - h["h2_heat_kwh"], abs=0.1)


def test_conversion_chain_keeps_about_half(result: dict) -> None:
    chain = result["chain"]
    assert 0.5 < chain["to_hydrogen"] < 0.6  # ≈ 60 kWh/kg at rated power
    assert 0.45 <= chain["useful"] <= 0.6
    assert chain["electricity"] == pytest.approx(0.45 * chain["to_hydrogen"], abs=0.002)


def test_the_tank_is_given_its_real_size(result: dict) -> None:
    assert 4.0 < result["tank_volume_m3"] < 5.5  # 10 kg at 30 bar ≈ 4.8 m³ of water volume


def test_verdict_is_honest_about_money(result: dict) -> None:
    text = " ".join(result["verdict"])
    if (
        result["scenarios"]["hestia"]["economics"]["npv"]
        < result["scenarios"]["solar_battery"]["economics"]["npv"]
    ):
        assert "On money alone" in text


def test_recommendation_comes_from_the_sweep(result: dict) -> None:
    rec = result["recommended"]
    assert any(
        r["electrolyser_kw"] == rec["electrolyser_kw"] and r["tank_kg"] == rec["tank_kg"]
        for r in result["sweep"]
    )


def test_hydrogen_never_takes_what_the_grid_could_take_in_tunis() -> None:
    # Exporting avoids three times more CO₂ than hydrogen there: with no limit
    # on what may be sold, the electrolyser has nothing to do.
    x = PlannerInputs(export_cap_share=1.0)
    f = plan(x, bundled_year(), "bundled")["scenarios"]["hestia"]["flows"]
    assert f["h2_produced_kg"] == 0.0


def test_where_the_grid_takes_nothing_hydrogen_turns_waste_into_comfort() -> None:
    r = plan(PlannerInputs(export_cap_share=0.0), bundled_year(), "bundled")
    sb, h = r["scenarios"]["solar_battery"], r["scenarios"]["hestia"]
    assert h["flows"]["curtailed_kwh"] < sb["flows"]["curtailed_kwh"]
    assert h["economics"]["self_sufficiency"] > sb["economics"]["self_sufficiency"] + 0.03
    assert r["surplus_order"] == ["battery", "hydrogen", "export"]


def test_tunisian_export_cap_is_respected() -> None:
    x = PlannerInputs(pv_kwp=12, battery_kwh=0, electrolyser_kw=0, tank_kg=0)
    f = plan(x, bundled_year(), "bundled")["scenarios"]["solar"]["flows"]
    assert f["export_kwh"] <= 0.30 * f["pv_kwh"] + 1e-6
    assert f["curtailed_kwh"] > 0


def test_incoherent_inputs_are_refused() -> None:
    with pytest.raises(ValidationError):
        PlannerInputs(heat_setpoint_c=22, cool_setpoint_c=23)
    with pytest.raises(ValidationError):
        PlannerInputs(timezone="Mars/Olympus")
    with pytest.raises(ValidationError):
        PlannerInputs(fuel_cell_electrical_efficiency=0.6, fuel_cell_thermal_efficiency=0.5)


@pytest.mark.parametrize("name", list(PRESETS))
def test_presets_are_valid_inputs(name: str) -> None:
    fields = {k: v for k, v in PRESETS[name].items() if k in PlannerInputs.model_fields}
    PlannerInputs(preset=name, **fields)
