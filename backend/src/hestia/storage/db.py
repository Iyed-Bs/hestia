"""
The gateway's local database (SQLite, one file under HESTIA_DATA_DIR).

SQLite fits a building gateway: no server to run or secure, one file to back
up, and WAL mode lets the dashboard read while the control loop writes. One
connection is shared behind a lock (the gateway is a single process), and
the schema is versioned so an upgraded gateway migrates its old data.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1

MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE journal (
        id        INTEGER PRIMARY KEY,
        ts        TEXT    NOT NULL,      -- ISO-8601 UTC
        kind      TEXT    NOT NULL,
        severity  TEXT    NOT NULL,      -- info | warning | critical
        actor     TEXT    NOT NULL,      -- 'system', 'device:<id>' or a user name
        summary   TEXT    NOT NULL,
        details   TEXT    NOT NULL,      -- canonical JSON
        prev_hash TEXT    NOT NULL,
        hash      TEXT    NOT NULL,      -- SHA-256 chained over the previous entry
        mac       TEXT    NOT NULL       -- HMAC-SHA256 of hash with the gateway's journal key
    );
    CREATE INDEX journal_ts ON journal(ts);
    CREATE INDEX journal_kind ON journal(kind);

    CREATE TABLE maintenance (
        id           INTEGER PRIMARY KEY,
        kind         TEXT NOT NULL,      -- bump_test | calibration | inspection | sensor_replaced
        sensor       TEXT NOT NULL,      -- h2 | temperature | ph | installation
        performed_at TEXT NOT NULL,
        technician   TEXT NOT NULL,
        result       TEXT NOT NULL,      -- pass | fail
        data         TEXT NOT NULL,      -- JSON (gas concentrations, readings, ...)
        notes        TEXT NOT NULL,
        journal_id   INTEGER NOT NULL REFERENCES journal(id)
    );

    CREATE TABLE users (
        id            INTEGER PRIMARY KEY,
        username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
        display_name  TEXT NOT NULL,
        role          TEXT NOT NULL,     -- viewer | operator | admin
        password_hash TEXT NOT NULL,     -- argon2id
        created_at    TEXT NOT NULL,
        disabled      INTEGER NOT NULL DEFAULT 0,
        session_version INTEGER NOT NULL DEFAULT 0  -- bumped to revoke every open session
    );

    CREATE TABLE login_failures (
        key        TEXT NOT NULL,      -- 'user:<name>' or 'ip:<address>'
        failed_at  REAL NOT NULL       -- unix time
    );
    CREATE INDEX login_failures_key ON login_failures(key, failed_at);

    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
}


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._migrate()

    def _migrate(self) -> None:
        # executescript() commits any open transaction first, so each migration
        # carries its own BEGIN/COMMIT instead of running inside transaction().
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            for target in range(version + 1, SCHEMA_VERSION + 1):
                self._conn.executescript(
                    f"BEGIN;\n{MIGRATIONS[target]}\nPRAGMA user_version = {int(target)};\nCOMMIT;"
                )

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Cursor]:
        """Run statements atomically (BEGIN IMMEDIATE … COMMIT/ROLLBACK)."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                yield cur
            except BaseException:
                cur.execute("ROLLBACK")
                raise
            else:
                cur.execute("COMMIT")
            finally:
                cur.close()

    def query(self, sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
