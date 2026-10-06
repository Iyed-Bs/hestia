"""
Weather for the digital twin: a replay of a real measured year.

The bundled file is one year (2025-05-24 → 2026-05-24) of hourly weather for
Tunis (36.8 N, 10.2 E) from the Open-Meteo historical archive (CC BY 4.0,
https://open-meteo.com). The twin interpolates between hours, so a cloudy
afternoon in the twin is a cloudy afternoon that really happened.

The planner (planner/weather.py) fetches the same kind of year for any
location; this module only serves the twin and the offline fallback.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from functools import lru_cache
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "weather_tunis_2025_2026.csv"


@dataclass(frozen=True)
class Weather:
    timestamp: datetime  # UTC
    temperature_c: float
    humidity_pct: float
    irradiance_wpm2: float

    @property
    def hour(self) -> float:
        return self.timestamp.hour + self.timestamp.minute / 60.0

    @property
    def day_of_year(self) -> int:
        return self.timestamp.timetuple().tm_yday


@dataclass(frozen=True)
class HourlyYear:
    start: datetime
    temperature_c: tuple[float, ...]
    humidity_pct: tuple[float, ...]
    irradiance_wpm2: tuple[float, ...]

    def __len__(self) -> int:
        return len(self.temperature_c)


def _parse_ts(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)


@lru_cache(maxsize=1)
def bundled_year() -> HourlyYear:
    """The bundled Tunis year, loaded once."""
    temps, hums, irrs = [], [], []
    with DATA_FILE.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        temps.append(float(row["temperature_c"]))
        hums.append(float(row["humidity_pct"]))
        irrs.append(max(0.0, float(row["solar_irradiance_wpm2"])))
    return HourlyYear(_parse_ts(rows[0]["timestamp_utc"]), tuple(temps), tuple(hums), tuple(irrs))


class WeatherReplay:
    """Weather at any second of the replayed year (wraps around at the end)."""

    def __init__(self, year: HourlyYear | None = None) -> None:
        self.year = year or bundled_year()

    def at(self, elapsed_s: float) -> Weather:
        n = len(self.year)
        hours = (elapsed_s / 3600.0) % n

        def lerp(series: tuple[float, ...], position: float) -> float:
            i = int(position) % n
            j = (i + 1) % n
            frac = position - int(position)
            return series[i] + (series[j] - series[i]) * frac

        # Temperature and humidity are instantaneous values at the hour.
        # Irradiance is the mean of the *preceding* hour (Open-Meteo), so the
        # value labelled H belongs to H - 30 min: shift by half an hour before
        # interpolating, or the sun would rise and set 30 minutes late.
        return Weather(
            timestamp=self.year.start + timedelta(hours=hours),
            temperature_c=lerp(self.year.temperature_c, hours),
            humidity_pct=lerp(self.year.humidity_pct, hours),
            irradiance_wpm2=max(0.0, lerp(self.year.irradiance_wpm2, (hours + 0.5) % n)),
        )

    def offset_of(self, month: int, day: int, hour: float, tz: tzinfo = UTC) -> float:
        """Seconds from the start of the replay to a date and hour (local time in tz)."""
        start = self.year.start
        for year in (start.year, start.year + 1):
            target = datetime(year, month, day, tzinfo=tz) + timedelta(hours=hour)
            if target >= start:
                return (target - start).total_seconds()
        return 0.0
