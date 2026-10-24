"""Folder synchronization between an approved root folder and another folder (second drive, NAS share...).

Modes
- mirror:  source -> destination. Files deleted/changed in the destination are archived, never silently lost.
- two_way: changes on either side propagate. If both sides changed since the last sync the destination copy
           is kept as "name (conflict <host> <date>).ext" and a conflict is recorded.

State per file is persisted after every copy, so an interrupted sync resumes where it stopped.
"""
from __future__ import annotations

import fnmatch
import os
import shutil
import uuid
from datetime import datetime

from .. import db
from ..config import get_config
from ..paths import get_root, resolve
from .jobs import JobContext, handler

TOL = 2.0  # seconds of mtime tolerance (FAT/exFAT have 2 s resolution)


def _scan(base: str, exclude: list[str]) -> dict[str, tuple[int, float]]:
    out = {}
    for dirpath, dirnames, files in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in {".pcloud-tmp", "$RECYCLE.BIN", "System Volume Information"}
                       and not any(fnmatch.fnmatch(d, p) for p in exclude)]
        for f in files:
            if f.endswith(".pcloud-sync.part") or any(fnmatch.fnmatch(f, p) for p in exclude):
                continue
            p = os.path.join(dirpath, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out[os.path.relpath(p, base).replace("\\", "/")] = (st.st_size, st.st_mtime)
    return out


def _copy(src: str, dst: str) -> tuple[int, float]:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".pcloud-sync.part"
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)
    st = os.stat(dst)
    return st.st_size, st.st_mtime


def _archive(pair_id: int, base: str, rel: str) -> None:
    """Move a file that sync would overwrite/delete into the sync archive (recoverable)."""
    src = os.path.join(base, rel)
    if not os.path.exists(src):
        return
    dest = get_config().data_dir / "sync-archive" / str(pair_id) / datetime.now().strftime("%Y%m%d-%H%M%S") / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(src, dest)


def _changed(cur: tuple[int, float] | None, size, mtime) -> bool:
    if cur is None or size is None:
        return True
    return cur[0] != size or abs(cur[1] - mtime) > TOL


