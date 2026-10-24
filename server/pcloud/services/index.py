"""Metadata + full-text search index (SQLite FTS5) over approved roots."""
from __future__ import annotations

import logging
import os
import threading
import time

from .. import db, settings_store
from ..paths import Root, list_roots
from . import extract

log = logging.getLogger("pcloud.index")
_scan_lock = threading.Lock()
SKIP_DIRS = {"$RECYCLE.BIN", "System Volume Information", ".pcloud-tmp", "node_modules", ".git", "__pycache__"}


def _row_for(root: Root, abs_path: str, rel: str) -> tuple | None:
    try:
        st = os.stat(abs_path)
    except OSError:
        return None
    is_dir = os.path.isdir(abs_path)
    name = os.path.basename(abs_path)
    ext = "" if is_dir else os.path.splitext(name)[1].lower().lstrip(".")
    return (root.id, rel, name, ext, int(is_dir), 0 if is_dir else st.st_size, st.st_mtime)


def _index_content(root: Root, abs_path: str, rel: str, size: int) -> None:
    db.execute("DELETE FROM content_fts WHERE root_id=? AND rel_path=?", (root.id, rel))
    name = os.path.basename(rel)
    body = ""
    max_mb = int(settings_store.get("index_max_content_mb"))
    if root.index_content and extract.can_extract(name) and size <= max_mb * 1024 * 1024:
        try:
            body = extract.extract_text(abs_path, max_chars=500_000)
        except Exception as e:  # corrupted / encrypted docs
            log.debug("extract failed for %s: %s", abs_path, e)
    db.execute("INSERT INTO content_fts(root_id, rel_path, name, body) VALUES(?,?,?,?)",
               (root.id, rel, name.replace("_", " ").replace("-", " ").replace(".", " "), body))
    db.execute("UPDATE file_index SET content_indexed=1 WHERE root_id=? AND rel_path=?", (root.id, rel))


def upsert(root: Root, abs_path: str, rel: str, recursive: bool = False) -> None:
    if not rel:
        return
    try:
        row = _row_for(root, abs_path, rel)
        if row is None:
            return
        db.execute("INSERT INTO file_index(root_id, rel_path, name, ext, is_dir, size, mtime, content_indexed) "
                   "VALUES(?,?,?,?,?,?,?,0) ON CONFLICT(root_id, rel_path) DO UPDATE SET name=excluded.name, "
                   "ext=excluded.ext, is_dir=excluded.is_dir, size=excluded.size, mtime=excluded.mtime, "
                   "content_indexed=0", row)
        if not row[4]:
            _index_content(root, abs_path, rel, row[5])
        elif recursive:
            for dirpath, dirnames, files in os.walk(abs_path):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                for n in dirnames + files:
                    p = os.path.join(dirpath, n)
                    child_rel = rel + "/" + os.path.relpath(p, abs_path).replace("\\", "/")
                    upsert(root, p, child_rel)
    except Exception:
        log.exception("index upsert failed for %s", abs_path)


def remove(root: Root, rel: str) -> None:
    if not rel:
        return
    like = rel.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
    db.execute("DELETE FROM file_index WHERE root_id=? AND (rel_path=? OR rel_path LIKE ? ESCAPE '\\')",
               (root.id, rel, like))
    db.execute("DELETE FROM content_fts WHERE root_id=? AND (rel_path=? OR rel_path LIKE ? ESCAPE '\\')",
               (root.id, rel, like))


