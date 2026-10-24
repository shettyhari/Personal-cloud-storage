"""File explorer API: browse, preview, download, organize, trash, versions, search."""
from __future__ import annotations

import mimetypes
import os
import secrets
import time
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from .. import auth
from ..auth import Principal, current, writer
from ..paths import NotFound, PathError, resolve
from ..services import extract, fileops, index
from ..services.fileops import Conflict

router = APIRouter(prefix="/api", tags=["files"])

UNSAFE_INLINE = {"text/html", "image/svg+xml", "application/xhtml+xml", "text/xml", "application/xml",
                 "application/javascript", "text/javascript"}
SANDBOX_CSP = "sandbox; default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'"


def handle_errors(fn):
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except NotFound as e:
            raise HTTPException(404, str(e))
        except PathError as e:
            raise HTTPException(400, str(e))
        except Conflict as e:
            raise HTTPException(409, str(e))
        except PermissionError as e:
            raise HTTPException(403, f"Windows denied access: {e.strerror or e}")
        except FileNotFoundError:
            raise HTTPException(404, "File or folder not found")
        except OSError as e:
            raise HTTPException(500, f"File system error: {e.strerror or e}")
    return wrapper


class Item(BaseModel):
    root_id: int
    path: str = Field(max_length=4096)


# ------------------------------------------------------------- browse
@router.get("/files/list")
@handle_errors
def list_files(root_id: int, path: str = "", show_hidden: bool = False, p: Principal = Depends(current)):
    root = p.require_root(root_id)
    items = fileops.list_dir(root, path, show_hidden)
    _, norm = resolve(root, path)
    return {"root": root.to_dict() | {"access": p.root_access(root)}, "path": norm, "items": items}


@router.get("/files/stat")
@handle_errors
def stat_file(root_id: int, path: str, p: Principal = Depends(current)):
    root = p.require_root(root_id)
    return fileops.stat_path(root, path)


@router.get("/files/tree")
@handle_errors
def folder_tree(root_id: int, path: str = "", p: Principal = Depends(current)):
    """Sub-folders only - used by the move/copy destination picker."""
    root = p.require_root(root_id)
    return [i for i in fileops.list_dir(root, path) if i["is_dir"]]


class MkdirBody(Item):
    name: str = Field(max_length=255)


@router.post("/files/mkdir")
@handle_errors
def mkdir(body: MkdirBody, p: Principal = Depends(writer)):
    root = p.require_root(body.root_id, write=True)
    e = fileops.make_dir(root, body.path, body.name)
    auth.log(p, "file.mkdir", f"{root.name}/{e['path']}")
    return e


class RenameBody(Item):
    new_name: str = Field(max_length=255)


@router.post("/files/rename")
@handle_errors
def rename(body: RenameBody, p: Principal = Depends(writer)):
    root = p.require_root(body.root_id, write=True)
    e = fileops.rename(root, body.path, body.new_name)
    auth.log(p, "file.rename", f"{root.name}/{body.path}", details={"to": e["path"]})
    return e


class TransferBody(BaseModel):
    items: list[Item] = Field(min_length=1, max_length=1000)
    dest_root_id: int
    dest_dir: str = ""
    as_copy: bool = False
    on_conflict: str = Field(default="rename", pattern=r"^(rename|overwrite|fail)$")


@router.post("/files/transfer")
@handle_errors
def transfer(body: TransferBody, p: Principal = Depends(writer)):
    dst = p.require_root(body.dest_root_id, write=True)
    results = []
    for it in body.items:
        src = p.require_root(it.root_id, write=not body.as_copy)
        try:
            e = fileops.transfer(src, it.path, dst, body.dest_dir, body.as_copy, body.on_conflict)
            results.append({"path": it.path, "ok": True, "result": e})
        except (PathError, NotFound, Conflict, OSError) as ex:
            results.append({"path": it.path, "ok": False, "error": str(ex)})
        auth.log(p, "file.copy" if body.as_copy else "file.move", f"{src.name}/{it.path}",
                 "ok" if results[-1]["ok"] else "error", {"to": f"{dst.name}/{body.dest_dir}"})
    return {"results": results}


class DeleteBody(BaseModel):
    items: list[Item] = Field(min_length=1, max_length=1000)
    permanent: bool = False