def run_pair(pair_id: int, ctx: JobContext | None = None) -> dict:
    pair = db.one("SELECT * FROM sync_pairs WHERE id=?", (pair_id,))
    if pair is None:
        raise ValueError("Sync pair not found")
    root = get_root(pair["root_id"])
    src_base, _ = resolve(root, pair["rel_path"], must_exist=True)
    dst_base = os.path.realpath(pair["dest_path"])
    if not os.path.isdir(dst_base):
        os.makedirs(dst_base, exist_ok=True)
    exclude = db.loads(pair["exclude"], [])
    two_way = pair["mode"] == "two_way"
    db.execute("UPDATE sync_pairs SET last_status='running', last_error=NULL WHERE id=?", (pair_id,))

    src = _scan(src_base, exclude)
    dst = _scan(dst_base, exclude)
    state = {r["rel_path"]: r for r in db.all_("SELECT * FROM sync_state WHERE pair_id=?", (pair_id,))}
    stats = {"copied_to_dest": 0, "copied_to_source": 0, "deleted_dest": 0, "deleted_source": 0, "conflicts": 0,
             "unchanged": 0}
    all_paths = sorted(set(src) | set(dst) | set(state))

    def save(rel, s, d):
        if s is None and d is None:
            db.execute("DELETE FROM sync_state WHERE pair_id=? AND rel_path=?", (pair_id, rel))
        else:
            db.execute("INSERT INTO sync_state(pair_id, rel_path, src_size, src_mtime, dst_size, dst_mtime) "
                       "VALUES(?,?,?,?,?,?) ON CONFLICT(pair_id, rel_path) DO UPDATE SET src_size=excluded.src_size, "
                       "src_mtime=excluded.src_mtime, dst_size=excluded.dst_size, dst_mtime=excluded.dst_mtime",
                       (pair_id, rel, *(s or (None, None)), *(d or (None, None))))

    for i, rel in enumerate(all_paths):
        if ctx:
            ctx.check()
            if i % 100 == 0:
                ctx.progress(i / max(1, len(all_paths)), f"{pair['name']}: {i}/{len(all_paths)}")
        s, d, st = src.get(rel), dst.get(rel), state.get(rel)
        s_changed = _changed(s, st["src_size"], st["src_mtime"]) if st else s is not None
        d_changed = _changed(d, st["dst_size"], st["dst_mtime"]) if st else d is not None
        sp, dp = os.path.join(src_base, rel), os.path.join(dst_base, rel)

        if not two_way:
            if s is None:
                if d is not None:
                    _archive(pair_id, dst_base, rel)
                    stats["deleted_dest"] += 1
                save(rel, None, None)
            elif d is None or s[0] != d[0] or abs(s[1] - d[1]) > TOL:
                if d is not None:
                    _archive(pair_id, dst_base, rel)
                save(rel, s, _copy(sp, dp))
                stats["copied_to_dest"] += 1
            else:
                save(rel, s, d)
                stats["unchanged"] += 1
            continue

        # ---- two-way
        if s is not None and d is not None:
            if s[0] == d[0] and abs(s[1] - d[1]) <= TOL:
                save(rel, s, d)
                stats["unchanged"] += 1
            elif s_changed and d_changed:
                base, ext = os.path.splitext(rel)
                conflict_rel = f"{base} (conflict {datetime.now():%Y-%m-%d %H%M%S}){ext}"
                os.replace(dp, os.path.join(dst_base, conflict_rel))
                new_d = _copy(sp, dp)
                _copy(os.path.join(dst_base, conflict_rel), os.path.join(src_base, conflict_rel))
                db.execute("INSERT INTO sync_conflicts(pair_id, rel_path, detail, created_at) VALUES(?,?,?,?)",
                           (pair_id, rel, f"Both sides changed. Destination version kept as '{conflict_rel}'.",
                            db.now()))
                save(rel, s, new_d)
                stats["conflicts"] += 1
            elif d_changed and not s_changed:
                _copy(dp, sp)
                save(rel, (os.path.getsize(sp), os.path.getmtime(sp)), d)
                stats["copied_to_source"] += 1
            else:
                save(rel, s, _copy(sp, dp))
                stats["copied_to_dest"] += 1
        elif s is not None and d is None:
            if st is not None and not s_changed:      # deleted on destination
                _archive(pair_id, src_base, rel)
                save(rel, None, None)
                stats["deleted_source"] += 1
            else:
                save(rel, s, _copy(sp, dp))
                stats["copied_to_dest"] += 1
        elif d is not None and s is None:
            if st is not None and not d_changed:      # deleted on source
                _archive(pair_id, dst_base, rel)
                save(rel, None, None)
                stats["deleted_dest"] += 1
            else:
                _copy(dp, sp)
                save(rel, (os.path.getsize(sp), os.path.getmtime(sp)), d)
                stats["copied_to_source"] += 1
        else:
            save(rel, None, None)

    db.execute("UPDATE sync_pairs SET last_run=?, last_status=?, last_error=NULL WHERE id=?",
               (db.now(), "ok" if not stats["conflicts"] else "conflicts", pair_id))
    return stats


@handler("sync", "Folder sync", resumable=True)
def job_sync(ctx: JobContext, p: dict):
    try:
        return run_pair(int(p["pair_id"]), ctx)
    except Exception as e:
        db.execute("UPDATE sync_pairs SET last_run=?, last_status='error', last_error=? WHERE id=?",
                   (db.now(), str(e)[:500], int(p["pair_id"])))
        raise


def validate_dest(dest: str, root_real: str) -> str:
    if not dest or not os.path.isabs(dest):
        raise ValueError("Destination must be a full path, e.g. E:\\Backup\\Documents")
    real = os.path.realpath(dest)
    a, b = os.path.normcase(real), os.path.normcase(root_real)
    if a == b or a.startswith(b + os.sep) or b.startswith(a + os.sep):
        raise ValueError("Destination cannot be inside (or contain) the source folder")
    if os.path.dirname(real) == real:
        raise ValueError("Choose a folder, not a whole drive")
    data = os.path.normcase(str(get_config().data_dir))
    if a.startswith(data) or data.startswith(a + os.sep):
        raise ValueError("Destination cannot be inside the server's data folder")
    return real


def new_id() -> str:
    return uuid.uuid4().hex
