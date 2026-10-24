"""Health, overview, metrics, network status, diagnostics and secure shutdown."""
from __future__ import annotations

import os
import sys
import threading

import psutil
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import __version__, auth, db, settings_store
from ..auth import Principal, admin, current
from ..config import get_config
from ..security import verify_password
from ..services import local_ai, remote
from ..services.jobs import job_dict
from ..services.monitor import monitor

router = APIRouter(prefix="/api", tags=["system"])
SHUTDOWN_HOOK: list = []  # populated by main.py with a callable that stops the server


@router.get("/health")
def health():
    """Unauthenticated liveness probe - reveals nothing sensitive."""
    return {"status": "ok", "version": __version__}


@router.get("/system/metrics")
def metrics(p: Principal = Depends(current)):
    snap = monitor.snapshot()
    if not p.is_admin:
        snap.pop("process", None)
    return snap | {"history": list(monitor.history)}


@router.get("/system/overview")
def overview(p: Principal = Depends(current)):
    snap = monitor.snapshot()
    t = db.now()
    roots = p.visible_roots()
    ids = [r.id for r in roots]
    from ..services import index
    recent = index.search(ids, "", limit=8)
    jobs = [job_dict(r) for r in db.all_("SELECT * FROM jobs WHERE status IN ('queued','running') ORDER BY created_at DESC LIMIT 10")]
    uploads = [dict(r) for r in db.all_("SELECT id, filename, size, received, updated_at FROM uploads "
                                        "WHERE status='active' AND user_id=? AND updated_at > ? ORDER BY updated_at DESC LIMIT 10",
                                        (p.user_id, t - 3600))]
    devices = [dict(r) for r in db.all_("SELECT id, name, kind, last_used, last_ip FROM tokens WHERE revoked=0 AND "
                                        "last_used > ? ORDER BY last_used DESC LIMIT 10", (t - 86400,))]
    sessions = db.one("SELECT COUNT(*) AS n FROM sessions WHERE last_seen > ? AND mfa_pending=0", (t - 900,))["n"]
    ai = [dict(r) for r in db.all_("SELECT ts, actor, action, target, status FROM activity WHERE actor_type='ai' "
                                   "ORDER BY ts DESC LIMIT 8")]
    pending = db.one("SELECT COUNT(*) AS n FROM approvals WHERE status='pending'")["n"]
    sync = [dict(r) for r in db.all_("SELECT id, name, last_run, last_status, last_error FROM sync_pairs")]
    backups = [dict(r) for r in db.all_("SELECT id, name, last_run, last_status, last_error FROM backup_plans")]
    storage = []
    for r in roots:
        if os.path.isdir(r.path):
            u = psutil.disk_usage(r.path)
            storage.append({"root": r.name, "drive": os.path.splitdrive(r.real)[0], "total": u.total, "used": u.used,
                            "free": u.free})
    return {
        "server_name": settings_store.get("server_name"),
        "online": True,
        "uptime_seconds": t - snap["boot_time"],
        "agent_uptime_seconds": t - snap["agent_started"],
        "cpu": snap["cpu"]["percent"], "memory": snap["memory"], "network": snap["network"],
        "storage": storage, "recent_files": recent, "active_jobs": jobs, "active_uploads": uploads,
        "connected_devices": devices, "active_sessions": sessions, "ai_activity": ai,
        "pending_approvals": pending, "sync": sync, "backups": backups,
        "history": list(monitor.history)[-60:],
    }


@router.get("/system/network")
def network(request: Request, p: Principal = Depends(current)):
    cfg = get_config()
    ts = remote.tailscale_status()
    addrs = []
    for name, lst in psutil.net_if_addrs().items():
        for a in lst:
            if a.family.name in ("AF_INET", "AF_INET6") and not a.address.startswith(("127.", "::1", "fe80")):
                addrs.append({"interface": name, "address": a.address})
    return {
        "bind": f"{cfg.host}:{cfg.port}",
        "public_url": settings_store.get("public_url"),
        "request_base": str(request.base_url),
        "secure_request": request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https",
        "allow_tailscale": settings_store.get("allow_tailscale"),
        "allow_lan": settings_store.get("allow_lan"),
        "extra_allowed_cidrs": settings_store.get("extra_allowed_cidrs"),
        "tailscale": ts,
        "addresses": addrs if p.is_admin else [],
        "listening_on_all_interfaces": cfg.host in ("0.0.0.0", "::"),
    }


@router.get("/system/diagnostics")
def diagnostics(p: Principal = Depends(admin)):
    from ..diagnostics import run_checks
    return run_checks()


@router.get("/system/errors")
def errors(p: Principal = Depends(admin)):
    rows = db.all_("SELECT ts, actor_type, actor, action, target, status, details FROM activity "
                   "WHERE status IN ('error','failed','denied') ORDER BY ts DESC LIMIT 50")
    return {"recent": [dict(r) for r in rows], "runtime": list(monitor.errors),
            "failed_jobs": [job_dict(r) for r in db.all_("SELECT * FROM jobs WHERE status='failed' ORDER BY created_at DESC LIMIT 10")]}


class ShutdownBody(BaseModel):
    password: str = Field(max_length=256)
    restart: bool = False


@router.post("/system/shutdown")
def shutdown(body: ShutdownBody, p: Principal = Depends(admin)):
    """Stops the server agent (not Windows). Requires password re-entry."""
    u = db.one("SELECT password_hash FROM users WHERE id=?", (p.user_id,))
    if not verify_password(body.password, u["password_hash"]):
        auth.log(p, "system.shutdown", status="denied")
        raise HTTPException(403, "Password incorrect")
    auth.log(p, "system.restart" if body.restart else "system.shutdown")
    flag = get_config().data_dir / "stop.flag"
    if not body.restart:
        flag.write_text("stopped from dashboard")
    if SHUTDOWN_HOOK:
        threading.Timer(0.5, SHUTDOWN_HOOK[0]).start()
    return {"ok": True, "restart": body.restart}


@router.get("/system/info")
def info(p: Principal = Depends(current)):
    cfg = get_config()
    return {"version": __version__, "python": sys.version.split()[0], "platform": sys.platform,
            "data_dir": str(cfg.data_dir) if p.is_admin else None, "dev_mode": cfg.dev_mode,
            "local_ai": local_ai.status() if p.is_admin else None}
