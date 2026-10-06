"""
The simulators of every signed-in session, and their lifetimes.

One simulation per browser session (two people sharing the demo login each
get their own), at most `sim_max_sessions` at once: each one runs a physics
loop on the gateway's CPU. A simulation nobody has watched or touched for
`sim_idle_minutes` is closed. Weather years are loaded once per location.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path

from hestia.planner.weather import year_for
from hestia.settings import Settings
from hestia.twin.weather import HourlyYear, bundled_year

from .config import LOCATIONS, SimConfig
from .scenarios import Scenario
from .session import SimSession, close_quietly

log = logging.getLogger(__name__)

REAP_EVERY_S = 30.0


class SimulatorFull(RuntimeError):
    """Every simulator slot is taken by someone active."""


class SimulationManager:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.sessions: dict[str, SimSession] = {}
        self._weather: dict[str, tuple[HourlyYear, str]] = {}
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._reaper = asyncio.create_task(self._reap(), name="sim-reaper")

    async def stop(self) -> None:
        if self._reaper:
            self._reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper
        for key in list(self.sessions):
            await close_quietly(self.sessions.pop(key))

    def get(self, key: str) -> SimSession | None:
        session = self.sessions.get(key)
        if session:
            session.touch()
        return session

    async def open(
        self, key: str, owner: str, config: SimConfig, scenario: Scenario | None = None
    ) -> SimSession:
        """Build a fresh simulation for this session, replacing its previous one."""
        if scenario and scenario.config:
            config = config.model_copy(update=scenario.config)
            config = SimConfig.model_validate(config.model_dump())  # re-check the combination
        weather, source = await self._weather_for(config)
        async with self._lock:
            old = self.sessions.pop(key, None)
            if old:
                await close_quietly(old)
            if len(self.sessions) >= self.s.sim_max_sessions:
                await close_quietly(self._evict_one())
            session = SimSession(owner, self.s, config, weather, source)
            await session.start()
            if scenario:
                session.apply_scenario(scenario, owner)
                await session.refresh()
            self.sessions[key] = session
            return session

    async def close(self, key: str) -> None:
        async with self._lock:
            session = self.sessions.pop(key, None)
        if session:
            await close_quietly(session)

    def _evict_one(self) -> SimSession:
        """Make room: take out the longest-idle simulation that nobody is watching."""
        idle = [(s.last_seen, k) for k, s in self.sessions.items() if not s.watched and not s.busy]
        if not idle:
            raise SimulatorFull(
                f"All {self.s.sim_max_sessions} simulators are in use; try again in a few minutes"
            )
        _, key = min(idle)
        return self.sessions.pop(key)

    async def _weather_for(self, config: SimConfig) -> tuple[HourlyYear, str]:
        if config.location not in self._weather:
            if config.location == "tunis":
                self._weather["tunis"] = (bundled_year(), "Tunis, measured year 2025-2026 (Open-Meteo)")
            else:
                _, lat, lon, _ = LOCATIONS[config.location]
                self._weather[config.location] = await year_for(
                    lat, lon, data_dir=Path(self.s.data_dir), internet=self.s.planner_internet
                )
        return self._weather[config.location]

    async def _reap(self) -> None:
        idle_s = self.s.sim_idle_minutes * 60.0
        while True:
            await asyncio.sleep(REAP_EVERY_S)
            now = time.monotonic()
            for key, session in list(self.sessions.items()):
                if session.watched:
                    session.touch()
                elif now - session.last_seen > idle_s and not session.busy:
                    log.info("Closing an idle simulator (%s)", session.owner)
                    async with self._lock:
                        if self.sessions.get(key) is session:
                            del self.sessions[key]
                    await close_quietly(session)