@router.post("/files/delete")
@handle_errors
def delete(body: DeleteBody, p: Principal = Depends(writer)):
    if body.permanent and not p.is_admin:
        raise HTTPException(403, "Only administrators can permanently delete files")
    results = []
    for it in body.items:
        root = p.require_root(it.root_id, write=True)
        try:
            r = fileops.delete(root, it.path, actor=p.label, permanent=body.permanent)
            results.append({"path": it.path, "ok": True, **r})
        except (PathError, NotFound, OSError) as ex:
            results.append({"path": it.path, "ok": False, "error": str(ex)})
        auth.log(p, "file.delete_permanent" if body.permanent else "file.delete", f"{root.name}/{it.path}",
                 "ok" if results[-1]["ok"] else "error")
    return {"results": results}


class WriteBody(Item):
    content: str = Field(max_length=20 * 1024 * 1024)
    overwrite: bool = False


@router.post("/files/write")
@handle_errors
def write_text(body: WriteBody, p: Principal = Depends(writer)):
    root = p.require_root(body.root_id, write=True)
    e = fileops.write_bytes(root, body.path, body.content.encode("utf-8"), body.overwrite, reason="edited")
    auth.log(p, "file.write", f"{root.name}/{body.path}")
    return e


# ---------------------------------------------------------- download
def file_response(abs_path: str, inline: bool, filename: str | None = None) -> FileResponse:
    name = filename or os.path.basename(abs_path)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if inline and mime in UNSAFE_INLINE:
        mime = "text/plain; charset=utf-8"
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}
    if mime != "application/pdf":
        headers["Content-Security-Policy"] = SANDBOX_CSP
    return FileResponse(abs_path, media_type=mime, filename=name, headers=headers,
                        content_disposition_type="inline" if inline else "attachment")


@router.get("/files/download")
@handle_errors
def download(root_id: int, path: str, inline: bool = False, p: Principal = Depends(current)):
    root = p.require_root(root_id)
    abs_path, norm = resolve(root, path, must_exist=True)
    if os.path.isdir(abs_path):
        raise HTTPException(400, "Use the ZIP download for folders")
    if not inline:
        auth.log(p, "file.download", f"{root.name}/{norm}")
    return file_response(abs_path, inline)


_zip_tickets: dict[str, tuple[int, str, str, float]] = {}


class ZipBody(BaseModel):
    items: list[Item] = Field(min_length=1, max_length=5000)
    name: str = Field(default="download.zip", max_length=200)


@router.post("/files/zip")
@handle_errors
def make_zip(body: ZipBody, p: Principal = Depends(current)):
    """Build a ZIP of the selection and return a single-use download ticket."""
    tmp = str(fileops.safe_tmp_path(".zip"))
    total = 0
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        for it in body.items:
            root = p.require_root(it.root_id)
            abs_path, norm = resolve(root, it.path, must_exist=True)
            base = os.path.dirname(abs_path)
            if os.path.isdir(abs_path):
                for dirpath, _, files in os.walk(abs_path):
                    for f in files:
                        fp = os.path.join(dirpath, f)
                        z.write(fp, os.path.relpath(fp, base))
                        total += 1
            else:
                z.write(abs_path, os.path.basename(abs_path))
                total += 1
    ticket = secrets.token_urlsafe(24)
    name = body.name if body.name.lower().endswith(".zip") else body.name + ".zip"
    _zip_tickets[ticket] = (p.user_id, tmp, name, time.time())
    auth.log(p, "file.download_zip", f"{len(body.items)} items", details={"files": total})
    return {"ticket": ticket, "files": total, "size": os.path.getsize(tmp)}


@router.get("/files/zip/{ticket}")
def get_zip(ticket: str, p: Principal = Depends(current)):
    entry = _zip_tickets.pop(ticket, None)
    if entry is None or entry[0] != p.user_id or time.time() - entry[3] > 3600:
        raise HTTPException(404, "Download expired")
    _, tmp, name, _ = entry
    resp = file_response(tmp, False, name)
    resp.background = BackgroundTask(lambda: os.path.exists(tmp) and os.remove(tmp))
    return resp


@router.get("/files/text")
@handle_errors
def text_preview(root_id: int, path: str, max_chars: int = Query(200_000, le=2_000_000), p: Principal = Depends(current)):
    root = p.require_root(root_id)
    abs_path, _ = resolve(root, path, must_exist=True)
    try:
        text = extract.extract_text(abs_path, max_chars)
    except ValueError as e:
        raise HTTPException(415, str(e))
    return PlainTextResponse(text, headers={"X-Truncated": str(len(text) >= max_chars).lower()})


