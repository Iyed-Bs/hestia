"""
Request dependencies: the services, the logged-in user, role and CSRF checks.

Every route declares the role it needs (viewer, operator, admin). Requests
that change something (POST, PUT, PATCH, DELETE) must also carry the
session's CSRF token in the X-CSRF-Token header.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from hestia.runtime.engine import Engine
from hestia.security.auth import LoginThrottle, Role, SessionCodec, User, UserStore
from hestia.settings import Settings
from hestia.sim.manager import SimulationManager
from hestia.storage.db import Database
from hestia.trust.journal import SafetyJournal
from hestia.trust.maintenance import MaintenanceLog

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class Services:
    settings: Settings
    db: Database
    journal: SafetyJournal
    maintenance: MaintenanceLog
    users: UserStore
    throttle: LoginThrottle
    sessions: SessionCodec
    engine: Engine
    sims: SimulationManager | None = None  # None when HESTIA_SIM_ENABLED=false


def services(request: Request) -> Services:
    svc: Services = request.app.state.services
    return svc


ServicesDep = Annotated[Services, Depends(services)]


@dataclass(frozen=True)
class Principal:
    user: User
    csrf: str


def current_principal(request: Request, svc: ServicesDep) -> Principal:
    token = request.cookies.get(SessionCodec.COOKIE)
    session = svc.sessions.read(token) if token else None
    user = svc.users.get(session.user_id) if session else None
    if session is None or user is None or user.disabled or user.session_version != session.version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in")
    if request.method in UNSAFE_METHODS:
        sent = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(sent, session.csrf):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing or invalid CSRF token")
    return Principal(user, session.csrf)


def require(role: Role) -> Callable[..., Principal]:
    def check(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
        if principal.user.role < role:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"This needs the {role.label} role")
        return principal

    return check


Viewer = Annotated[Principal, Depends(require(Role.VIEWER))]
Operator = Annotated[Principal, Depends(require(Role.OPERATOR))]
Admin = Annotated[Principal, Depends(require(Role.ADMIN))]


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
