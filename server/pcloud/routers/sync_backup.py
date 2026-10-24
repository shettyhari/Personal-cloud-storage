"""Sync pairs, backup plans, snapshots and restore."""
from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import auth, db
from ..auth import Principal, admin, current
from ..paths import get_root, resolve
from ..services import backup, sync
from ..services.jobs import get_manager

router = APIRouter(prefix="/api", tags=["sync-backup"])


# ------------------------------------------------------------------ sync
class SyncBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    root_id: int
    rel_path: str = ""
    dest_path: str = Field(max_length=1024)
    mode: str = Field(pattern=r"^(mirror|two_way)$")
    interval_minutes: int = Field(default=60, ge=5, le=10080)
    exclude: list[str] = Field(default_factory=lambda: ["*.tmp", "~$*", "Thumbs.db", "desktop.ini"])
    enabled: bool = True


@router.get("/sync")
def list_sync(p: Principal = Depends(current)):
    pairs = [dict(r) | {"exclude": db.loads(r["exclude"], [])} for r in db.all_("SELECT * FROM sync_pairs ORDER BY name")]
    for pr in pairs:
        pr["open_conflicts"] = db.one("SELECT COUNT(*) AS n FROM sync_conflicts WHERE pair_id=? AND resolved=0",
                                      (pr["id"],))["n"]
    return pairs


@router.post("/sync")
def add_sync(body: SyncBody, p: Principal = Depends(admin)):
    root = get_root(body.root_id)
    src, rel = resolve(root, body.rel_path, must_exist=True)
    try:
        dest = sync.validate_dest(body.dest_path, src)
    except ValueError as e:
        raise HTTPException(400, str(e))
    cur = db.execute("INSERT INTO sync_pairs(name, root_id, rel_path, dest_path, mode, interval_minutes, enabled, exclude, created_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?)", (body.name, root.id, rel, dest, body.mode, body.interval_minutes,
                                                   int(body.enabled), db.dumps(body.exclude), db.now()))
    auth.log(p, "sync.create", body.name, details={"src": f"{root.name}/{rel}", "dest": dest, "mode": body.mode})
    return {"id": cur.lastrowid}


class SyncUpdate(BaseModel):
    enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=5, le=10080)


@router.patch("/sync/{pair_id}")
def update_sync(pair_id: int, body: SyncUpdate, p: Principal = Depends(admin)):
    if body.enabled is not None:
        db.execute("UPDATE sync_pairs SET enabled=? WHERE id=?", (int(body.enabled), pair_id))
    if body.interval_minutes is not None:
        db.execute("UPDATE sync_pairs SET interval_minutes=? WHERE id=?", (body.interval_minutes, pair_id))
    auth.log(p, "sync.update", str(pair_id), details=body.model_dump(exclude_none=True))
    return {"ok": True}


@router.delete("/sync/{pair_id}")
def delete_sync(pair_id: int, p: Principal = Depends(admin)):
    db.execute("DELETE FROM sync_state WHERE pair_id=?", (pair_id,))
    db.execute("DELETE FROM sync_conflicts WHERE pair_id=?", (pair_id,))
    db.execute("DELETE FROM sync_pairs WHERE id=?", (pair_id,))
    auth.log(p, "sync.delete", str(pair_id))
    return {"ok": True}


@router.post("/sync/{pair_id}/run")
def run_sync(pair_id: int, p: Principal = Depends(admin)):
    if db.one("SELECT 1 FROM sync_pairs WHERE id=?", (pair_id,)) is None:
        raise HTTPException(404, "Sync pair not found")
    return {"job_id": get_manager().submit("sync", {"pair_id": pair_id}, created_by=p.label)}


@router.get("/sync/{pair_id}/conflicts")
def conflicts(pair_id: int, p: Principal = Depends(current)):
    return [dict(r) for r in db.all_("SELECT * FROM sync_conflicts WHERE pair_id=? ORDER BY created_at DESC LIMIT 200",
                                     (pair_id,))]


@router.post("/sync/conflicts/{conflict_id}/resolve")
def resolve_conflict(conflict_id: int, p: Principal = Depends(admin)):
    db.execute("UPDATE sync_conflicts SET resolved=1 WHERE id=?", (conflict_id,))
    return {"ok": True}


# ---------------------------------------------------------------- backup
class BackupBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    root_ids: list[int] = Field(min_length=1)
    destination: str = Field(max_length=1024)
    encrypted: bool = True
    interval_hours: int = Field(default=24, ge=1, le=24 * 30)
    keep_snapshots: int = Field(default=30, ge=1, le=1000)


@router.get("/backups")
def list_backups(p: Principal = Depends(current)):
    out = []
    for r in db.all_("SELECT * FROM backup_plans ORDER BY name"):
        d = dict(r) | {"root_ids": db.loads(r["root_ids"], [])}
        d["has_key"] = bool(backup.secrets_store().get(backup.key_name(r["id"]))) if r["encrypted"] else None
        d["destination_available"] = os.path.isdir(r["destination"])
        out.append(d)
    return out