def scan_root(root: Root, progress=None, cancelled=lambda: False) -> dict:
    """Full reconciliation scan of a root. Only re-extracts content for changed files."""
    base = root.real
    if not os.path.isdir(base):
        return {"root": root.name, "error": "unavailable"}
    stamp = int(time.time())
    existing = {r["rel_path"]: (r["size"], r["mtime"], r["content_indexed"])
                for r in db.all_("SELECT rel_path, size, mtime, content_indexed FROM file_index WHERE root_id=?",
                                 (root.id,))}
    files = dirs = changed = 0
    batch: list[tuple] = []

    def flush():
        if batch:
            with db.tx() as c:
                c.executemany("INSERT INTO file_index(root_id, rel_path, name, ext, is_dir, size, mtime, content_indexed, seen) "
                              "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(root_id, rel_path) DO UPDATE SET "
                              "name=excluded.name, ext=excluded.ext, is_dir=excluded.is_dir, size=excluded.size, "
                              "mtime=excluded.mtime, content_indexed=excluded.content_indexed, seen=excluded.seen", batch)
            batch.clear()

    to_extract: list[tuple[str, str, int]] = []
    for dirpath, dirnames, filenames in os.walk(base):
        if cancelled():
            break
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith("$")]
        for n in dirnames + filenames:
            p = os.path.join(dirpath, n)
            rel = os.path.relpath(p, base).replace("\\", "/")
            row = _row_for(root, p, rel)
            if row is None:
                continue
            prev = existing.get(rel)
            unchanged = prev is not None and prev[0] == row[5] and abs(prev[1] - row[6]) < 1
            content_flag = prev[2] if (unchanged and prev) else 0
            batch.append(row + (content_flag, stamp))
            if row[4]:
                dirs += 1
            else:
                files += 1
                if not content_flag:
                    to_extract.append((p, rel, row[5]))
                    changed += 1
            if len(batch) >= 1000:
                flush()
                if progress:
                    progress(None, f"{root.name}: {files} files")
    flush()
    if not cancelled():
        # anything not seen in this scan no longer exists
        gone = db.all_("SELECT rel_path FROM file_index WHERE root_id=? AND seen<>?", (root.id, stamp))
        with db.tx() as c:
            for g in gone:
                c.execute("DELETE FROM content_fts WHERE root_id=? AND rel_path=?", (root.id, g["rel_path"]))
            c.execute("DELETE FROM file_index WHERE root_id=? AND seen<>?", (root.id, stamp))
    for i, (p, rel, size) in enumerate(to_extract):
        if cancelled():
            break
        _index_content(root, p, rel, size)
        if progress and i % 50 == 0:
            progress(i / max(1, len(to_extract)), f"{root.name}: indexing content {i}/{len(to_extract)}")
    return {"root": root.name, "files": files, "folders": dirs, "content_updated": changed}


def scan_all(progress=None, cancelled=lambda: False) -> list[dict]:
    if not _scan_lock.acquire(blocking=False):
        return [{"skipped": "scan already running"}]
    try:
        return [scan_root(r, progress, cancelled) for r in list_roots()]
    finally:
        _scan_lock.release()


def _fts_query(text: str) -> str:
    terms = [t for t in "".join(ch if ch.isalnum() else " " for ch in text).split() if t]
    return " ".join(f'"{t}"*' for t in terms[:12])


