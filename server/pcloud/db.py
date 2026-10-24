"""SQLite persistence: connection handling and schema migrations."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

from .config import get_config

_local = threading.local()
_write_lock = threading.RLock()

MIGRATIONS: list[str] = [
    # 1 - core schema
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user',          -- admin | user | readonly
        totp_secret TEXT,
        totp_enabled INTEGER NOT NULL DEFAULT 0,
        disabled INTEGER NOT NULL DEFAULT 0,
        failed_logins INTEGER NOT NULL DEFAULT 0,
        locked_until REAL NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        password_changed_at REAL NOT NULL
    );
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY,                         -- sha256 of cookie token
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        csrf TEXT NOT NULL,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        last_seen REAL NOT NULL,
        ip TEXT, user_agent TEXT,
        mfa_pending INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE tokens (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,                          -- device | webdav | mcp | api
        token_hash TEXT NOT NULL UNIQUE,
        prefix TEXT NOT NULL,
        scopes TEXT NOT NULL DEFAULT '[]',
        read_only INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        last_used REAL, last_ip TEXT,
        expires_at REAL,
        revoked INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE roots (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        path TEXT NOT NULL UNIQUE COLLATE NOCASE,
        read_only INTEGER NOT NULL DEFAULT 0,
        webdav INTEGER NOT NULL DEFAULT 1,
        ai_read INTEGER NOT NULL DEFAULT 0,
        ai_write INTEGER NOT NULL DEFAULT 0,
        versioning INTEGER NOT NULL DEFAULT 1,
        index_content INTEGER NOT NULL DEFAULT 1,
        created_at REAL NOT NULL
    );
    CREATE TABLE root_permissions (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        root_id INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
        can_write INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, root_id)
    );
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE activity (
        id INTEGER PRIMARY KEY,
        ts REAL NOT NULL,
        actor_type TEXT NOT NULL,                    -- user | ai | device | system
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        target TEXT,
        status TEXT NOT NULL DEFAULT 'ok',
        ip TEXT,
        details TEXT
    );
    CREATE INDEX idx_activity_ts ON activity(ts DESC);
    CREATE INDEX idx_activity_actor ON activity(actor_type, ts DESC);
    CREATE TABLE uploads (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        root_id INTEGER NOT NULL,
        rel_dir TEXT NOT NULL,
        filename TEXT NOT NULL,
        size INTEGER NOT NULL,
        received INTEGER NOT NULL DEFAULT 0,
        sha256 TEXT,
        overwrite INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'active'        -- active | complete | aborted
    );
    CREATE TABLE trash (
        id INTEGER PRIMARY KEY,
        root_id INTEGER NOT NULL,
        rel_path TEXT NOT NULL,
        stored_path TEXT NOT NULL,
        is_dir INTEGER NOT NULL,
        size INTEGER NOT NULL,
        deleted_at REAL NOT NULL,
        deleted_by TEXT NOT NULL
    );
    CREATE TABLE versions (
        id INTEGER PRIMARY KEY,
        root_id INTEGER NOT NULL,
        rel_path TEXT NOT NULL COLLATE NOCASE,
        stored_path TEXT NOT NULL,
        size INTEGER NOT NULL,
        mtime REAL NOT NULL,
        sha256 TEXT,
        created_at REAL NOT NULL,
        reason TEXT
    );
    CREATE INDEX idx_versions_path ON versions(root_id, rel_path);
    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY,
        type TEXT NOT NULL,
        params TEXT NOT NULL,
        status TEXT NOT NULL,                        -- queued | running | succeeded | failed | cancelled | interrupted
        progress REAL NOT NULL DEFAULT 0,
        message TEXT,
        result TEXT,
        source TEXT NOT NULL,                        -- user | ai | scheduler
        created_by TEXT NOT NULL,
        created_at REAL NOT NULL,
        started_at REAL, finished_at REAL
    );
    CREATE INDEX idx_jobs_status ON jobs(status, created_at DESC);
    CREATE TABLE approvals (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,
        params TEXT NOT NULL,
        summary TEXT NOT NULL,
        requested_by TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',      -- pending | approved | rejected | executed | failed | expired
        created_at REAL NOT NULL,
        decided_at REAL, decided_by TEXT,
        result TEXT
    );
    CREATE TABLE sync_pairs (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        root_id INTEGER NOT NULL,
        rel_path TEXT NOT NULL,
        dest_path TEXT NOT NULL,
        mode TEXT NOT NULL,                          -- mirror | two_way
        interval_minutes INTEGER NOT NULL DEFAULT 60,
        enabled INTEGER NOT NULL DEFAULT 1,
        exclude TEXT NOT NULL DEFAULT '[]',
        last_run REAL, last_status TEXT, last_error TEXT,
        created_at REAL NOT NULL
    );
    CREATE TABLE sync_state (
        pair_id INTEGER NOT NULL,
        rel_path TEXT NOT NULL,
        src_size INTEGER, src_mtime REAL,
        dst_size INTEGER, dst_mtime REAL,
        PRIMARY KEY (pair_id, rel_path)
    );
    CREATE TABLE sync_conflicts (
        id INTEGER PRIMARY KEY,
        pair_id INTEGER NOT NULL,
        rel_path TEXT NOT NULL,
        detail TEXT NOT NULL,
        created_at REAL NOT NULL,
        resolved INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE backup_plans (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        root_ids TEXT NOT NULL,
        destination TEXT NOT NULL,
        encrypted INTEGER NOT NULL DEFAULT 1,
        interval_hours INTEGER NOT NULL DEFAULT 24,
        keep_snapshots INTEGER NOT NULL DEFAULT 30,
        enabled INTEGER NOT NULL DEFAULT 1,
        last_run REAL, last_status TEXT, last_error TEXT,
        created_at REAL NOT NULL
    );
    CREATE TABLE file_index (
        root_id INTEGER NOT NULL,
        rel_path TEXT NOT NULL COLLATE NOCASE,
        name TEXT NOT NULL,
        ext TEXT NOT NULL,
        is_dir INTEGER NOT NULL,
        size INTEGER NOT NULL,
        mtime REAL NOT NULL,
        content_indexed INTEGER NOT NULL DEFAULT 0,
        seen INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (root_id, rel_path)
    );
    CREATE INDEX idx_index_name ON file_index(name);
    CREATE INDEX idx_index_mtime ON file_index(mtime DESC);
    CREATE INDEX idx_index_size ON file_index(size DESC);
    CREATE VIRTUAL TABLE content_fts USING fts5(root_id UNINDEXED, rel_path UNINDEXED, name, body, tokenize='unicode61');
    CREATE TABLE apps (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL,                          -- app | script
        executable TEXT NOT NULL,
        args TEXT NOT NULL DEFAULT '[]',
        working_dir TEXT,
        allow_ai INTEGER NOT NULL DEFAULT 0,
        timeout_seconds INTEGER NOT NULL DEFAULT 3600,
        created_at REAL NOT NULL
    );
    """,
]


