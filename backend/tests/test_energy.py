"""The energy manager's decisions, one rule at a time."""

from __future__ import annotations

from dataclasses import replace

from hestia.domain.energy import EnergyInputs, EnergyManager

SUNNY = EnergyInputs(
    now_s=0.0,
    available_w=1500.0,
    stack_min_w=400.0,
    electrolyser_running=False,
    h2_sensor_trusted=True,
    h2_tank_pct=40.0,
    indoor_c=22.0,
    outdoor_c=24.0,
    cooling_available=True,
    h2_use="grid_backup",
)


# ── Production permit ────────────────────────────────────────────────────────


def test_start_needs_a_margin_above_the_stack_minimum() -> None:
    assert not EnergyManager().decide(replace(SUNNY, available_w=450.0)).permit  # < 1.2 × 400
    assert EnergyManager().decide(replace(SUNNY, available_w=480.0)).permit


def test_a_running_stack_stops_below_its_minimum() -> None:
    m = EnergyManager()
    m.decide(SUNNY)
    d = m.decide(replace(SUNNY, now_s=1000.0, electrolyser_running=True, available_w=380.0))
    assert not d.permit and "minimum load" in d.permit_reason


def test_a_new_run_is_held_for_the_minimum_on_time_and_says_so() -> None:
    m = EnergyManager()
    assert m.decide(SUNNY).permit
    d = m.decide(replace(SUNNY, now_s=60.0, electrolyser_running=True, ml_production_level=0))
    assert d.permit
    assert "at least 300 s" in d.permit_reason and "240 s left" in d.permit_reason


def test_the_stack_rests_between_runs_and_says_so() -> None:
    m = EnergyManager()
    m.decide(SUNNY)
    m.decide(replace(SUNNY, now_s=400.0, electrolyser_running=True, ml_production_level=0))  # stops
    d = m.decide(replace(SUNNY, now_s=450.0))
    assert not d.permit and "rests at least 200 s" in d.permit_reason


def test_a_bench_on_its_lab_supply_ignores_solar_advice() -> None:
    bench = replace(SUNNY, fixed_supply=True, ml_production_level=0)
    d = EnergyManager().decide(bench)
    assert d.permit and "bench supply" in d.permit_reason
    assert not EnergyManager().decide(replace(bench, fixed_supply=False)).permit


def test_hydrogen_in_the_room_withdraws_the_permit_at_once() -> None:
    m = EnergyManager()
    m.decide(SUNNY)
    d = m.decide(replace(SUNNY, now_s=10.0, electrolyser_running=True, gas_detected=True))
    assert not d.permit and "Hydrogen detected" in d.permit_reason


def test_safety_reasons_are_never_held() -> None:
    m = EnergyManager()
    m.decide(SUNNY)
    d = m.decide(
        replace(SUNNY, now_s=10.0, electrolyser_running=True, h2_sensor_trusted=False, h2_sensor_why="frozen")
    )
    assert not d.permit and "not trusted" in d.permit_reason


def test_a_full_tank_stops_production_until_it_drops_below_90_percent() -> None:
    m = EnergyManager()
    assert not m.decide(replace(SUNNY, h2_tank_pct=98.0)).permit
    assert not m.decide(replace(SUNNY, h2_tank_pct=93.0)).permit  # hysteresis
    assert m.decide(replace(SUNNY, h2_tank_pct=89.0)).permit


# ── Comfort ──────────────────────────────────────────────────────────────────


def test_heating_has_hysteresis_around_the_setpoint() -> None:
    m = EnergyManager()
    cold = replace(SUNNY, outdoor_c=8.0)
    assert m.decide(replace(cold, indoor_c=19.4)).hvac.mode == "heat"
    assert m.decide(replace(cold, indoor_c=20.3)).hvac.mode == "heat"  # still inside the band
    assert m.decide(replace(cold, indoor_c=20.6)).hvac.mode == "off"


def test_cooling_only_where_a_heat_pump_can_cool() -> None:
    hot = replace(SUNNY, outdoor_c=36.0, indoor_c=27.0)
    assert EnergyManager().decide(hot).hvac.mode == "cool"
    assert EnergyManager().decide(replace(hot, cooling_available=False)).hvac.mode == "off"


def test_no_heating_when_it_is_warm_outside() -> None:
    d = EnergyManager().decide(replace(SUNNY, indoor_c=19.0, outdoor_c=31.0))
    assert d.hvac.mode == "off" and "Warm outside" in d.hvac.reason


def test_hydrogen_use_follows_the_policy_and_keeps_a_reserve() -> None:
    cold = replace(SUNNY, outdoor_c=5.0, indoor_c=18.0)
    assert EnergyManager().decide(cold).hvac.allow_h2
    assert not EnergyManager().decide(replace(cold, h2_use="never")).hvac.allow_h2
    hot = replace(SUNNY, outdoor_c=36.0, indoor_c=28.0)
    assert not EnergyManager().decide(replace(hot, h2_use="heating_only")).hvac.allow_h2
    assert not EnergyManager().decide(replace(cold, h2_tank_pct=4.0)).hvac.allow_h2


def test_emergency_heating_may_use_the_reserve() -> None:
    d = EnergyManager().decide(replace(SUNNY, outdoor_c=2.0, indoor_c=12.0, h2_tank_pct=3.0))
    assert d.hvac.mode == "heat" and d.hvac.emergency and d.hvac.allow_h2