@router.get("/files/checksum")
@handle_errors
def checksum(root_id: int, path: str, algo: str = Query("sha256", pattern=r"^(sha256|sha1|md5)$"),
             p: Principal = Depends(current)):
    root = p.require_root(root_id)
    abs_path, norm = resolve(root, path, must_exist=True)
    if os.path.isdir(abs_path):
        raise HTTPException(400, "Checksums are computed for files")
    if os.path.getsize(abs_path) > 2 * 1024**3:
        raise HTTPException(413, "File is large - run the 'Compute file checksum' background task instead")
    return {"path": norm, "algorithm": algo, "checksum": fileops.checksum(abs_path, algo)}


# ---------------------------------------------------------- versions
@router.get("/files/versions")
@handle_errors
def versions(root_id: int, path: str, p: Principal = Depends(current)):
    root = p.require_root(root_id)
    return fileops.list_versions(root, path)


class RestoreVersion(BaseModel):
    root_id: int


@router.post("/files/versions/{version_id}/restore")
@handle_errors
def restore_version(version_id: int, body: RestoreVersion, p: Principal = Depends(writer)):
    root = p.require_root(body.root_id, write=True)
    e = fileops.restore_version(root, version_id)
    auth.log(p, "file.version_restore", f"{root.name}/{e['path']}", details={"version_id": version_id})
    return e


@router.get("/files/versions/{version_id}/download")
@handle_errors
def download_version(version_id: int, root_id: int, p: Principal = Depends(current)):
    from .. import db
    root = p.require_root(root_id)
    v = db.one("SELECT * FROM versions WHERE id=? AND root_id=?", (version_id, root.id))
    if v is None:
        raise HTTPException(404, "Version not found")
    base, ext = os.path.splitext(os.path.basename(v["rel_path"]))
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(v["mtime"]))
    return file_response(v["stored_path"], False, f"{base} ({stamp}){ext}")


# ------------------------------------------------------------- trash
@router.get("/trash")
def trash(p: Principal = Depends(current)):
    return fileops.list_trash([r.id for r in p.visible_roots()])


def _trash_root_check(trash_id: int, p: Principal):
    from .. import db
    t = db.one("SELECT root_id FROM trash WHERE id=?", (trash_id,))
    if t is None:
        raise HTTPException(404, "Item not in recycle bin")
    p.require_root(t["root_id"], write=True)


@router.post("/trash/{trash_id}/restore")
@handle_errors
def trash_restore(trash_id: int, p: Principal = Depends(writer)):
    _trash_root_check(trash_id, p)
    e = fileops.restore_trash(trash_id)
    auth.log(p, "trash.restore", e["path"])
    return e


@router.delete("/trash/{trash_id}")
@handle_errors
def trash_purge(trash_id: int, p: Principal = Depends(writer)):
    _trash_root_check(trash_id, p)
    fileops.purge_trash(trash_id)
    auth.log(p, "trash.purge", str(trash_id))
    return {"ok": True}


@router.post("/trash/empty")
@handle_errors
def trash_empty(p: Principal = Depends(writer)):
    ids = [r.id for r in p.visible_roots() if p.root_access(r) == "rw"]
    items = fileops.list_trash(ids)
    for t in items:
        fileops.purge_trash(t["id"])
    auth.log(p, "trash.empty", f"{len(items)} items")
    return {"purged": len(items)}


# ------------------------------------------------------------ search
@router.get("/search")
def search(q: str = "", root_id: int | None = None, ext: str = "", folder: str = "",
           after: float | None = None, before: float | None = None, content: bool = True,
           limit: int = Query(100, le=500), p: Principal = Depends(current)):
    roots = [r.id for r in p.visible_roots()]
    if root_id is not None:
        roots = [r for r in roots if r == root_id]
    exts = [e for e in ext.replace(" ", "").split(",") if e]
    return index.search(roots, q, exts or None, folder or None, after, before, content=content, limit=limit)


@router.get("/files/recent")
def recent(limit: int = Query(20, le=100), p: Principal = Depends(current)):
    return index.search([r.id for r in p.visible_roots()], "", limit=limit)