@router.post("/backups")
def add_backup(body: BackupBody, p: Principal = Depends(admin)):
    dest = body.destination.strip()
    if not os.path.isabs(dest):
        raise HTTPException(400, "Destination must be a full path, e.g. E:\\Backups")
    try:
        os.makedirs(dest, exist_ok=True)
    except OSError as e:
        raise HTTPException(400, f"Cannot create destination: {e.strerror or e}")
    for rid in body.root_ids:
        root = get_root(rid)
        try:
            sync.validate_dest(dest, root.real)
        except ValueError as e:
            raise HTTPException(400, f"{root.name}: {e}")
    cur = db.execute("INSERT INTO backup_plans(name, root_ids, destination, encrypted, interval_hours, keep_snapshots, created_at) "
                     "VALUES(?,?,?,?,?,?,?)", (body.name, db.dumps(body.root_ids), os.path.realpath(dest),
                                               int(body.encrypted), body.interval_hours, body.keep_snapshots, db.now()))
    plan_id = cur.lastrowid
    recovery = None
    if body.encrypted:
        key = secrets.token_bytes(32)
        backup.secrets_store().set(backup.key_name(plan_id), key)
        recovery = backup.encode_recovery_key(key)
    auth.log(p, "backup.create", body.name, details={"destination": dest, "encrypted": body.encrypted})
    return {"id": plan_id, "recovery_key": recovery,
            "notice": "Store the recovery key somewhere safe (password manager or printed). Without it, encrypted "
                      "backups cannot be restored on another PC." if recovery else None}


class BackupUpdate(BaseModel):
    enabled: bool | None = None
    interval_hours: int | None = Field(default=None, ge=1, le=24 * 30)
    keep_snapshots: int | None = Field(default=None, ge=1, le=1000)


@router.patch("/backups/{plan_id}")
def update_backup(plan_id: int, body: BackupUpdate, p: Principal = Depends(admin)):
    for k, v in body.model_dump(exclude_none=True).items():
        db.execute(f"UPDATE backup_plans SET {k}=? WHERE id=?", (int(v), plan_id))
    auth.log(p, "backup.update", str(plan_id), details=body.model_dump(exclude_none=True))
    return {"ok": True}


@router.delete("/backups/{plan_id}")
def delete_backup(plan_id: int, p: Principal = Depends(admin)):
    """Removes the plan only. Existing backup data at the destination is left untouched."""
    db.execute("DELETE FROM backup_plans WHERE id=?", (plan_id,))
    auth.log(p, "backup.delete", str(plan_id))
    return {"ok": True}


@router.post("/backups/{plan_id}/run")
def run_backup(plan_id: int, p: Principal = Depends(admin)):
    return {"job_id": get_manager().submit("backup", {"plan_id": plan_id}, created_by=p.label)}


@router.post("/backups/{plan_id}/verify")
def verify_backup(plan_id: int, snapshot: str | None = None, p: Principal = Depends(admin)):
    return {"job_id": get_manager().submit("backup_verify", {"plan_id": plan_id, "snapshot": snapshot},
                                           created_by=p.label)}


@router.get("/backups/{plan_id}/snapshots")
def snapshots(plan_id: int, p: Principal = Depends(admin)):
    plan = db.one("SELECT * FROM backup_plans WHERE id=?", (plan_id,))
    if plan is None:
        raise HTTPException(404, "Plan not found")
    try:
        repo = backup.Repo.for_plan(plan)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    out = []
    for s in repo.list_snapshots()[:200]:
        try:
            m = repo.read_snapshot(s)
            out.append({"id": s, "created": m["created"], "stats": m.get("stats"), "roots": m["roots"]})
        except Exception as e:
            out.append({"id": s, "error": str(e)})
    return out


@router.get("/backups/{plan_id}/snapshots/{snap_id}/files")
def snapshot_files(plan_id: int, snap_id: str, prefix: str = "", p: Principal = Depends(admin)):
    plan = db.one("SELECT * FROM backup_plans WHERE id=?", (plan_id,))
    repo = backup.Repo.for_plan(plan)
    m = repo.read_snapshot(snap_id)
    files = [f for f in m["files"] if f["path"].startswith(prefix)][:2000]
    return [{k: f[k] for k in ("root_id", "path", "size", "mtime")} for f in files]


class RestoreBody(BaseModel):
    snapshot: str = Field(pattern=r"^\d{8}-\d{6}$")
    target_root_id: int
    target_dir: str = ""
    prefix: str = ""
    source_root_id: int | None = None
    recovery_key: str | None = Field(default=None, max_length=100)


@router.post("/backups/{plan_id}/restore")
def restore(plan_id: int, body: RestoreBody, p: Principal = Depends(admin)):
    p.require_root(body.target_root_id, write=True)
    params = body.model_dump(exclude={"recovery_key"}) | {"plan_id": plan_id}
    if body.recovery_key:
        try:
            key = backup.decode_recovery_key(body.recovery_key)
        except Exception:
            raise HTTPException(400, "Recovery key is not valid")
        ref = secrets.token_hex(8)
        backup.TRANSIENT_KEYS[ref] = key
        params["key_ref"] = ref
    job_id = get_manager().submit("backup_restore", params, created_by=p.label)
    auth.log(p, "backup.restore", body.snapshot, details={"plan_id": plan_id, "prefix": body.prefix})
    return {"job_id": job_id}
