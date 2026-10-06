"""
Users, roles, sessions and login protection.

Roles (each includes the one above it):
    viewer    sees everything: live state, safety page, journal, reports
    operator  + sends commands, resets alarms (with a reason), records
              maintenance, and in the digital twin injects faults
    admin     + manages users

Passwords are hashed with argon2id (the Password Hashing Competition winner,
OWASP's first recommendation), never stored or logged in clear.

Sessions are signed cookies (HttpOnly, SameSite=Strict, Secure in
production) carrying the user id and a session version. Changing a password,
disabling a user or "log out everywhere" bumps the version, which instantly
invalidates every cookie issued before.

CSRF: SameSite=Strict already blocks cross-site requests; as defence in
depth every state-changing request must also echo the CSRF token (a random
value tied to the session) in the X-CSRF-Token header.

Brute force: after N failed logins for a user or from an address within the
lock-out window, further attempts are refused until the window passes. Every
failure is written to the safety journal.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from hestia.storage.db import Database

MIN_PASSWORD_LENGTH = 12
_hasher = PasswordHasher()  # argon2id with the library's current OWASP-aligned defaults
# A real hash of a random password, used to keep failed logins for unknown
# users as slow as for known ones (no user enumeration by timing).
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


class Role(IntEnum):
    VIEWER = 1
    OPERATOR = 2
    ADMIN = 3

    @classmethod
    def parse(cls, value: str) -> Role:
        return cls[value.upper()]

    @property
    def label(self) -> str:
        return self.name.lower()


@dataclass(frozen=True)
class User:
    id: int
    username: str
    display_name: str
    role: Role
    disabled: bool
    session_version: int

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role.label,
            "disabled": self.disabled,
        }


class AuthError(Exception):
    """Login refused. The message is safe to show to the user."""


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Passwords need at least {MIN_PASSWORD_LENGTH} characters")
    if password.lower() in {"passwordpassword", "hestiahestia", "123456789012"}:
        raise ValueError("This password is too common")


class UserStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _row(r: sqlite3.Row) -> User:
        return User(
            r["id"],
            r["username"],
            r["display_name"],
            Role.parse(r["role"]),
            bool(r["disabled"]),
            r["session_version"],
        )

    def count(self) -> int:
        return int(self.db.query("SELECT COUNT(*) AS n FROM users")[0]["n"])

    def get(self, user_id: int) -> User | None:
        rows = self.db.query("SELECT * FROM users WHERE id = ?", (user_id,))
        return self._row(rows[0]) if rows else None

    def by_name(self, username: str) -> User | None:
        rows = self.db.query("SELECT * FROM users WHERE username = ?", (username,))
        return self._row(rows[0]) if rows else None

    def all(self) -> list[User]:
        return [self._row(r) for r in self.db.query("SELECT * FROM users ORDER BY username")]

    def create(self, username: str, password: str, role: Role, display_name: str = "") -> User:
        if (
            not username.isascii()
            or not username.replace("-", "").replace("_", "").replace(".", "").isalnum()
        ):
            raise ValueError("Usernames may contain letters, digits, '.', '-' and '_' only")
        validate_password(password)
        with self.db.transaction() as cur:
            cur.execute(
                "INSERT INTO users (username, display_name, role, password_hash, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    username,
                    display_name or username,
                    role.label,
                    _hasher.hash(password),
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )
        user = self.by_name(username)
        if user is None:  # pragma: no cover - the row was just inserted
            raise RuntimeError("user not found after insert")
        return user

    def set_password(self, user_id: int, password: str) -> None:
        validate_password(password)
        with self.db.transaction() as cur:
            cur.execute(
                "UPDATE users SET password_hash = ?, session_version = session_version + 1 WHERE id = ?",
                (_hasher.hash(password), user_id),
            )

    def update(self, user_id: int, *, role: Role | None = None, disabled: bool | None = None) -> None:
        with self.db.transaction() as cur:
            if role is not None:
                cur.execute(
                    "UPDATE users SET role = ?, session_version = session_version + 1 WHERE id = ?",
                    (role.label, user_id),
                )
            if disabled is not None:
                cur.execute(
                    "UPDATE users SET disabled = ?, session_version = session_version + 1 WHERE id = ?",
                    (int(disabled), user_id),
                )

    def revoke_sessions(self, user_id: int) -> None:
        with self.db.transaction() as cur:
            cur.execute("UPDATE users SET session_version = session_version + 1 WHERE id = ?", (user_id,))

    def verify(self, username: str, password: str) -> User | None:
        rows = self.db.query("SELECT * FROM users WHERE username = ?", (username,))
        if not rows:
            # Same cost as a real check, so response time does not reveal
            # whether the username exists.
            with contextlib.suppress(VerifyMismatchError):
                _hasher.verify(_DUMMY_HASH, password)
            return None
        row = rows[0]
        try:
            _hasher.verify(row["password_hash"], password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return None
        if _hasher.check_needs_rehash(row["password_hash"]):
            with self.db.transaction() as cur:
                cur.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?", (_hasher.hash(password), row["id"])
                )
        user = self._row(row)
        return None if user.disabled else user


class LoginThrottle:
    """Counts recent failures per user and per client address."""

    def __init__(self, db: Database, max_failures: int, window_minutes: int) -> None:
        self.db = db
        self.max_failures = max_failures
        self.window_s = window_minutes * 60

    def _count(self, key: str, now: float) -> int:
        rows = self.db.query(
            "SELECT COUNT(*) AS n FROM login_failures WHERE key = ? AND failed_at > ?",
            (key, now - self.window_s),
        )
        return int(rows[0]["n"])

    def check(self, username: str, ip: str) -> None:
        now = time.time()
        if (
            max(self._count(f"user:{username.lower()}", now), self._count(f"ip:{ip}", now))
            >= self.max_failures
        ):
            raise AuthError("Too many failed attempts. Try again later.")

    def failed(self, username: str, ip: str) -> None:
        now = time.time()
        with self.db.transaction() as cur:
            cur.execute("DELETE FROM login_failures WHERE failed_at < ?", (now - self.window_s,))
            cur.executemany(
                "INSERT INTO login_failures (key, failed_at) VALUES (?, ?)",
                [(f"user:{username.lower()}", now), (f"ip:{ip}", now)],
            )

    def succeeded(self, username: str) -> None:
        with self.db.transaction() as cur:
            cur.execute("DELETE FROM login_failures WHERE key = ?", (f"user:{username.lower()}",))


@dataclass(frozen=True)
class Session:
    user_id: int
    version: int
    csrf: str


class SessionCodec:
    """Signs and checks session cookies."""

    COOKIE = "hestia_session"

    def __init__(self, secret: str, max_age_s: int) -> None:
        self._s = URLSafeTimedSerializer(secret, salt="hestia.session.v1")
        self._csrf_key = hashlib.sha256(("csrf:" + secret).encode()).digest()
        self.max_age_s = max_age_s

    def issue(self, user: User) -> tuple[str, str]:
        nonce = secrets.token_urlsafe(16)
        csrf = hmac.new(self._csrf_key, nonce.encode(), hashlib.sha256).hexdigest()
        token = self._s.dumps({"u": user.id, "v": user.session_version, "n": nonce})
        return token, csrf

    def read(self, token: str) -> Session | None:
        try:
            data = self._s.loads(token, max_age=self.max_age_s)
        except (BadSignature, SignatureExpired):
            return None
        csrf = hmac.new(self._csrf_key, str(data["n"]).encode(), hashlib.sha256).hexdigest()
        return Session(int(data["u"]), int(data["v"]), csrf)
