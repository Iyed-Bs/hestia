"""
Hestia gateway: application factory.

Run in development (digital twin, generated dev secrets, demo logins off):
    cd backend && uv run uvicorn hestia.main:app --reload

Run in production: see deploy/docker-compose.yml (HTTPS proxy, broker, .env).

The web dashboard is the compiled frontend (frontend/ → npm run build),
served from hestia/static by this same process, so the gateway is one
container with one port behind the HTTPS proxy.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from hestia import __version__
from hestia.demo import seed_maintenance
from hestia.ml.runtime import MLAdvisor
from hestia.runtime.engine import Engine
from hestia.runtime.links import DeviceLink, MqttLink, TwinLink
from hestia.runtime.notifier import Notifier
from hestia.security.auth import LoginThrottle, Role, SessionCodec, UserStore
from hestia.security.headers import SecurityHeaders
from hestia.settings import Settings, get_settings
from hestia.sim.manager import SimulationManager
from hestia.storage.db import Database
from hestia.trust.journal import SafetyJournal
from hestia.trust.maintenance import MaintenanceLog, MaintenancePlan

from .api.deps import Services
from .api.routes import router
from .api.sim_routes import router as sim_router

STATIC_DIR = Path(__file__).resolve().parent / "static"
log = logging.getLogger("hestia")

# Demo logins (HESTIA_DEMO_USERS=true), only ever allowed in digital-twin mode,
# so they can never reach a real installation. Published in the README so
# anyone can explore the twin.
DEMO_USERS = (
    ("visitor", "hestia-visitor", Role.VIEWER, "Visitor (read-only)"),
    ("operator", "hestia-operator", Role.OPERATOR, "Demo operator"),
)


def _seed_demo(users: UserStore, maintenance: MaintenanceLog, journal: SafetyJournal) -> None:
    """Demo logins, and a maintenance history so the twin starts with a healthy, documented sensor."""
    for username, password, role, name in DEMO_USERS:
        if users.by_name(username) is None:
            users.create(username, password, role, name)
    if journal.head_hash() != "0" * 64:
        return  # already seeded on a previous start
    seed_maintenance(maintenance)


def build_link(settings: Settings) -> DeviceLink:
    return TwinLink(settings) if settings.mode == "twin" else MqttLink(settings)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(settings.db_path)
        journal = SafetyJournal(db, settings.journal_secret())
        plan = MaintenancePlan(
            settings.bump_test_days,
            settings.calibration_days,
            settings.inspection_days,
            grace_days=settings.maintenance_grace_days,
        )
        maintenance = MaintenanceLog(db, journal, plan)
        users = UserStore(db)
        if settings.demo_users:
            _seed_demo(users, maintenance, journal)
        try:
            advisor: MLAdvisor | None = MLAdvisor(background=True)
        except Exception:  # missing runtime or manifest: run without advice
            log.exception("ML advisor unavailable")
            advisor = None
        engine = Engine(
            settings, journal, maintenance, build_link(settings), advisor, Notifier(settings, journal)
        )
        sims = SimulationManager(settings) if settings.sim_enabled else None
        app.state.services = Services(
            settings=settings,
            db=db,
            journal=journal,
            maintenance=maintenance,
            users=users,
            throttle=LoginThrottle(db, settings.login_max_failures, settings.login_lockout_minutes),
            sessions=SessionCodec(settings.session_secret(), settings.session_hours * 3600),
            engine=engine,
            sims=sims,
        )
        if users.count() == 0:
            log.warning("No users yet. Create the first admin with:  hestia create-user <name> --role admin")
        await engine.start()
        if sims:
            await sims.start()
        try:
            yield
        finally:
            if sims:
                await sims.stop()
            await engine.stop()
            db.close()

    app = FastAPI(
        title="Hestia gateway API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.include_router(router)
    app.include_router(sim_router)

    # Validation errors say what is wrong, never echo what was sent: the
    # rejected value may be a password.
    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{k: v for k, v in e.items() if k not in ("input", "ctx", "url")} for e in exc.errors()]
        return JSONResponse({"detail": jsonable_encoder(errors)}, status_code=422)

    app.add_middleware(SecurityHeaders, hsts=bool(settings.cookie_secure))

    if STATIC_DIR.exists():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

        # Any other path is a dashboard route (/safety, /planner...): serve the
        # single-page app and let it route. Unknown /api/ paths stay 404 JSON.
        # index.html is never cached, so a gateway update reaches every browser;
        # the hashed files under /assets can be cached forever.
        @app.get("/{path:path}", include_in_schema=False, response_model=None)
        def spa(path: str, request: Request) -> FileResponse | JSONResponse:
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            candidate = (STATIC_DIR / path).resolve()
            if path and candidate.is_file() and STATIC_DIR in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def __getattr__(name: str) -> FastAPI:
    # `uvicorn hestia.main:app` builds the app lazily, so importing this module
    # (tests, CLI) never reads the environment or opens the database.
    if name == "app":
        return create_app()
    raise AttributeError(name)
