"""Activity log and settings."""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from .. import auth, db, settings_store
from ..auth import Principal, admin, current
from ..security import ip_allowed

router = APIRouter(prefix="/api", tags=["admin"])


@router.get("/activity")
def activity(actor_type: str | None = None, q: str = "", status: str | None = None,
             before: float | None = None, limit: int = Query(100, le=1000), p: Principal = Depends(current)):
    clauses, params = [], []
    if not p.is_admin:
        clauses.append("(actor = ? OR actor LIKE ?)")
        params += [p.username, p.username + "/%"]
    if actor_type:
        clauses.append("actor_type=?")
        params.append(actor_type)
    if status:
        clauses.append("status=?")
        params.append(status)
    if q:
        clauses.append("(action LIKE ? OR target LIKE ? OR actor LIKE ?)")
        params += [f"%{q}%"] * 3
    if before:
        clauses.append("ts < ?")
        params.append(before)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = db.all_(f"SELECT * FROM activity {where} ORDER BY ts DESC LIMIT ?", (*params, limit))
    return [dict(r) | {"details": db.loads(r["details"], None)} for r in rows]


@router.get("/activity/export.csv")
def export_activity(p: Principal = Depends(admin)):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["time", "actor_type", "actor", "action", "target", "status", "ip", "details"])
    import datetime
    for r in db.all_("SELECT * FROM activity ORDER BY ts DESC LIMIT 100000"):
        w.writerow([datetime.datetime.fromtimestamp(r["ts"]).isoformat(), r["actor_type"], r["actor"], r["action"],
                    r["target"], r["status"], r["ip"], r["details"]])
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="activity-log.csv"'})


EDITABLE = {
    "server_name": str, "trash_retention_days": int, "versions_keep": int, "versions_max_file_mb": int,
    "index_interval_minutes": int, "index_max_content_mb": int, "allow_tailscale": bool, "allow_lan": bool,
    "extra_allowed_cidrs": list, "public_url": str, "session_idle_minutes": int, "require_2fa_for_admin": bool,
    "mcp_enabled": bool, "ai_output_folder": str, "ai_max_read_chars": int, "ai_approval_expiry_hours": int,
    "local_ai_enabled": bool, "local_ai_url": str, "local_ai_model": str, "theme": str,
}


@router.get("/settings")
def get_settings(p: Principal = Depends(current)):
    s = settings_store.all_settings()
    if not p.is_admin:
        return {k: s[k] for k in ("server_name", "theme", "public_url")}
    return s


@router.patch("/settings")
def update_settings(changes: dict[str, Any], p: Principal = Depends(admin)):
    for k, v in changes.items():
        if k not in EDITABLE:
            raise HTTPException(400, f"Setting '{k}' cannot be changed here")
        typ = EDITABLE[k]
        if typ is int:
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise HTTPException(400, f"{k} must be a number")
            if v < 0:
                raise HTTPException(400, f"{k} must be positive")
        elif not isinstance(v, typ):
            raise HTTPException(400, f"{k} has the wrong type")
        if k == "extra_allowed_cidrs":
            import ipaddress
            for c in v:
                try:
                    ipaddress.ip_network(str(c), strict=False)
                except ValueError:
                    raise HTTPException(400, f"Invalid network: {c}")
        if k == "public_url" and v and not v.startswith(("https://", "http://")):
            raise HTTPException(400, "Public URL must start with https://")
        if k == "local_ai_url" and not any(h in v for h in ("://127.0.0.1", "://localhost", "://[::1]")):
            raise HTTPException(400, "Local AI must run on this PC (http://127.0.0.1:11434)")
        if k == "allow_tailscale" and v is False and p.ip and not ip_allowed(p.ip, False,
                                                                              changes.get("allow_lan", settings_store.get("allow_lan")),
                                                                              settings_store.get("extra_allowed_cidrs")):
            raise HTTPException(400, "This change would lock out your current connection")
        settings_store.set(k, v)
    auth.log(p, "settings.update", ", ".join(changes.keys()), details=changes)
    return settings_store.all_settings()
