"""
The simulator's API: a private whole-site sandbox per signed-in session.

Every signed-in user, visitors included, may run one: it only ever touches
its own simulated building (sim/session.py), never the live installation.

    GET  /api/sim/catalog       scenarios, default site, faults, field limits
    POST /api/sim/start         build a simulation (site settings, optional scenario)
    POST /api/sim/stop          close it
    GET  /api/sim/status        is a simulation running for this sign-in?
    GET  /api/sim/state         latest snapshot
    GET  /api/sim/stream        live snapshots (Server-Sent Events)
    POST /api/sim/scenario      load a scenario into the running simulation
    POST /api/sim/time          speed, pause, jump to a date, fast-forward
    POST /api/sim/fault         switch a fault on or off
    POST /api/sim/conditions    set the tank, battery, indoor and electrolyte state
    POST /api/sim/commands      operator commands to the simulated controller
    POST /api/sim/bump-test     expose the simulated detector to test gas
    POST /api/sim/maintenance   record a check in the simulation's own log
    GET  /api/sim/journal       the simulation's own safety journal
    GET  /api/sim/trust         its trust layer
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import Field, ValidationError

from hestia.domain.commands import CommandRejected
from hestia.sim.config import LOCATIONS, SimConfig
from hestia.sim.manager import SimulationManager, SimulatorFull
from hestia.sim.scenarios import BY_ID, SCENARIOS
from hestia.sim.session import SimSession
from hestia.trust.journal import EventKind
from hestia.twin.faults import ALL_FAULTS

from .deps import Principal, ServicesDep, Viewer
from .routes import COMMAND, REQUIRED_DATA, BumpBody, FaultBody, JumpBody, MaintenanceBody, Strict

router = APIRouter(prefix="/api/sim")


def _manager(svc: ServicesDep) -> SimulationManager:
    if svc.sims is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The simulator is turned off on this gateway")
    return svc.sims


def _key(principal: Principal) -> str:
    # One simulation per sign-in, not per account: people sharing the demo
    # login each get their own. The CSRF token is unique to the session.
    return hashlib.sha256(f"{principal.user.id}:{principal.csrf}".encode()).hexdigest()[:24]


def _session(principal: Principal, svc: ServicesDep) -> SimSession:
    session = _manager(svc).get(_key(principal))
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No simulation running: start one first")
    return session


# ── Catalogue and lifecycle ──────────────────────────────────────────────────


@router.get("/catalog")
def catalog(_: Viewer, svc: ServicesDep) -> dict[str, Any]:
    _manager(svc)
    return {
        "scenarios": [s.as_dict() for s in SCENARIOS],
        "defaults": SimConfig().model_dump(),
        "schema": SimConfig.model_json_schema(),
        "locations": {k: {"name": v[0], "latitude": v[1], "longitude": v[2]} for k, v in LOCATIONS.items()},
        "faults": list(ALL_FAULTS),
    }


class StartBody(Strict):
    config: dict[str, Any] = Field(default_factory=dict)
    scenario: str | None = None


@router.post("/start")
async def start(body: StartBody, principal: Viewer, svc: ServicesDep) -> dict[str, Any]:
    manager = _manager(svc)
    try:
        config = SimConfig.model_validate(body.config)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            exc.errors(include_url=False, include_input=False, include_context=False),
        ) from exc
    scenario = None
    if body.scenario is not None:
        scenario = BY_ID.get(body.scenario)
        if scenario is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such scenario")
    try:
        session = await manager.open(_key(principal), principal.user.username, config, scenario)
    except SimulatorFull as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return session.engine.snapshot()


@router.post("/stop")
async def stop(principal: Viewer, svc: ServicesDep) -> dict[str, str]:
    await _manager(svc).close(_key(principal))
    return {"status": "closed"}


@router.get("/status")
def sim_status(principal: Viewer, svc: ServicesDep) -> dict[str, Any]:
    session = _manager(svc).get(_key(principal))
    return {
        "running": session is not None,
        "scenario": session.scenario.id if session and session.scenario else None,
    }


@router.get("/state")
def state(principal: Viewer, svc: ServicesDep) -> dict[str, Any]:
    return _session(principal, svc).engine.snapshot()


@router.get("/stream")
async def stream(request: Request, principal: Viewer, svc: ServicesDep) -> StreamingResponse:
    session = _session(principal, svc)
    engine = session.engine
    queue = engine.subscribe()

    async def events() -> AsyncIterator[str]:
        try:
            while not await request.is_disconnected():
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {payload}\n\n"
                except TimeoutError:
                    yield ": keep-alive\n\n"
                session.touch()
        finally:
            engine.unsubscribe(queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


# ── Driving the simulation ───────────────────────────────────────────────────


class ScenarioBody(Strict):
    id: str = Field(max_length=40)


@router.post("/scenario")
async def scenario(body: ScenarioBody, principal: Viewer, svc: ServicesDep) -> dict[str, Any]:
    chosen = BY_ID.get(body.id)
    if chosen is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such scenario")
    session = _session(principal, svc)
    needs = session.config.model_copy(update=chosen.config)
    if needs != session.config:
        # The scenario needs different equipment: rebuild the site around it.
        try:
            session = await _manager(svc).open(
                _key(principal), principal.user.username, session.config, chosen
            )
        except SimulatorFull as exc:  # pragma: no cover - the caller's own slot is reused
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    else:
        session.apply_scenario(chosen, principal.user.username)
    await session.refresh()
    return session.engine.snapshot()


class TimeBody(Strict):
    speed: float | None = Field(None, ge=1, le=3600)
    paused: bool | None = None
    jump: JumpBody | None = None
    advance_hours: float | None = Field(None, gt=0, le=24)


@router.post("/time")
async def time_control(body: TimeBody, principal: Viewer, svc: ServicesDep) -> dict[str, Any]:
    session = _session(principal, svc)
    if session.busy:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already fast-forwarding")
    if body.speed is not None:
        session.link.set_speed(body.speed)
    if body.paused is not None:
        session.link.paused = body.paused
    if body.jump is not None:
        session.device.jump_to(body.jump.month, body.jump.day, body.jump.hour)
        await session.refresh()
    if body.advance_hours is not None:
        await session.fast_forward(body.advance_hours)
    return {"speed": session.link.speed, "paused": session.link.paused}


@router.post("/fault")
def fault(body: FaultBody, principal: Viewer, svc: ServicesDep) -> dict[str, Any]:
    session = _session(principal, svc)
    session.engine.set_fault(body.name, body.on, principal.user.username)
    return {"faults": sorted(session.device.faults.active)}


class ConditionsBody(Strict):
    tank_pct: float | None = Field(None, ge=0, le=100)
    battery_pct: float | None = Field(None, ge=0, le=100)
    indoor_c: float | None = Field(None, ge=5, le=35)
    electrolyte_c: float | None = Field(None, ge=5, le=65)


@router.post("/conditions")
async def conditions(body: ConditionsBody, principal: Viewer, svc: ServicesDep) -> dict[str, str]:
    session = _session(principal, svc)
    session.set_state(**body.model_dump())
    await session.refresh()
    session.journal.append(
        EventKind.SCENARIO,
        "[Simulation] conditions set by hand",
        actor=principal.user.username,
        details={**body.model_dump(exclude_none=True), "simulation": True},
    )
    return {"status": "ok"}


@router.post("/commands")
async def command(body: dict[str, Any], principal: Viewer, svc: ServicesDep) -> dict[str, str]:
    session = _session(principal, svc)
    try:
        cmd = COMMAND.validate_python(body)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            exc.errors(include_url=False, include_input=False, include_context=False),
        ) from exc
    try:
        result = await session.engine.command(cmd, principal.user.username)
    except CommandRejected as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {"result": result}


@router.post("/bump-test")
def bump_test(body: BumpBody, principal: Viewer, svc: ServicesDep) -> dict[str, float]:
    session = _session(principal, svc)
    return {"gas_ppm": body.gas_ppm, "peak_reading_ppm": session.link.bump_test(body.gas_ppm)}


@router.post("/maintenance")
async def maintenance(body: MaintenanceBody, principal: Viewer, svc: ServicesDep) -> dict[str, Any]:
    session = _session(principal, svc)
    missing = REQUIRED_DATA.get((body.kind, body.sensor), set()) - body.data.keys()
    if missing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Missing measurements: {', '.join(sorted(missing))}"
        )
    if any(v < 0 or v > 100_000 for v in body.data.values()):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Measurements must be between 0 and 100 000"
        )
    return await session.engine.record_maintenance(
        kind=body.kind,
        sensor=body.sensor,
        technician=principal.user.username,
        data=body.data,
        notes=body.notes,
    )


# ── Reading it back ──────────────────────────────────────────────────────────


@router.get("/journal")
def journal(
    principal: Viewer,
    svc: ServicesDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    before_id: Annotated[int | None, Query(ge=1)] = None,
) -> dict[str, Any]:
    session = _session(principal, svc)
    entries = session.journal.entries(limit=limit, before_id=before_id)
    return {"entries": [e.as_dict() for e in entries]}


@router.get("/trust")
def trust(principal: Viewer, svc: ServicesDep) -> dict[str, Any]:
    session = _session(principal, svc)
    engine = session.engine
    return {
        "integrity": engine.trust.as_dict() if engine.trust else None,
        "maintenance": [c.as_dict() for c in session.maintenance.status()],
        "h2_standing": dict(zip(("level", "why"), session.maintenance.h2_sensor_standing(), strict=True)),
        "baseline_ppm": round(engine.integrity.h2_baseline_ppm, 1),
    }
