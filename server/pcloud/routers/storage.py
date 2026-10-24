"""Approved storage locations (roots) and storage statistics."""
from __future__ import annotations

import os
import shutil

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import auth, db
from ..auth import Principal, admin, current
from ..paths import PathError, check_new_root, get_root
from ..services import index
from ..services.jobs import get_manager
from ..services.monitor import monitor

router = APIRouter(prefix="/api", tags=["storage"])


class RootBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    path: str = Field(max_length=1024)
    read_only: bool = False
    webdav: bool = True
    ai_read: bool = False
    ai_write: bool = False
    versioning: bool = True
    index_content: bool = True
    create: bool = False


@router.get("/roots")
def roots(p: Principal = Depends(current)):
    out = []
    for r in p.visible_roots():
        d = r.to_dict() | {"access": p.root_access(r), "available": os.path.isdir(r.path)}
        if d["available"]:
            u = shutil.disk_usage(r.path)
            d["disk"] = {"total": u.total, "used": u.used, "free": u.free}
        out.append(d)
    return out


@router.post("/roots")
def add_root(body: RootBody, p: Principal = Depends(admin)):
    if body.create:
        try:
            os.makedirs(body.path, exist_ok=True)
        except OSError as e:
            raise HTTPException(400, f"Could not create folder: {e.strerror or e}")
    try:
        path = check_new_root(body.path)
    except PathError as e:
        raise HTTPException(400, str(e))
    if not path.is_dir():
        raise HTTPException(400, "Folder does not exist. Tick 'create folder' to create it.")
    if db.one("SELECT 1 FROM roots WHERE name=?", (body.name.strip(),)):
        raise HTTPException(409, "A storage location with that name already exists")
    if any(c in body.name for c in '/\\:*?"<>|'):
        raise HTTPException(400, "Name cannot contain / \\ : * ? \" < > |")
    cur = db.execute("INSERT INTO roots(name, path, read_only, webdav, ai_read, ai_write, versioning, index_content, created_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?)",
                     (body.name.strip(), str(path), int(body.read_only), int(body.webdav), int(body.ai_read),
                      int(body.ai_write and body.ai_read), int(body.versioning), int(body.index_content), db.now()))
    auth.log(p, "root.add", str(path), details=body.model_dump(exclude={"create"}))
    get_manager().submit("index_scan", {}, created_by=p.label)
    return get_root(cur.lastrowid).to_dict()


class RootUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    read_only: bool | None = None
    webdav: bool | None = None
    ai_read: bool | None = None
    ai_write: bool | None = None
    versioning: bool | None = None
    index_content: bool | None = None


@router.patch("/roots/{root_id}")
def update_root(root_id: int, body: RootUpdate, p: Principal = Depends(admin)):
    root = get_root(root_id)
    changes = body.model_dump(exclude_none=True)
    if changes.get("ai_read") is False:
        changes["ai_write"] = False
    if changes.get("ai_write") and not (changes.get("ai_read", root.ai_read)):
        raise HTTPException(400, "AI write access requires AI read access")
    for k, v in changes.items():
        db.execute(f"UPDATE roots SET {k}=? WHERE id=?", (v if k == "name" else int(v), root_id))
    auth.log(p, "root.update", root.name, details=changes)
    return get_root(root_id).to_dict()


@router.delete("/roots/{root_id}")
def remove_root(root_id: int, p: Principal = Depends(admin)):
    """Stops sharing a folder. Files on disk are NOT touched."""
    root = get_root(root_id)
    db.execute("DELETE FROM file_index WHERE root_id=?", (root_id,))
    db.execute("DELETE FROM content_fts WHERE root_id=?", (root_id,))
    db.execute("DELETE FROM roots WHERE id=?", (root_id,))
    auth.log(p, "root.remove", root.path)
    return {"ok": True}


@router.get("/fs/browse")
def browse_host(path: str = "", p: Principal = Depends(admin)):
    """Folder picker for the setup wizard (admin only, folders only, no file contents)."""
    if not path:
        import psutil
        drives = []
        for part in psutil.disk_partitions(all=False):
            try:
                u = shutil.disk_usage(part.mountpoint)
                drives.append({"name": part.mountpoint, "path": part.mountpoint, "free": u.free, "total": u.total})
            except OSError:
                continue
        home = os.path.expanduser("~")
        shortcuts = [os.path.join(home, d) for d in ("Documents", "Pictures", "Music", "Videos", "Downloads", "Desktop")]
        return {"path": "", "parent": None, "folders": drives,
                "shortcuts": [{"name": os.path.basename(s), "path": s} for s in shortcuts if os.path.isdir(s)]}
    real = os.path.realpath(path)
    if not os.path.isdir(real):
        raise HTTPException(404, "Folder not found")
    folders = []
    try:
        with os.scandir(real) as it:
            for e in it:
                try:
                    if e.is_dir() and not e.name.startswith("$") and e.name not in ("System Volume Information",):
                        folders.append({"name": e.name, "path": e.path})
                except OSError:
                    continue
    except PermissionError:
        raise HTTPException(403, "Windows denied access to this folder")
    parent = os.path.dirname(real)
    return {"path": real, "parent": parent if parent != real else "", "folders": sorted(folders, key=lambda f: f["name"].lower())}


@router.get("/storage/stats")
def storage_stats(p: Principal = Depends(current)):
    roots = p.visible_roots()
    ids = [r.id for r in roots]
    stats = index.stats(ids)
    drives: dict[str, dict] = {}
    for r in roots:
        if not os.path.isdir(r.path):
            continue
        drive = os.path.splitdrive(r.real)[0] or "/"
        if drive not in drives:
            u = shutil.disk_usage(r.real)
            drives[drive] = {"drive": drive, "total": u.total, "used": u.used, "free": u.free, "roots": []}
        drives[drive]["roots"].append(r.name)
    health = monitor.disk_health() if p.is_admin else []
    activity = [dict(r) for r in db.all_(
        "SELECT action, COUNT(*) AS count FROM activity WHERE action IN ('file.upload','file.download','file.download_zip',"
        "'dav.put','dav.get') AND ts > ? GROUP BY action", (db.now() - 7 * 86400,))]
    return stats | {"drives": list(drives.values()), "health": health, "transfer_activity_7d": activity,
                    "total": sum(d["total"] for d in drives.values()),
                    "used": sum(d["used"] for d in drives.values()),
                    "free": sum(d["free"] for d in drives.values())}
