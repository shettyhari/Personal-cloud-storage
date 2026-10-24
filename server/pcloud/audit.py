"""Activity / audit log."""
from __future__ import annotations

import logging
from typing import Any

from . import db

log = logging.getLogger("pcloud.audit")


def record(actor_type: str, actor: str, action: str, target: str | None = None, status: str = "ok",
           ip: str | None = None, details: dict[str, Any] | None = None) -> None:
    try:
        db.execute("INSERT INTO activity(ts, actor_type, actor, action, target, status, ip, details) "
                   "VALUES(?,?,?,?,?,?,?,?)",
                   (db.now(), actor_type, actor, action, target, status, ip, db.dumps(details) if details else None))
    except Exception:  # audit must never break the request
        log.exception("failed to write audit record")
    log.info("%s %s %s %s %s", actor_type, actor, action, target or "", status)


def from_principal(p, action: str, target: str | None = None, status: str = "ok", details: dict | None = None):
    record(p.actor_type, p.label, action, target, status, p.ip, details)
