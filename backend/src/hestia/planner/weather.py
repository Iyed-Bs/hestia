"""
A year of real hourly weather for any location, for the planner.

Source: Open-Meteo historical archive (ERA5 reanalysis, CC BY 4.0), the last
complete calendar year at the requested coordinates. Years are cached under
the data directory (rounded to 0.1°, about 10 km), so a location is fetched
once. With HESTIA_PLANNER_INTERNET=false, or when the service cannot be
reached, the bundled Tunis year is used and the result says so.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from hestia.twin.weather import HourlyYear, bundled_year

log = logging.getLogger(__name__)

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"


def _cache_path(data_dir: Path, lat: float, lon: float, year: int) -> Path:
    return data_dir / "weather-cache" / f"{lat:.1f}_{lon:.1f}_{year}.json"


async def year_for(lat: float, lon: float, *, data_dir: Path, internet: bool) -> tuple[HourlyYear, str]:
    """Returns (weather year, description of the source)."""
    year = date.today().year - 1
    path = _cache_path(data_dir, round(lat, 1), round(lon, 1), year)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return _to_year(data), f"Open-Meteo archive {year} at {lat:.1f}, {lon:.1f} (cached)"
    if internet:
        params: dict[str, str | float] = {
            "latitude": round(lat, 2),
            "longitude": round(lon, 2),
            "start_date": f"{year}-01-01",
            "end_date": f"{year}-12-31",
            "hourly": "temperature_2m,relative_humidity_2m,shortwave_radiation",
            "timezone": "UTC",
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(ARCHIVE, params=params)
                response.raise_for_status()
                hourly = response.json()["hourly"]
            data = {
                "start": hourly["time"][0],
                "temperature_c": hourly["temperature_2m"],
                "humidity_pct": hourly["relative_humidity_2m"],
                "irradiance_wpm2": hourly["shortwave_radiation"],
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data), encoding="utf-8")
            return _to_year(data), f"Open-Meteo archive {year} at {lat:.1f}, {lon:.1f}"
        except (httpx.HTTPError, KeyError, ValueError, IndexError) as exc:
            log.warning("Weather archive unavailable, using the bundled Tunis year: %s", exc)
    return bundled_year(), "Bundled Tunis year (2025–2026): location weather unavailable offline"


def _to_year(data: dict[str, object]) -> HourlyYear:
    def clean(values: object) -> tuple[float, ...]:
        seq = values if isinstance(values, list) else []
        out, last = [], 0.0
        for v in seq:
            last = float(v) if v is not None else last  # fill rare gaps with the previous hour
            out.append(last)
        return tuple(out)

    start = datetime.fromisoformat(str(data["start"])).replace(tzinfo=UTC)
    return HourlyYear(
        start,
        clean(data["temperature_c"]),
        clean(data["humidity_pct"]),
        tuple(max(0.0, v) for v in clean(data["irradiance_wpm2"])),
    )