def search(root_ids: list[int], query: str = "", ext: list[str] | None = None, folder: str | None = None,
           modified_after: float | None = None, modified_before: float | None = None,
           min_size: int | None = None, content: bool = True, limit: int = 100) -> list[dict]:
    if not root_ids:
        return []
    clauses = [f"f.root_id IN ({','.join('?' * len(root_ids))})"]
    params: list = list(root_ids)
    if ext:
        exts = [e.lower().lstrip(".") for e in ext]
        clauses.append(f"f.ext IN ({','.join('?' * len(exts))})")
        params += exts
    if folder:
        clauses.append("(f.rel_path LIKE ? ESCAPE '\\')")
        params.append(folder.strip("/").replace("%", "\\%").replace("_", "\\_") + "/%")
    if modified_after:
        clauses.append("f.mtime >= ?")
        params.append(modified_after)
    if modified_before:
        clauses.append("f.mtime <= ?")
        params.append(modified_before)
    if min_size:
        clauses.append("f.size >= ?")
        params.append(min_size)
    where = " AND ".join(clauses)
    results: dict[tuple, dict] = {}
    q = query.strip()
    if q:
        like = "%" + q.replace("%", "\\%").replace("_", "\\_") + "%"
        for r in db.all_(f"SELECT f.* FROM file_index f WHERE {where} AND f.name LIKE ? ESCAPE '\\' "
                         f"ORDER BY f.is_dir DESC, f.mtime DESC LIMIT ?", (*params, like, limit)):
            results[(r["root_id"], r["rel_path"])] = dict(r) | {"match": "name", "snippet": None}
        fq = _fts_query(q)
        if content and fq and len(results) < limit:
            try:
                rows = db.all_(
                    f"SELECT f.*, snippet(content_fts, 3, '[', ']', ' ... ', 12) AS snip, bm25(content_fts) AS rank "
                    f"FROM content_fts JOIN file_index f ON f.root_id=content_fts.root_id AND f.rel_path=content_fts.rel_path "
                    f"WHERE content_fts MATCH ? AND {where} ORDER BY rank LIMIT ?", (fq, *params, limit))
            except Exception:
                rows = []
            for r in rows:
                key = (r["root_id"], r["rel_path"])
                if key not in results:
                    d = dict(r)
                    snip = d.pop("snip", None)
                    d.pop("rank", None)
                    results[key] = d | {"match": "content", "snippet": snip}
    else:
        for r in db.all_(f"SELECT f.* FROM file_index f WHERE {where} AND f.is_dir=0 ORDER BY f.mtime DESC LIMIT ?",
                         (*params, limit)):
            results[(r["root_id"], r["rel_path"])] = dict(r) | {"match": "filter", "snippet": None}
    out = []
    for d in list(results.values())[:limit]:
        d.pop("seen", None)
        d.pop("content_indexed", None)
        d["path"] = d.pop("rel_path")
        d["is_dir"] = bool(d["is_dir"])
        d["modified"] = d.pop("mtime")
        out.append(d)
    return out


def stats(root_ids: list[int]) -> dict:
    if not root_ids:
        return {"by_type": [], "largest": [], "recent": [], "files": 0, "folders": 0}
    q = ",".join("?" * len(root_ids))
    p = tuple(root_ids)
    by_type = [dict(r) for r in db.all_(
        f"SELECT CASE WHEN ext='' THEN '(none)' ELSE ext END AS ext, COUNT(*) AS count, SUM(size) AS size "
        f"FROM file_index WHERE is_dir=0 AND root_id IN ({q}) GROUP BY 1 ORDER BY size DESC LIMIT 15", p)]
    largest = [dict(r) for r in db.all_(
        f"SELECT root_id, rel_path AS path, name, size, mtime AS modified FROM file_index "
        f"WHERE is_dir=0 AND root_id IN ({q}) ORDER BY size DESC LIMIT 15", p)]
    recent = [dict(r) for r in db.all_(
        f"SELECT root_id, rel_path AS path, name, size, mtime AS modified, ext FROM file_index "
        f"WHERE is_dir=0 AND root_id IN ({q}) ORDER BY mtime DESC LIMIT 15", p)]
    counts = db.one(f"SELECT SUM(is_dir=0) AS files, SUM(is_dir=1) AS folders FROM file_index WHERE root_id IN ({q})", p)
    by_root = [dict(r) for r in db.all_(
        f"SELECT root_id, SUM(size) AS size, SUM(is_dir=0) AS files FROM file_index WHERE root_id IN ({q}) GROUP BY root_id", p)]
    top_folders = [dict(r) for r in db.all_(
        f"SELECT root_id, CASE WHEN instr(rel_path,'/')>0 THEN substr(rel_path,1,instr(rel_path,'/')-1) "
        f"ELSE '(files in root)' END AS folder, SUM(size) AS size, COUNT(*) AS files FROM file_index "
        f"WHERE is_dir=0 AND root_id IN ({q}) GROUP BY 1,2 ORDER BY size DESC LIMIT 15", p)]
    return {"by_type": by_type, "largest": largest, "recent": recent, "by_root": by_root,
            "top_folders": top_folders, "files": counts["files"] or 0, "folders": counts["folders"] or 0}
