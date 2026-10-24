"""Runtime settings persisted in SQLite, with typed defaults."""
from __future__ import annotations

from typing import Any

from . import db

DEFAULTS: dict[str, Any] = {
    "server_name": "Home PC",
    "setup_complete": False,
    "trash_retention_days": 30,
    "versions_keep": 20,
    "versions_max_file_mb": 512,
    "index_interval_minutes": 30,
    "index_max_content_mb": 10,
    # Network access: which client addresses may reach the server at all.
    # loopback is always allowed. tailscale = 100.64.0.0/10 + fd7a:115c:a1e0::/48.
    "allow_tailscale": True,
    "allow_lan": False,
    "extra_allowed_cidrs": [],
    "public_url": "",                 # e.g. https://my-pc.tail1234.ts.net (set after `tailscale serve`)
    "session_idle_minutes": 120,
    "require_2fa_for_admin": False,
    # AI integration
    "mcp_enabled": True,
    "ai_output_folder": "",           # "<root_id>:<rel path>" - where AI-generated docs land by default
    "ai_max_read_chars": 200_000,
    "ai_approval_expiry_hours": 72,
    "local_ai_enabled": False,
    "local_ai_url": "http://127.0.0.1:11434",
    "local_ai_model": "llama3.2",
    # Remote desktop
    "remote_desktop": {"provider": "none", "rustdesk_id": "", "rdp_host": "", "notes": ""},
    "theme": "light",
}


def get(key: str) -> Any:
    row = db.one("SELECT value FROM settings WHERE key=?", (key,))
    if row is None:
        return DEFAULTS.get(key)
    return db.loads(row["value"], DEFAULTS.get(key))


def set(key: str, value: Any) -> None:  # noqa: A001 - mirrors dict API
    db.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, db.dumps(value)))


def all_settings() -> dict[str, Any]:
    out = dict(DEFAULTS)
    for row in db.all_("SELECT key, value FROM settings"):
        out[row["key"]] = db.loads(row["value"], DEFAULTS.get(row["key"]))
    return out