def _connect() -> sqlite3.Connection:
    cfg = get_config()
    conn = sqlite3.connect(cfg.db_path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    path = str(get_config().db_path)
    if c is None or getattr(_local, "path", None) != path:
        c = _connect()
        _local.conn = c
        _local.path = path
    return c


def close_thread_connection() -> None:
    c = getattr(_local, "conn", None)
    if c is not None:
        c.close()
        _local.conn = None


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """Serialized write transaction."""
    c = conn()
    with _write_lock:
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise


def execute(sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
    with _write_lock:
        return conn().execute(sql, params)


def one(sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
    return conn().execute(sql, params).fetchone()


def all_(sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
    return conn().execute(sql, params).fetchall()


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def migrate() -> None:
    c = conn()
    c.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = c.execute("SELECT version FROM schema_version").fetchone()
    current = row[0] if row else 0
    if row is None:
        c.execute("INSERT INTO schema_version (version) VALUES (0)")
    for i, script in enumerate(MIGRATIONS[current:], start=current + 1):
        with _write_lock:
            c.executescript("BEGIN;" + script + f";UPDATE schema_version SET version={i};COMMIT;")


def now() -> float:
    return time.time()


def dumps(v: Any) -> str:
    return json.dumps(v, separators=(",", ":"), default=str)


def loads(v: str | None, default: Any = None) -> Any:
    if not v:
        return default
    try:
        return json.loads(v)
    except ValueError:
        return default
