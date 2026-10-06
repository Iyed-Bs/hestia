"""
One private simulation: a whole site, its own gateway engine, its own journal.

Everything the live gateway runs runs here too (controller, energy manager,
trust layer, ML advice, journal), on a simulated building instead of the
bench. Nothing is shared with the live installation: the database lives in
memory and disappears with the session, the journal has its own key, and
e-mail alerts are off, so a simulated leak never wakes anyone up.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from pathlib import Path
from typing import Any

from hestia.demo import seed_maintenance
from hestia.ml.runtime import MLAdvisor
from hestia.runtime.engine import Engine
from hestia.runtime.links import TwinLink
from hestia.runtime.notifier import Notifier
from hestia.settings import Settings
from hestia.storage.db import Database
from hestia.trust.journal import EventKind, SafetyJournal
from hestia.trust.maintenance import MaintenanceLog, MaintenancePlan
from hestia.twin.device import VirtualDevice
from hestia.twin.faults import ALL_FAULTS
from hestia.twin.plant import SiteModel
from hestia.twin.weather import HourlyYear, WeatherReplay

from .config import SimConfig
from .scenarios import Scenario

log = logging.getLogger(__name__)

FAST_FORWARD_CHUNK_S = 600.0  # simulated seconds per slice, so other sessions keep their turn


class SimSession:
    def __init__(
        self,
        owner: str,
        settings: Settings,
        config: SimConfig,
        weather: HourlyYear,
        weather_source: str,
        *,
        seed: int | None = None,
    ) -> None:
        self.owner = owner
        self.config = config
        self.weather_source = weather_source
        self.scenario: Scenario | None = None
        self.last_seen = time.monotonic()
        self.busy = False  # fast-forward running

        name, *_ = config.place
        self.settings = settings.model_copy(
            update={"email_alerts": False, "site_name": f"Simulator, {name}", "twin_speed": 60.0}
        )
        self.db = Database(Path(":memory:"))
        self.journal = SafetyJournal(self.db, secrets.token_bytes(32))
        self.maintenance = MaintenanceLog(
            self.db,
            self.journal,
            MaintenancePlan(
                settings.bump_test_days,
                settings.calibration_days,
                settings.inspection_days,
                grace_days=settings.maintenance_grace_days,
            ),
        )
        seed_maintenance(self.maintenance)
        self.model = SiteModel(
            config.profile(),
            seed=seed if seed is not None else secrets.randbelow(1_000_000),
            weather=WeatherReplay(weather),
        )
        self.device = VirtualDevice(self.model, device_id="sim_1")
        self.link = TwinLink(self.settings, self.device)
        try:
            advisor: MLAdvisor | None = MLAdvisor(background=True)
        except Exception:  # missing runtime or manifest: run without advice
            log.exception("ML advisor unavailable in the simulator")
            advisor = None
        self.engine = Engine(
            self.settings,
            self.journal,
            self.maintenance,
            self.link,
            advisor,
            Notifier(self.settings, self.journal),
            label="Simulator",
        )
        self.engine.snapshot_extra = self.describe

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        await self.engine.start()

    async def stop(self) -> None:
        await self.engine.stop()
        self.db.close()

    def touch(self) -> None:
        self.last_seen = time.monotonic()

    @property
    def watched(self) -> bool:
        return self.engine.subscriber_count > 0

    # ── What the visitor can do ──────────────────────────────────────────────

    def apply_scenario(self, scenario: Scenario, actor: str) -> None:
        """Date, state, faults and speed of a scenario (equipment is set when the session is built)."""
        self.scenario = scenario
        for name in list(self.device.faults.active):
            self.device.set_fault(name, False)  # type: ignore[arg-type]
        self.device.jump_to(scenario.month, scenario.day, scenario.hour)
        self.set_state(
            tank_pct=scenario.tank_pct,
            battery_pct=scenario.battery_pct,
            indoor_c=scenario.indoor_c,
            electrolyte_c=scenario.electrolyte_c,
        )
        for name in scenario.faults:
            self.device.set_fault(name, True)
        self.link.set_speed(scenario.speed)
        self.link.paused = False
        self.journal.append(
            EventKind.SCENARIO,
            f"[Simulation] scenario '{scenario.title_en}'",
            actor=actor,
            details={"scenario": scenario.id, "faults": list(scenario.faults), "simulation": True},
        )

    def set_state(
        self,
        *,
        tank_pct: float | None = None,
        battery_pct: float | None = None,
        indoor_c: float | None = None,
        electrolyte_c: float | None = None,
    ) -> None:
        m = self.model
        if tank_pct is not None:
            m.h2_kg = tank_pct / 100.0 * m.p.tank.capacity_kg
        if battery_pct is not None:
            m.battery_kwh = battery_pct / 100.0 * m.p.battery_kwh
        if indoor_c is not None:
            m.indoor_c = m.mass_c = indoor_c
        if electrolyte_c is not None:
            m.plant.electrolyte_c = electrolyte_c

    async def refresh(self) -> None:
        """One physics step, so a jump or a new condition shows at once, even when paused."""
        await self.link.advance(self.link.step_s, self.engine.ingest)

    async def fast_forward(self, hours: float) -> None:
        """Run `hours` of simulated time as fast as the CPU allows, in slices."""
        self.busy = True
        was_paused, self.link.paused = self.link.paused, True
        try:
            left = hours * 3600.0
            while left > 0:
                chunk = min(FAST_FORWARD_CHUNK_S, left)
                await self.link.advance(chunk, self.engine.ingest)
                left -= chunk
                await asyncio.sleep(0)  # let the other sessions and requests run
        finally:
            self.link.paused = was_paused
            self.busy = False

    def describe(self) -> dict[str, Any]:
        """Added to every snapshot of this session."""
        return {
            "owner": self.owner,
            "config": self.config.model_dump(),
            "scenario": self.scenario.id if self.scenario else None,
            "weather_source": self.weather_source,
            "all_faults": list(ALL_FAULTS),
            "busy": self.busy,
            # Where surplus goes here, best first, with what each kWh is worth.
            "merit": self.model.p.values().table(),
            "surplus_order": self.model.p.surplus_order(),
            "tank": {
                "capacity_kg": round(self.model.p.tank.capacity_kg, 3),
                "volume_m3": round(self.model.p.tank.volume_m3, 3),
                "mawp_bar": self.model.p.tank.mawp_bar,
            },
        }


async def close_quietly(session: SimSession) -> None:
    with contextlib.suppress(Exception):
        await session.stop()
