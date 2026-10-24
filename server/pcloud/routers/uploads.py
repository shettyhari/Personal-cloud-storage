"""Resumable chunked uploads.

Protocol:
  POST   /api/uploads                     -> create (or resume) a session, returns {id, received, chunk_size}
  PUT    /api/uploads/{id}?offset=N       -> append raw bytes at offset N (must equal bytes received so far)
  GET    /api/uploads/{id}                -> how many bytes the server has (resume point)
  POST   /api/uploads/{id}/complete       -> verify size (+ optional SHA-256) and move into place
  DELETE /api/uploads/{id}                -> abort
Partial data lives in the data folder, so uploads survive browser reloads, network drops and server restarts.
"""
from __future__ import annotations

import hashlib
import os
import uuid

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import auth, db
from ..auth import Principal, writer
from ..config import get_config
from ..paths import PathError, normalize_rel, resolve, validate_name
from ..services import fileops
from ..services.fileops import Conflict

router = APIRouter(prefix="/api/uploads", tags=["uploads"])
CHUNK_SIZE = 8 * 1024 * 1024
MAX_CHUNK = 64 * 1024 * 1024


class CreateUpload(BaseModel):
    root_id: int
    dir: str = Field(default="", max_length=4096)
    filename: str = Field(max_length=255)
    size: int = Field(ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    overwrite: bool = False


def _part(upload_id: str) -> str:
    return str(get_config().uploads_dir / f"{upload_id}.part")


def _get(upload_id: str, p: Principal):
    row = db.one("SELECT * FROM uploads WHERE id=?", (upload_id,))
    if row is None or row["user_id"] != p.user_id:
        raise HTTPException(404, "Upload not found")
    return row


@router.post("")
def create(body: CreateUpload, p: Principal = Depends(writer)):
    root = p.require_root(body.root_id, write=True)
    try:
        name = validate_name(body.filename)
        rel_dir = normalize_rel(body.dir)
        resolve(root, rel_dir)
    except PathError as e:
        raise HTTPException(400, str(e))
    if body.size > get_config().max_upload_bytes:
        raise HTTPException(413, f"File exceeds the {get_config().max_upload_bytes // 1024**3} GB upload limit")
    # Free space check on the destination drive
    try:
        free = __import__("shutil").disk_usage(root.real).free
        if body.size > free:
            raise HTTPException(507, "Not enough free space on the destination drive")
    except OSError:
        pass
    existing = db.one("SELECT * FROM uploads WHERE user_id=? AND root_id=? AND rel_dir=? AND filename=? AND size=? "
                      "AND status='active' ORDER BY updated_at DESC", (p.user_id, root.id, rel_dir, name, body.size))
    if existing and os.path.exists(_part(existing["id"])):
        received = os.path.getsize(_part(existing["id"]))
        db.execute("UPDATE uploads SET received=?, overwrite=?, updated_at=? WHERE id=?",
                   (received, int(body.overwrite), db.now(), existing["id"]))
        return {"id": existing["id"], "received": received, "chunk_size": CHUNK_SIZE, "resumed": True}
    upload_id = uuid.uuid4().hex
    open(_part(upload_id), "wb").close()
    t = db.now()
    db.execute("INSERT INTO uploads(id, user_id, root_id, rel_dir, filename, size, sha256, overwrite, created_at, updated_at) "
               "VALUES(?,?,?,?,?,?,?,?,?,?)", (upload_id, p.user_id, root.id, rel_dir, name, body.size,
                                               (body.sha256 or "").lower() or None, int(body.overwrite), t, t))
    return {"id": upload_id, "received": 0, "chunk_size": CHUNK_SIZE, "resumed": False}


@router.get("/{upload_id}")
def status(upload_id: str, p: Principal = Depends(writer)):
    row = _get(upload_id, p)
    part = _part(upload_id)
    received = os.path.getsize(part) if os.path.exists(part) else 0
    return {"id": upload_id, "received": received, "size": row["size"], "status": row["status"],
            "chunk_size": CHUNK_SIZE}


@router.put("/{upload_id}")
async def put_chunk(upload_id: str, offset: int, request: Request, p: Principal = Depends(writer)):
    row = await anyio.to_thread.run_sync(_get, upload_id, p)
    if row["status"] != "active":
        raise HTTPException(409, f"Upload is {row['status']}")
    part = _part(upload_id)
    current_size = os.path.getsize(part) if os.path.exists(part) else 0
    if offset != current_size:
        raise HTTPException(409, detail={"message": "Offset mismatch", "received": current_size})
    written = 0
    with open(part, "ab") as f:
        async for chunk in request.stream():
            written += len(chunk)
            if written > MAX_CHUNK or current_size + written > row["size"]:
                f.truncate(current_size)
                raise HTTPException(413, "Chunk too large or exceeds declared file size")
            await anyio.to_thread.run_sync(f.write, chunk)
    received = current_size + written
    await anyio.to_thread.run_sync(lambda: db.execute("UPDATE uploads SET received=?, updated_at=? WHERE id=?",
                                                       (received, db.now(), upload_id)))
    return {"received": received}


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@router.post("/{upload_id}/complete")
def complete(upload_id: str, p: Principal = Depends(writer)):
    row = _get(upload_id, p)
    if row["status"] != "active":
        raise HTTPException(409, f"Upload is {row['status']}")
    root = p.require_root(row["root_id"], write=True)
    part = _part(upload_id)
    received = os.path.getsize(part) if os.path.exists(part) else 0
    if received != row["size"]:
        raise HTTPException(409, detail={"message": "Upload incomplete", "received": received})
    digest = _sha256(part)
    if row["sha256"] and digest != row["sha256"]:
        db.execute("UPDATE uploads SET status='aborted' WHERE id=?", (upload_id,))
        os.remove(part)
        raise HTTPException(422, "Checksum mismatch - the file was corrupted in transit. Please upload again.")
    try:
        e = fileops.place_file(root, part, row["rel_dir"], row["filename"], bool(row["overwrite"]))
    except PathError as ex:
        raise HTTPException(400, str(ex))
    except Conflict as ex:
        raise HTTPException(409, str(ex))
    db.execute("UPDATE uploads SET status='complete', updated_at=? WHERE id=?", (db.now(), upload_id))
    auth.log(p, "file.upload", f"{root.name}/{e['path']}", details={"size": row["size"], "sha256": digest})
    return e | {"sha256": digest}


@router.delete("/{upload_id}")
def abort(upload_id: str, p: Principal = Depends(writer)):
    _get(upload_id, p)
    part = _part(upload_id)
    if os.path.exists(part):
        os.remove(part)
    db.execute("UPDATE uploads SET status='aborted' WHERE id=?", (upload_id,))
    return {"ok": True}
