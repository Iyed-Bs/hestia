"""The shared physics against values anyone can check in the cited sources."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from hestia.physics.building import Building
from hestia.physics.conversion import FuelCell, HeatPump
from hestia.physics.electrolyser import H2_HHV_KWH_PER_KG, U_THERMONEUTRAL, AlkalineStack
from hestia.physics.electrolyte import KohSolution, density_kg_per_l
from hestia.physics.solar import PvArray, erbs_diffuse_fraction, plane_of_array, sun_at
from hestia.physics.storage import Tank, pressure_bar
from hestia.twin.weather import bundled_year

TUNIS = (36.8, 10.2)


# ── Sun and PV ───────────────────────────────────────────────────────────────


def test_declination_at_equinox_and_solstice() -> None:
    assert abs(math.degrees(sun_at(datetime(2025, 3, 20, 12, tzinfo=UTC), *TUNIS).declination)) < 1.0
    assert math.degrees(sun_at(datetime(2025, 6, 21, 12, tzinfo=UTC), *TUNIS).declination) == pytest.approx(
        23.44, abs=0.3
    )


def test_sun_at_solar_noon_in_tunis_in_june() -> None:
    # Solar noon in Tunis ≈ 11:20 UTC (10.2° E); zenith ≈ latitude - declination = 13.4°.
    best = max(
        (sun_at(datetime(2025, 6, 21, 10, tzinfo=UTC) + timedelta(minutes=m), *TUNIS) for m in range(0, 120)),
        key=lambda s: s.cos_zenith,
    )
    assert math.degrees(math.acos(best.cos_zenith)) == pytest.approx(13.4, abs=0.4)


def test_erbs_correlation_limits_and_continuity() -> None:
    assert erbs_diffuse_fraction(0.0) == 1.0
    assert erbs_diffuse_fraction(0.9) == 0.165
    assert erbs_diffuse_fraction(0.2199) == pytest.approx(erbs_diffuse_fraction(0.2201), abs=0.002)
    assert erbs_diffuse_fraction(0.7999) == pytest.approx(erbs_diffuse_fraction(0.8001), abs=0.002)


def test_annual_yield_in_tunis_matches_published_range() -> None:
    """A 30° south-facing array in Tunis: PVGIS gives roughly 1600-1750 kWh/kWp/year."""
    year = bundled_year()
    array = PvArray(kwp=1.0)
    energy_wh = poa_wh = ghi_wh = 0.0
    for h in range(len(year)):
        # Each value is the mean of the preceding hour: evaluate the sun mid-hour.
        when = year.start + timedelta(hours=h) - timedelta(minutes=30)
        sun = sun_at(when, *TUNIS)
        ghi = year.irradiance_wpm2[h]
        poa = plane_of_array(ghi, sun, TUNIS[0], 30.0, 0.0)
        energy_wh += array.ac_power(poa, year.temperature_c[h])
        poa_wh += poa
        ghi_wh += ghi
    kwh_per_kwp = energy_wh / 1000.0 * 365.0 / (len(year) / 24.0)
    assert 1500.0 < kwh_per_kwp < 1850.0
    assert 1.05 < poa_wh / ghi_wh < 1.25  # tilting towards the sun gains 5-25 % at this latitude


def test_hot_panels_produce_less() -> None:
    array = PvArray(kwp=3.0)
    assert array.ac_power(1000.0, 40.0) < array.ac_power(1000.0, 10.0)


# ── Alkaline stack ───────────────────────────────────────────────────────────


def test_cell_voltage_is_typical_of_alkaline_stacks() -> None:
    stack = AlkalineStack()
    assert stack.cell_voltage(2000.0, 60.0) == pytest.approx(1.88, abs=0.05)
    assert stack.cell_voltage(2000.0, 25.0) > stack.cell_voltage(
        2000.0, 60.0
    )  # cold stacks are less efficient


def test_faraday_efficiency_collapses_at_low_load() -> None:
    stack = AlkalineStack()
    assert stack.faraday_efficiency(0.10 * stack.rated_current_density) == pytest.approx(0.82, abs=0.01)
    assert stack.faraday_efficiency(0.05 * stack.rated_current_density) < 0.60
    assert stack.faraday_efficiency(stack.rated_current_density) > 0.97


def test_rated_point_yield_heat_and_energy_balance() -> None:
    stack = AlkalineStack(rated_w=1000.0)
    point = stack.operate(1000.0, 60.0)
    kg_per_kwh = point.h2_kg_per_s * 3600.0
    assert 0.016 < kg_per_kwh < 0.020  # 50-60 kWh/kg: a real alkaline system
    assert 0.18 < point.heat_w / point.power_w < 0.30
    # Energy closes: DC input = heat + chemical energy of the hydrogen (HHV).
    chemical_w = point.h2_kg_per_s * H2_HHV_KWH_PER_KG * 3.6e6
    dc_w = stack.stack_dc_w(point.power_w)
    assert point.heat_w + chemical_w == pytest.approx(dc_w, rel=0.01)
    assert point.cell_voltage > U_THERMONEUTRAL


def test_running_far_below_rated_wastes_energy() -> None:
    """Faraday losses and the fixed auxiliaries make very low load expensive per kg."""
    stack = AlkalineStack(rated_w=1000.0)
    assert stack.operate(100.0, 60.0).kwh_per_kg > 1.15 * stack.operate(500.0, 60.0).kwh_per_kg
    assert stack.operate(30.0, 60.0).h2_kg_per_s == 0.0  # below the auxiliary draw nothing is made


# ── KOH electrolyte ──────────────────────────────────────────────────────────


def test_koh_properties_at_30_percent() -> None:
    assert density_kg_per_l(30.0, 25.0) == pytest.approx(1.29, abs=0.01)
    solution = KohSolution.mixed(20.0, 30.0)
    assert solution.molarity(25.0) == pytest.approx(6.9, abs=0.2)
    assert 0.8 < solution.conductivity(60.0) < 1.25  # S/cm, Gilliam et al. 2007


def test_electrolysis_concentrates_and_topping_up_restores() -> None:
    solution = KohSolution.mixed(20.0, 30.0)
    water = solution.electrolyse(0.2)  # 0.2 kg of H₂
    assert water == pytest.approx(1.787, abs=0.01)
    assert solution.wt_pct > 31.0
    solution.add_water(water)
    assert solution.wt_pct == pytest.approx(30.0, abs=0.01)


# ── Hydrogen tank ────────────────────────────────────────────────────────────


def test_real_gas_pressure_exceeds_ideal_gas() -> None:
    real = pressure_bar(2.0, 1.0, 15.0)
    ideal = 2.0 * 4124.2 * 288.15 / 1e5
    assert real > ideal
    assert real == pytest.approx(ideal, rel=0.03)  # a few % at 25-30 bar


def test_tank_capacity_is_rated_hot_and_relief_vents_excess() -> None:
    tank = Tank.for_capacity(5.0, mawp_bar=30.0)
    assert tank.capacity_kg == pytest.approx(5.0, rel=1e-6)
    assert tank.pressure(5.0, 50.0) == pytest.approx(0.95 * 30.0, rel=1e-3)
    assert tank.pressure(5.0, 20.0) < tank.pressure(5.0, 50.0)
    assert tank.relief(5.0, 50.0) == 0.0
    overfilled = 5.5
    vented = tank.relief(overfilled, 50.0)
    assert vented > 0.0
    assert tank.pressure(overfilled - vented, 50.0) == pytest.approx(30.0, rel=1e-3)


# ── Conversion and building ──────────────────────────────────────────────────


def test_fuel_cell_and_heat_pump_figures() -> None:
    kg_per_s, heat_w = FuelCell().run(1000.0)
    assert kg_per_s * 3600.0 == pytest.approx(1.0 / (0.45 * 33.33), rel=1e-3)
    assert heat_w == pytest.approx(1000.0 / 0.45 * 0.40)
    pump = HeatPump()
    assert pump.cop_heating(7.0) == pytest.approx(3.9, abs=0.3)  # EN 14511 A7/W35
    assert pump.eer_cooling(35.0) == pytest.approx(3.0, abs=0.3)  # EN 14511 A35/W7
    assert pump.cop_heating(-5.0) < pump.cop_heating(7.0)


def test_building_needs_nothing_inside_the_comfort_band() -> None:
    b = Building(floor_area_m2=100.0)
    heat, cool = b.steady_demand(outdoor_c=21.0, ghi=0.0)
    assert heat == 0.0 and cool == 0.0
    heat, _ = b.steady_demand(outdoor_c=5.0, ghi=0.0)
    assert heat == pytest.approx(b.ua_w_per_k * 15.0 - 300.0)
    _, cool = b.steady_demand(outdoor_c=36.0, ghi=800.0)
    assert cool > 0.0
    hours = b.capacity_j_per_k / b.ua_w_per_k / 3600.0
    assert 20.0 < hours < 40.0  # a masonry house keeps its temperature for a day or so


def test_density_measures_the_koh_band_where_conductivity_cannot() -> None:
    from hestia.physics.electrolyte import conductivity_s_per_cm, density_kg_per_l, wt_pct_from_density

    # Conductivity peaks inside the operating band: two concentrations, one reading.
    m26, m28 = (KohSolution.mixed(10, w).molarity(25) for w in (26, 28))
    assert abs(conductivity_s_per_cm(m26, 25) - conductivity_s_per_cm(m28, 25)) < 0.002
    # Density is monotonic and inverts exactly, at any temperature.
    for t in (20.0, 52.0):
        for w in (24.0, 26.0, 28.0, 30.0, 32.0):
            assert wt_pct_from_density(density_kg_per_l(w, t), t) == pytest.approx(w, abs=1e-9)
