"""
Country presets for the planner: tariffs, carbon intensity, equipment costs.

Each value says where it comes from. Values marked "illustrative" are
reasonable starting points, not quotes: the planner shows them openly and
every one can be edited in the form, which is the point. A planner that
hides its assumptions is a sales brochure.

Sources:
- Hydrogen LHV 33.33 kWh/kg: US DOE Hydrogen Program.
- Natural gas combustion 0.202 kg CO₂/kWh: IPCC 2006 default (56.1 t/TJ).
- Tunisia grid 0.468 kg CO₂e/kWh (2024): Low-Carbon Power / Ember data.
- France grid 0.0213 kg CO₂e/kWh (2024 production): RTE, Bilan électrique 2024.
- STEG residential tariffs 176–414 millimes/kWh by tier (in force since May 2022).
- Tunisian self-producers may sell surplus to STEG within 30 % of their
  yearly production (law 2015-12; US Trade.gov country commercial guide,
  Tunisia, renewable energy). Applied to a household as an illustration.
- Electrolyser cost: ~2 300 $/kW installed for MW-scale alkaline plants in
  2025 (BNEF via pv magazine, 2024); kW-scale systems cost several times more.
- Heating: reversible split heat pumps are the usual way Tunisian homes heat
  and cool; French homes are still mostly heated by gas or electricity and
  rarely cooled. Both are editable.
"""

from __future__ import annotations

from typing import Any

TND_PER_EUR = 3.4  # approximate 2025–2026 rate, for converting Tunisian tariffs

COMMON_COSTS: dict[str, Any] = {
    "currency": "EUR",
    "pv_eur_per_kwp": 1200.0,  # illustrative, small rooftop installed
    "battery_eur_per_kwh": 600.0,  # illustrative, installed
    "electrolyser_eur_per_kw": 4000.0,  # illustrative: kW-scale premium over MW plants
    "storage_eur_per_kg": 700.0,  # illustrative, 30 bar steel vessels
    "fuel_cell_eur_per_kw": 4000.0,  # illustrative, kW-scale PEM module with its power electronics
    "h2_boiler_eur": 3500.0,  # illustrative, hydrogen-ready condensing boiler
    "installation_share": 0.15,  # illustrative, balance of plant + labour
    "om_share_per_year": 0.02,  # illustrative, maintenance as a share of capex
    "lifetime_years": 20,
    "discount_rate": 0.06,
}

PRESETS: dict[str, dict[str, Any]] = {
    "tunisia": {
        "label": "Tunisia (STEG)",
        "latitude": 36.8,
        "longitude": 10.2,
        "timezone": "Africa/Tunis",
        "grid_kgco2_per_kwh": 0.468,
        "import_price": round(0.300 / TND_PER_EUR, 3),  # ≈ 300 millimes/kWh, mid-tier STEG
        "export_price": round(0.120 / TND_PER_EUR, 3),  # illustrative
        "export_cap_share": 0.30,  # of the yearly PV production
        "heating": "heat_pump",
        "cooling": True,
        "gas_price": round(0.090 / TND_PER_EUR, 3),  # illustrative, per kWh of gas
        **COMMON_COSTS,
        "pv_eur_per_kwp": 800.0,  # illustrative, ≈ 2 700 TND/kWp installed (Tunisian market)
    },
    "france": {
        "label": "France",
        "latitude": 48.85,
        "longitude": 2.35,
        "timezone": "Europe/Paris",
        "grid_kgco2_per_kwh": 0.0213,
        "import_price": 0.25,  # illustrative, regulated residential tariff order of magnitude
        "export_price": 0.04,  # illustrative, small-installation surplus buy-back
        "export_cap_share": 1.0,
        "heating": "gas_boiler",
        "cooling": False,
        "gas_price": 0.11,  # illustrative
        **COMMON_COSTS,
        "pv_eur_per_kwp": 2000.0,  # illustrative, small French rooftop installed
    },
}
