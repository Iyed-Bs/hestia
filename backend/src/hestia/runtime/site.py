"""
Site context on a real installation (HESTIA_MODE=device).

The controller only measures the electrolyser loop. Solar production,
weather and storage come from elsewhere; until a site meter is integrated,
Hestia estimates them:

- pv_source=weather: current irradiance, temperature and humidity from the
  Open-Meteo forecast API (no key, refreshed every 10 minutes); PV power from
  the same tilted-panel model as the simulator (physics/solar.py). Marked
  source="estimated" everywhere.
- pv_source=bench: the bench's lab power supply (HESTIA_BENCH_SUPPLY_W).

If the weather service is unreachable the last estimate is kept for an hour,
then PV is assumed to be zero: the energy manager stops granting production
rather than guessing.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

import httpx

from hestia.domain.telemetry import SiteContext
from hestia.physics.solar import PvArray
from hestia.settings import Settings

log = logging.getLogger(__name__)

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
REFRESH_S = 600
MAX_AGE_S = 3600


class SiteEstimator:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._fetched_at = 0.0
        self._weather: tuple[float, float, float] | None = None  # temp, humidity, irradiance
        self.array = PvArray(
            kwp=settings.pv_kwp, tilt_deg=settings.pv_tilt_deg, azimuth_deg=settings.pv_azimuth_deg
        )

    async def current(self) -> SiteContext:
        now = time.monotonic()
        if self.s.pv_source == "bench":
            return self._context(25.0, 50.0, 0.0, pv_w=0.0, available_w=self.s.bench_supply_w)
        if now - self._fetched_at > REFRESH_S:
            await self._refresh(now)
        if self._weather is None or now - self._fetched_at > MAX_AGE_S:
            return self._context(0.0, 0.0, 0.0, pv_w=0.0, available_w=0.0)
        temp, hum, irr = self._weather
        pv = self.array.output(irr, temp, datetime.now(UTC), self.s.site_latitude, self.s.site_longitude)
        return self._context(temp, hum, irr, pv_w=pv, available_w=pv)

    async def _refresh(self, now: float) -> None:
        params: dict[str, str | float] = {
            "latitude": self.s.site_latitude,
            "longitude": self.s.site_longitude,
            "current": "temperature_2m,relative_humidity_2m,shortwave_radiation",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(OPEN_METEO, params=params)
                response.raise_for_status()
                cur = response.json()["current"]
            self._weather = (
                float(cur["temperature_2m"]),
                float(cur["relative_humidity_2m"]),
                max(0.0, float(cur["shortwave_radiation"])),
            )
            self._fetched_at = now
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            log.warning("Weather estimate unavailable: %s", exc)
            self._fetched_at = now - REFRESH_S + 60  # retry in a minute

    def _context(
        self, temp: float, hum: float, irr: float, *, pv_w: float, available_w: float
    ) -> SiteContext:
        return SiteContext(
            source="estimated",
            timestamp=datetime.now(UTC),
            outdoor_c=round(temp, 1),
            humidity_pct=round(hum, 1),
            irradiance_wpm2=round(irr, 1),
            pv_w=round(pv_w, 1),
            electrolyser_w=0.0,
            available_w=round(available_w, 1),
        )
