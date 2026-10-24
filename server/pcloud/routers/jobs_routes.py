"""Background tasks, approved apps/scripts and remote desktop."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from .. import auth, db, settings_store
from ..auth import Principal, admin, current, writer
from ..services import remote
from ..services.jobs import HANDLERS, TITLES, get_manager, job_dict, read_log
from ..services.tasks import CONVERSIONS

router = APIRouter(prefix="/api", tags=["tasks"])

# job type -> (needs write access, admin only)
USER_JOBS = {
    "compress": (True, False), "extract": (True, False), "inventory": (True, False), "search_dir": (False, False),
    "checksums": (False, False), "find_duplicates": (False, False), "batch_extract_text": (True, False),
    "convert": (True, False), "hash_file": (False, False), "index_scan": (False, True),
    "run_script": (False, True),
}


@router.get("/jobs/types")
def job_types(p: Principal = Depends(current)):
    return [{"type": t, "title": TITLES[t], "write": w, "admin": a} for t, (w, a) in USER_JOBS.items()
            if t in HANDLERS] + [{"conversions": sorted(f"{a}->{b}" for a, b in CONVERSIONS)}]


class JobBody(BaseModel):
    type: str
    params: dict = Field(default_factory=dict)


@router.post("/jobs")
def submit_job(body: JobBody, p: Principal = Depends(writer)):
    if body.type not in USER_JOBS:
        raise HTTPException(400, "This task type cannot be started directly")
    write, admin_only = USER_JOBS[body.type]
    if admin_only and not p.is_admin:
        raise HTTPException(403, "Administrator access required")
    if "root_id" in body.params:
        p.require_root(int(body.params["root_id"]), write=write)
    elif body.type not in ("index_scan", "run_script"):
        raise HTTPException(400, "root_id is required")
    job_id = get_manager().submit(body.type, body.params, created_by=p.label)
    auth.log(p, f"job.start.{body.type}", f"job {job_id}", details=body.params)
    return {"id": job_id}


@router.get("/jobs")
def list_jobs(status: str | None = None, limit: int = Query(50, le=500), p: Principal = Depends(current)):
    if status:
        rows = db.all_("SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit))
    else:
        rows = db.all_("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
    jobs = [job_dict(r) for r in rows]
    if not p.is_admin:
        jobs = [j for j in jobs if j["created_by"].split("/")[0] == p.username]
    return jobs


def _job_for(job_id: int, p: Principal):
    r = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if r is None or (not p.is_admin and r["created_by"].split("/")[0] != p.username):
        raise HTTPException(404, "Task not found")
    return r


@router.get("/jobs/{job_id}")
def get_job(job_id: int, p: Principal = Depends(current)):
    return job_dict(_job_for(job_id, p))


@router.get("/jobs/{job_id}/log", response_class=PlainTextResponse)
def job_log(job_id: int, p: Principal = Depends(current)):
    _job_for(job_id, p)
    return read_log(job_id)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, p: Principal = Depends(writer)):
    _job_for(job_id, p)
    get_manager().cancel(job_id)
    auth.log(p, "job.cancel", f"job {job_id}")
    return {"ok": True}


# ------------------------------------------------- approved apps & scripts
class AppBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: str = Field(pattern=r"^(app|script)$")
    executable: str = Field(max_length=1024)
    args: list[str] = Field(default_factory=list, max_length=50)
    working_dir: str | None = None
    allow_ai: bool = False
    timeout_seconds: int = Field(default=3600, ge=5, le=86400)


@router.get("/apps")
def list_apps(p: Principal = Depends(current)):
    rows = db.all_("SELECT * FROM apps ORDER BY kind, name")
    return [dict(r) | {"args": db.loads(r["args"], []), "status": remote.app_status(r["id"]) if r["kind"] == "app" else None}
            for r in rows]


@router.post("/apps")
def add_app(body: AppBody, p: Principal = Depends(admin)):
    try:
        exe = remote.validate_executable(body.executable, body.kind)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if body.working_dir and not os.path.isdir(body.working_dir):
        raise HTTPException(400, "Working folder does not exist")
    try:
        cur = db.execute("INSERT INTO apps(name, kind, executable, args, working_dir, allow_ai, timeout_seconds, created_at) "
                         "VALUES(?,?,?,?,?,?,?,?)", (body.name, body.kind, exe, db.dumps(body.args), body.working_dir,
                                                     int(body.allow_ai), body.timeout_seconds, db.now()))
    except Exception:
        raise HTTPException(409, "An entry with that name already exists")
    auth.log(p, "app.register", body.name, details={"executable": exe, "kind": body.kind})
    return {"id": cur.lastrowid}


@router.delete("/apps/{app_id}")
def remove_app(app_id: int, p: Principal = Depends(admin)):
    remote.stop_app(app_id)
    db.execute("DELETE FROM apps WHERE id=?", (app_id,))
    auth.log(p, "app.remove", str(app_id))
    return {"ok": True}


@router.post("/apps/{app_id}/start")
def start_app(app_id: int, p: Principal = Depends(admin)):
    row = db.one("SELECT * FROM apps WHERE id=?", (app_id,))
    if row is None:
        raise HTTPException(404, "Not registered")
    if row["kind"] == "script":
        job_id = get_manager().submit("run_script", {"app_id": app_id}, created_by=p.label)
        auth.log(p, "script.run", row["name"], details={"job_id": job_id})
        return {"job_id": job_id}
    st = remote.start_app(dict(row))
    auth.log(p, "app.start", row["name"])
    return st


@router.post("/apps/{app_id}/stop")
def stop_app(app_id: int, p: Principal = Depends(admin)):
    st = remote.stop_app(app_id)
    auth.log(p, "app.stop", str(app_id))
    return st


# ------------------------------------------------------- remote desktop
@router.get("/remote-desktop")
def rd_status(p: Principal = Depends(current)):
    return remote.remote_desktop_status()


class RdConfig(BaseModel):
    provider: str = Field(pattern=r"^(none|rustdesk|rdp|vnc)$")
    rustdesk_id: str = Field(default="", max_length=40)
    rdp_host: str = Field(default="", max_length=255)
    notes: str = Field(default="", max_length=1000)


@router.put("/remote-desktop")
def rd_config(body: RdConfig, p: Principal = Depends(admin)):
    settings_store.set("remote_desktop", body.model_dump())
    auth.log(p, "remote_desktop.configure", body.provider)
    return body.model_dump()


@router.get("/remote-desktop/connection.rdp")
def rd_file(p: Principal = Depends(current)):
    cfg = settings_store.get("remote_desktop") or {}
    host = cfg.get("rdp_host") or (remote.tailscale_status().get("dns_name") or "")
    if not host:
        raise HTTPException(400, "Set the RDP host (your PC's Tailscale name) in Remote Computer settings first")
    auth.log(p, "remote_desktop.rdp_file", host)
    return Response(remote.rdp_file(host), media_type="application/x-rdp",
                    headers={"Content-Disposition": 'attachment; filename="HomePC.rdp"'})
