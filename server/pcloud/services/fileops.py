"""File operations inside approved roots. Shared by the REST API, WebDAV and MCP layers."""
from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
import stat
import uuid
from datetime import datetime
from pathlib import Path

from .. import db, settings_store
from ..config import get_config
from ..paths import NotFound, PathError, Root, get_root, normalize_rel, resolve, validate_name
from . import index

HIDDEN_ATTR = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 2)
SYSTEM_ATTR = getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 4)


class Conflict(Exception):
    pass


def file_kind(name: str, is_dir: bool) -> str:
    if is_dir:
        return "folder"
    ext = os.path.splitext(name)[1].lower()
    mime = mimetypes.guess_type(name)[0] or ""
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if ext == ".pdf":
        return "pdf"
    if ext in {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz"}:
        return "archive"
    if ext in {".xls", ".xlsx", ".csv", ".ods", ".tsv"}:
        return "spreadsheet"
    if ext in {".doc", ".docx", ".odt", ".rtf"}:
        return "document"
    if ext in {".ppt", ".pptx", ".odp"}:
        return "presentation"
    if ext in {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".rb",
               ".php", ".html", ".css", ".json", ".xml", ".yml", ".yaml", ".sh", ".ps1", ".sql", ".toml", ".ini"}:
        return "code"
    if mime.startswith("text/") or ext in {".md", ".txt", ".log"}:
        return "text"
    return "file"


def entry(root: Root, abs_path: str, rel: str, st: os.stat_result | None = None) -> dict:
    st = st or os.stat(abs_path)
    is_dir = stat.S_ISDIR(st.st_mode)
    name = os.path.basename(abs_path) if rel else root.name
    return {
        "name": name,
        "path": rel,
        "root_id": root.id,
        "is_dir": is_dir,
        "size": 0 if is_dir else st.st_size,
        "modified": st.st_mtime,
        "created": getattr(st, "st_birthtime", st.st_ctime),
        "kind": file_kind(name, is_dir),
        "mime": None if is_dir else (mimetypes.guess_type(name)[0] or "application/octet-stream"),
        "ext": "" if is_dir else os.path.splitext(name)[1].lower().lstrip("."),
    }


def _is_hidden(e: os.DirEntry) -> bool:
    try:
        attrs = getattr(e.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attrs & (HIDDEN_ATTR | SYSTEM_ATTR))


def list_dir(root: Root, rel: str, show_hidden: bool = False) -> list[dict]:
    abs_path, norm = resolve(root, rel, must_exist=True)
    if not os.path.isdir(abs_path):
        raise PathError("Not a folder")
    out = []
    with os.scandir(abs_path) as it:
        for e in it:
            if e.name in {".pcloud-tmp"}:
                continue
            if not show_hidden and (e.name.startswith("~$") or _is_hidden(e)):
                continue
            try:
                child_rel = f"{norm}/{e.name}" if norm else e.name
                # Skip links that escape the root.
                real = os.path.realpath(e.path)
                if e.is_symlink() or real != os.path.normpath(e.path):
                    try:
                        resolve(root, child_rel)
                    except PathError:
                        continue
                out.append(entry(root, e.path, child_rel, e.stat()))
            except (OSError, PathError):
                continue
    return out


def stat_path(root: Root, rel: str) -> dict:
    abs_path, norm = resolve(root, rel, must_exist=True)
    return entry(root, abs_path, norm)


def _unique_name(directory: str, name: str) -> str:
    base, ext = os.path.splitext(name)
    candidate, i = name, 1
    while os.path.exists(os.path.join(directory, candidate)):
        candidate = f"{base} ({i}){ext}"
        i += 1
    return candidate


def make_dir(root: Root, parent_rel: str, name: str) -> dict:
    name = validate_name(name)
    parent_abs, parent_norm = resolve(root, parent_rel, must_exist=True)
    target = os.path.join(parent_abs, name)
    if os.path.exists(target):
        raise Conflict(f"'{name}' already exists")
    os.makedirs(target)
    rel = f"{parent_norm}/{name}" if parent_norm else name
    index.upsert(root, target, rel)
    return entry(root, target, rel)


def make_dirs(root: Root, rel: str) -> str:
    abs_path, norm = resolve(root, rel)
    os.makedirs(abs_path, exist_ok=True)
    return abs_path


# ------------------------------------------------------------- versions
def snapshot_version(root: Root, rel: str, abs_path: str, reason: str) -> int | None:
    if not root.versioning or not os.path.isfile(abs_path):
        return None
    st = os.stat(abs_path)
    if st.st_size > int(settings_store.get("versions_max_file_mb")) * 1024 * 1024:
        return None
    cfg = get_config()
    dest_dir = cfg.versions_dir / str(root.id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stored = dest_dir / f"{uuid.uuid4().hex}{os.path.splitext(abs_path)[1]}"
    shutil.copy2(abs_path, stored)
    cur = db.execute("INSERT INTO versions(root_id, rel_path, stored_path, size, mtime, created_at, reason) "
                     "VALUES(?,?,?,?,?,?,?)", (root.id, rel, str(stored), st.st_size, st.st_mtime, db.now(), reason))
    prune_versions(root.id, rel)
    return cur.lastrowid


def prune_versions(root_id: int, rel: str) -> None:
    keep = int(settings_store.get("versions_keep"))
    rows = db.all_("SELECT id, stored_path FROM versions WHERE root_id=? AND rel_path=? ORDER BY created_at DESC",
                   (root_id, rel))
    for r in rows[keep:]:
        try:
            os.remove(r["stored_path"])
        except OSError:
            pass
        db.execute("DELETE FROM versions WHERE id=?", (r["id"],))


def list_versions(root: Root, rel: str) -> list[dict]:
    norm = normalize_rel(rel)
    return [dict(r) | {"stored_path": None} for r in
            db.all_("SELECT * FROM versions WHERE root_id=? AND rel_path=? ORDER BY created_at DESC", (root.id, norm))]


def restore_version(root: Root, version_id: int) -> dict:
    v = db.one("SELECT * FROM versions WHERE id=? AND root_id=?", (version_id, root.id))
    if v is None:
        raise NotFound("Version not found")
    abs_path, norm = resolve(root, v["rel_path"])
    if os.path.exists(abs_path):
        snapshot_version(root, norm, abs_path, "before restore")
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    tmp = abs_path + f".restore-{uuid.uuid4().hex[:8]}"
    shutil.copy2(v["stored_path"], tmp)
    os.replace(tmp, abs_path)
    index.upsert(root, abs_path, norm)
    return entry(root, abs_path, norm)


# ---------------------------------------------------------------- trash
def _dir_size(path: str) -> int:
    total = 0
    for dirpath, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def delete(root: Root, rel: str, actor: str, permanent: bool = False) -> dict:
    abs_path, norm = resolve(root, rel, must_exist=True)
    if not norm:
        raise PathError("The storage location itself cannot be deleted")
    is_dir = os.path.isdir(abs_path)
    if permanent:
        shutil.rmtree(abs_path) if is_dir else os.remove(abs_path)
        index.remove(root, norm)
        return {"trash_id": None}
    size = _dir_size(abs_path) if is_dir else os.path.getsize(abs_path)
    cfg = get_config()
    bucket = cfg.trash_dir / uuid.uuid4().hex
    bucket.mkdir(parents=True)
    stored = bucket / os.path.basename(abs_path)
    shutil.move(abs_path, stored)
    cur = db.execute("INSERT INTO trash(root_id, rel_path, stored_path, is_dir, size, deleted_at, deleted_by) "
                     "VALUES(?,?,?,?,?,?,?)", (root.id, norm, str(stored), int(is_dir), size, db.now(), actor))
    index.remove(root, norm)
    return {"trash_id": cur.lastrowid}


def list_trash(root_ids: list[int]) -> list[dict]:
    if not root_ids:
        return []
    q = ",".join("?" * len(root_ids))
    rows = db.all_(f"SELECT * FROM trash WHERE root_id IN ({q}) ORDER BY deleted_at DESC", tuple(root_ids))
    return [{k: r[k] for k in ("id", "root_id", "rel_path", "is_dir", "size", "deleted_at", "deleted_by")}
            | {"name": os.path.basename(r["rel_path"])} for r in rows]


def restore_trash(trash_id: int) -> dict:
    t = db.one("SELECT * FROM trash WHERE id=?", (trash_id,))
    if t is None:
        raise NotFound("Item not in recycle bin")
    root = get_root(t["root_id"])
    abs_path, norm = resolve(root, t["rel_path"])
    parent = os.path.dirname(abs_path)
    os.makedirs(parent, exist_ok=True)
    name = os.path.basename(abs_path)
    if os.path.exists(abs_path):
        name = _unique_name(parent, name)
    target = os.path.join(parent, name)
    shutil.move(t["stored_path"], target)
    try:
        os.rmdir(os.path.dirname(t["stored_path"]))
    except OSError:
        pass
    db.execute("DELETE FROM trash WHERE id=?", (trash_id,))
    rel = "/".join(filter(None, [os.path.dirname(norm).replace("\\", "/"), name]))
    index.upsert(root, target, rel, recursive=True)
    return entry(root, target, rel)


def purge_trash(trash_id: int) -> None:
    t = db.one("SELECT * FROM trash WHERE id=?", (trash_id,))
    if t is None:
        raise NotFound("Item not in recycle bin")
    bucket = os.path.dirname(t["stored_path"])
    shutil.rmtree(bucket, ignore_errors=True)
    db.execute("DELETE FROM trash WHERE id=?", (trash_id,))


def purge_expired_trash() -> int:
    days = int(settings_store.get("trash_retention_days"))
    cutoff = db.now() - days * 86400
    rows = db.all_("SELECT id FROM trash WHERE deleted_at < ?", (cutoff,))
    for r in rows:
        purge_trash(r["id"])
    return len(rows)


# ------------------------------------------------------- rename / move / copy
def rename(root: Root, rel: str, new_name: str) -> dict:
    new_name = validate_name(new_name)
    abs_path, norm = resolve(root, rel, must_exist=True)
    if not norm:
        raise PathError("The storage location itself cannot be renamed here")
    target = os.path.join(os.path.dirname(abs_path), new_name)
    if os.path.exists(target) and os.path.normcase(target) != os.path.normcase(abs_path):
        raise Conflict(f"'{new_name}' already exists")
    os.rename(abs_path, target)
    parent = os.path.dirname(norm).replace("\\", "/")
    new_rel = f"{parent}/{new_name}" if parent else new_name
    index.remove(root, norm)
    index.upsert(root, target, new_rel, recursive=True)
    return entry(root, target, new_rel)


def transfer(src_root: Root, src_rel: str, dst_root: Root, dst_dir_rel: str, copy: bool,
             on_conflict: str = "rename", new_name: str | None = None) -> dict:
    """Move or copy a file/folder into a destination folder. on_conflict: rename | overwrite | fail."""
    src_abs, src_norm = resolve(src_root, src_rel, must_exist=True)
    if not src_norm:
        raise PathError("A storage location itself cannot be moved")
    dst_dir_abs, dst_dir_norm = resolve(dst_root, dst_dir_rel, must_exist=True)
    if not os.path.isdir(dst_dir_abs):
        raise PathError("Destination is not a folder")
    name = validate_name(new_name) if new_name else os.path.basename(src_abs)
    target = os.path.join(dst_dir_abs, name)
    if os.path.isdir(src_abs):
        sc, tc = os.path.normcase(src_abs), os.path.normcase(target)
        if tc == sc or tc.startswith(sc + os.sep):
            raise PathError("Cannot move or copy a folder into itself")
    if os.path.exists(target):
        if os.path.normcase(target) == os.path.normcase(src_abs):
            if not copy:
                return entry(src_root, src_abs, src_norm)
            name = _unique_name(dst_dir_abs, name)
        elif on_conflict == "fail":
            raise Conflict(f"'{name}' already exists in the destination")
        elif on_conflict == "overwrite":
            dst_rel_existing = f"{dst_dir_norm}/{name}" if dst_dir_norm else name
            delete(dst_root, dst_rel_existing, actor="system:overwrite")
        else:
            name = _unique_name(dst_dir_abs, name)
        target = os.path.join(dst_dir_abs, name)
    dst_rel = f"{dst_dir_norm}/{name}" if dst_dir_norm else name
    if copy:
        if os.path.isdir(src_abs):
            shutil.copytree(src_abs, target)
        else:
            shutil.copy2(src_abs, target)
    else:
        shutil.move(src_abs, target)
        index.remove(src_root, src_norm)
    index.upsert(dst_root, target, dst_rel, recursive=True)
    return entry(dst_root, target, dst_rel)


# ------------------------------------------------------------- writing
def write_bytes(root: Root, rel: str, data: bytes, overwrite: bool, reason: str = "overwrite") -> dict:
    abs_path, norm = resolve(root, rel)
    if not norm:
        raise PathError("A file name is required")
    validate_name(os.path.basename(norm))
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    if os.path.exists(abs_path):
        if os.path.isdir(abs_path):
            raise Conflict("A folder with that name exists")
        if not overwrite:
            raise Conflict(f"'{os.path.basename(norm)}' already exists")
        snapshot_version(root, norm, abs_path, reason)
    tmp = abs_path + f".pcloud-{uuid.uuid4().hex[:8]}.part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, abs_path)
    index.upsert(root, abs_path, norm)
    return entry(root, abs_path, norm)


def place_file(root: Root, src_tmp: str, rel_dir: str, filename: str, overwrite: bool) -> dict:
    """Move a completed temp upload into place (handles versioning and name conflicts)."""
    filename = validate_name(filename)
    dir_abs, dir_norm = resolve(root, rel_dir)
    os.makedirs(dir_abs, exist_ok=True)
    target = os.path.join(dir_abs, filename)
    if os.path.exists(target):
        if os.path.isdir(target):
            raise Conflict("A folder with that name exists")
        if overwrite:
            snapshot_version(root, f"{dir_norm}/{filename}" if dir_norm else filename, target, "upload overwrite")
        else:
            filename = _unique_name(dir_abs, filename)
            target = os.path.join(dir_abs, filename)
    shutil.move(src_tmp, target)
    rel = f"{dir_norm}/{filename}" if dir_norm else filename
    index.upsert(root, target, rel)
    return entry(root, target, rel)


def checksum(abs_path: str, algo: str = "sha256") -> str:
    h = hashlib.new(algo)
    with open(abs_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def safe_tmp_path(suffix: str = "") -> Path:
    return get_config().tmp_dir / f"{uuid.uuid4().hex}{suffix}"
