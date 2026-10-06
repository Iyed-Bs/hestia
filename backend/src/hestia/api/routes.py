"""
The gateway's HTTP API (all under /api; interactive docs at /api/docs).

Read the module headers of each service for the "why"; this file is the
"who may do what": every route names the role it requires.

    GET  /api/health                       public  liveness probe (Docker)
    POST /api/auth/login | logout          public / any user
    GET  /api/auth/me                      viewer  who am I + CSRF token
    GET  /api/state                        viewer  latest snapshot
    GET  /api/stream                       viewer  live snapshots (Server-Sent Events)
    POST /api/commands                     operator  mode, thresholds, manual, alarm reset
    GET  /api/trust                        viewer  integrity + maintenance status
    GET  /api/journal                      viewer  safety journal (paged)
    POST /api/journal/verify               viewer  full chain verification
    GET  /api/maintenance                  viewer  history
    POST /api/maintenance                  operator  record a bump test / calibration / ...
    GET  /api/report?days=30               viewer  safety report (also journaled)
    POST /api/twin/fault | speed | jump | bump-test   operator, digital-twin mode only
    GET/POST/PATCH /api/users              admin   user management
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from hestia import __version__
from hestia.domain.commands import Command, CommandRejected
from hestia.runtime.links import LinkError
from hestia.security.auth import AuthError, Role, SessionCodec
from hestia.trust.journal import EventKind
from hestia.trust.report import build_report
from hestia.twin.faults import FaultName

from .deps import Admin, Operator, ServicesDep, Viewer, client_ip, current_principal

router = APIRouter(prefix="/api")
COMMAND: TypeAdapter[Command] = TypeAdapter(Command)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── Health & meta ────────────────────────────────────────────────────────────


@router.get("/health", include_in_schema=False)
def health(svc: ServicesDep) -> dict[str, Any]:
    # demo_users lets the sign-in page show the demo logins (twin mode only, see settings.py).
    return {
        "status": "ok",
        "version": __version__,
        "mode": svc.settings.mode,
        "demo_users": svc.settings.demo_users,
    }


# ── Authentication ───────────────────────────────────────────────────────────


class LoginBody(Strict):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.post("/auth/login")
def login(body: LoginBody, request: Request, response: Response, svc: ServicesDep) -> dict[str, Any]:
    ip = client_ip(request)
    try:
        svc.throttle.check(body.username, ip)
    except AuthError as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
    user = svc.users.verify(body.username, body.password)
    if user is None:
        svc.throttle.failed(body.username, ip)
        svc.journal.append(
            EventKind.LOGIN_FAILED, "Failed sign-in attempt", actor=body.username[:64], details={"ip": ip}
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong username or password")
    svc.throttle.succeeded(body.username)
    token, csrf = svc.sessions.issue(user)
    response.set_cookie(
        SessionCodec.COOKIE,
        token,
        max_age=svc.sessions.max_age_s,
        httponly=True,
        secure=bool(svc.settings.cookie_secure),
        samesite="strict",
        path="/",
    )
    svc.journal.append(EventKind.LOGIN, "Signed in", actor=user.username, details={"ip": ip})
    return {"user": user.as_dict(), "csrf": csrf}


@router.post("/auth/logout")
def logout(response: Response, request: Request, svc: ServicesDep) -> dict[str, str]:
    try:
        principal = current_principal(request, svc)
        svc.users.revoke_sessions(principal.user.id)  # also ends sessions on other devices
    except HTTPException:
        pass
    response.delete_cookie(SessionCodec.COOKIE, path="/")
    return {"status": "signed out"}


@router.get("/auth/me")
def me(principal: Viewer, svc: ServicesDep) -> dict[str, Any]:
    return {
        "user": principal.user.as_dict(),
        "csrf": principal.csrf,
        "mode": svc.settings.mode,
        "site_name": svc.settings.site_name,
        "version": __version__,
    }


# ── Live state ───────────────────────────────────────────────────────────────


@router.get("/state")
def state(_: Viewer, svc: ServicesDep) -> dict[str, Any]:
    return svc.engine.snapshot()


@router.get("/stream")
async def stream(request: Request, _: Viewer, svc: ServicesDep) -> StreamingResponse:
    queue = svc.engine.subscribe()

    async def events() -> AsyncIterator[str]:
        try:
            while not await request.is_disconnected():
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {payload}\n\n"
                except TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            svc.engine.unsubscribe(queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/commands")
async def command(body: dict[str, Any], principal: Operator, svc: ServicesDep) -> dict[str, str]:
    try:
        cmd = COMMAND.validate_python(body)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            exc.errors(include_url=False, include_input=False, include_context=False),
        ) from exc
    try:
        result = await svc.engine.command(cmd, principal.user.username)
    except CommandRejected as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except LinkError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return {"result": result}


# ── Trust layer ──────────────────────────────────────────────────────────────


@router.get("/trust")
def trust(_: Viewer, svc: ServicesDep) -> dict[str, Any]:
    engine = svc.engine
    return {
        "integrity": engine.trust.as_dict() if engine.trust else None,
        "maintenance": [c.as_dict() for c in svc.maintenance.status()],
        "h2_standing": dict(zip(("level", "why"), svc.maintenance.h2_sensor_standing(), strict=True)),
        "plan": {
            "bump_test_days": svc.maintenance.plan.bump_test_days,
            "calibration_days": svc.maintenance.plan.calibration_days,
            "inspection_days": svc.maintenance.plan.inspection_days,
            "grace_days": svc.maintenance.plan.grace_days,
        },
        "baseline_ppm": round(engine.integrity.h2_baseline_ppm, 1),
        "journal_head": svc.journal.head_hash(),
        "ml": engine.advisor.status() if engine.advisor else None,
    }


@router.get("/journal")
def journal(
    _: Viewer,
    svc: ServicesDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    before_id: Annotated[int | None, Query(ge=1)] = None,
    kind: Annotated[list[str] | None, Query()] = None,
) -> dict[str, Any]:
    entries = svc.journal.entries(limit=limit, before_id=before_id, kinds=kind)
    return {"entries": [e.as_dict() for e in entries], "kinds": [k.value for k in EventKind]}


@router.post("/journal/verify")
def verify_journal(_: Viewer, svc: ServicesDep) -> dict[str, Any]:
    v = svc.journal.verify()
    return {
        "ok": v.ok,
        "checked": v.checked,
        "head_hash": v.head_hash,
        "first_broken_id": v.first_broken_id,
        "problem": v.problem,
    }


class MaintenanceBody(Strict):
    kind: Literal["bump_test", "calibration", "inspection", "sensor_replaced"]
    sensor: Literal["h2", "temperature", "electrolyte", "installation"]
    data: dict[str, float] = {}
    notes: str = Field("", max_length=1000)


REQUIRED_DATA = {
    ("bump_test", "h2"): {"gas_ppm", "peak_reading_ppm"},
    ("calibration", "h2"): {"span_gas_ppm", "span_reading_ppm", "clean_air_ppm"},
    ("calibration", "electrolyte"): {"std_low_pct", "reading_low_pct", "std_high_pct", "reading_high_pct"},
}


@router.get("/maintenance")
def maintenance_history(_: Viewer, svc: ServicesDep) -> dict[str, Any]:
    return {"records": svc.maintenance.history(), "status": [c.as_dict() for c in svc.maintenance.status()]}


@router.post("/maintenance")
async def record_maintenance(body: MaintenanceBody, principal: Operator, svc: ServicesDep) -> dict[str, Any]:
    missing = REQUIRED_DATA.get((body.kind, body.sensor), set()) - body.data.keys()
    if missing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Missing measurements: {', '.join(sorted(missing))}"
        )
    if any(v < 0 or v > 100_000 for v in body.data.values()):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Measurements must be between 0 and 100 000"
        )
    return await svc.engine.record_maintenance(
        kind=body.kind,
        sensor=body.sensor,
        technician=principal.user.username,
        data=body.data,
        notes=body.notes,
    )


@router.get("/report")
def report(
    principal: Viewer, svc: ServicesDep, days: Annotated[int, Query(ge=1, le=3650)] = 30
) -> dict[str, Any]:
    return build_report(
        journal=svc.journal,
        maintenance=svc.maintenance,
        trust=svc.engine.trust,
        site_name=svc.settings.site_name,
        mode=svc.settings.mode,
        days=days,
        requested_by=principal.user.username,
    )


# ── Digital twin (simulation controls) ───────────────────────────────────────


class FaultBody(Strict):
    name: FaultName
    on: bool


class SpeedBody(Strict):
    speed: float = Field(ge=1, le=3600)
    paused: bool = False


class JumpBody(Strict):
    month: int = Field(ge=1, le=12)
    day: int = Field(ge=1, le=28)
    hour: float = Field(ge=0, lt=24)


class BumpBody(Strict):
    gas_ppm: float = Field(gt=0, le=10_000)


def _twin(svc: ServicesDep) -> Any:
    try:
        return svc.engine.twin()
    except CommandRejected as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/twin/fault")
def twin_fault(body: FaultBody, principal: Operator, svc: ServicesDep) -> dict[str, Any]:
    _twin(svc)
    svc.engine.set_fault(body.name, body.on, principal.user.username)
    return {"faults": sorted(svc.engine.twin().device.faults.active)}


@router.post("/twin/speed")
def twin_speed(body: SpeedBody, _: Operator, svc: ServicesDep) -> dict[str, Any]:
    twin = _twin(svc)
    twin.set_speed(body.speed)
    twin.paused = body.paused
    return {"speed": twin.speed, "paused": twin.paused}


@router.post("/twin/jump")
def twin_jump(body: JumpBody, _: Operator, svc: ServicesDep) -> dict[str, str]:
    _twin(svc).jump_to(body.month, body.day, body.hour)
    return {"status": "ok"}


@router.post("/twin/bump-test")
def twin_bump(body: BumpBody, _: Operator, svc: ServicesDep) -> dict[str, float]:
    """Expose the simulated H₂ sensor to test gas and read its peak (record it via /api/maintenance)."""
    return {"gas_ppm": body.gas_ppm, "peak_reading_ppm": _twin(svc).bump_test(body.gas_ppm)}


# ── Users (admin) ────────────────────────────────────────────────────────────


class NewUser(Strict):
    username: str = Field(min_length=2, max_length=64)
    display_name: str = Field("", max_length=100)
    role: Literal["viewer", "operator", "admin"]
    password: str = Field(min_length=12, max_length=256)


class UserPatch(Strict):
    role: Literal["viewer", "operator", "admin"] | None = None
    disabled: bool | None = None
    password: str | None = Field(None, min_length=12, max_length=256)


@router.get("/users")
def list_users(_: Admin, svc: ServicesDep) -> list[dict[str, object]]:
    return [u.as_dict() for u in svc.users.all()]


@router.post("/users", status_code=201)
def create_user(body: NewUser, principal: Admin, svc: ServicesDep) -> dict[str, object]:
    try:
        user = svc.users.create(body.username, body.password, Role.parse(body.role), body.display_name)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except Exception as exc:  # sqlite3.IntegrityError: name taken
        raise HTTPException(status.HTTP_409_CONFLICT, "This username already exists") from exc
    svc.journal.append(
        EventKind.USER_CHANGED,
        f"User '{user.username}' created ({user.role.label})",
        actor=principal.user.username,
    )
    return user.as_dict()


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserPatch, principal: Admin, svc: ServicesDep) -> dict[str, object]:
    target = svc.users.get(user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such user")
    if target.id == principal.user.id and (body.disabled or (body.role and body.role != "admin")):
        raise HTTPException(status.HTTP_409_CONFLICT, "You cannot disable or demote yourself")
    try:
        if body.password:
            svc.users.set_password(user_id, body.password)
        svc.users.update(user_id, role=Role.parse(body.role) if body.role else None, disabled=body.disabled)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    svc.journal.append(
        EventKind.USER_CHANGED,
        f"User '{target.username}' updated",
        actor=principal.user.username,
        details=body.model_dump(exclude={"password"}, exclude_none=True)
        | ({"credentials_reset": True} if body.password else {}),
    )
    updated = svc.users.get(user_id)
    if updated is None:  # pragma: no cover - checked above
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such user")
    return updated.as_dict()


# ── E-mail alerts (admin) ────────────────────────────────────────────────────
# Configured in .env only (HESTIA_EMAIL_ALERTS, HESTIA_GMAIL_*): the app
# password never goes through the browser or into the database.


@router.get("/alerts")
def alerts_status(_: Admin, svc: ServicesDep) -> dict[str, Any]:
    return svc.engine.notifier.status()


@router.post("/alerts/test")
async def alerts_test(principal: Admin, svc: ServicesDep) -> dict[str, Any]:
    from hestia.runtime.notifier import AlertError

    try:
        await svc.engine.notifier.send_test(principal.user.username)
    except AlertError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return svc.engine.notifier.status()


# ── Planner ──────────────────────────────────────────────────────────────────


@router.get("/planner/presets")
def planner_presets(_: Viewer) -> dict[str, Any]:
    from hestia.planner.presets import PRESETS

    return {"presets": PRESETS}


@router.post("/planner/run")
async def planner_run(body: dict[str, Any], _: Viewer, svc: ServicesDep) -> dict[str, Any]:
    """Simulate a year for the given building and sizes (runs in a worker thread, ~0.5 s)."""
    from starlette.concurrency import run_in_threadpool

    from hestia.planner.model import PlannerInputs, plan
    from hestia.planner.weather import year_for

    try:
        inputs = PlannerInputs.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            exc.errors(include_url=False, include_input=False, include_context=False),
        ) from exc
    weather, source = await year_for(
        inputs.latitude,
        inputs.longitude,
        data_dir=svc.settings.data_dir,
        internet=svc.settings.planner_internet,
    )
    return await run_in_threadpool(plan, inputs, weather, source)
