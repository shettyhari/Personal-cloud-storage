"""Versioned, deduplicated, optionally encrypted snapshot backups.

Repository layout (at the plan's destination):
    PersonalCloudBackup-<plan id>/
        repo.json                 format, encryption flag, key check value
        blobs/ab/<blob id>        file contents (AES-256-GCM in 4 MiB segments when encrypted)
        snapshots/<stamp>.snap    manifest of every file in that snapshot (encrypted when the repo is)

Snapshots are immutable: a file deleted or encrypted by ransomware today can be restored from any
earlier snapshot that is still retained. Sync is NOT backup - this module is.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
from datetime import datetime
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .. import db
from ..config import get_config
from ..paths import get_root, resolve
from ..security import SecretStore
from . import index
from .jobs import JobContext, handler

SEG = 4 * 1024 * 1024
MAGIC = b"PCB1"


def secrets_store() -> SecretStore:
    return SecretStore(get_config().secrets_dir)


def key_name(plan_id: int) -> str:
    return f"backup_key_{plan_id}"


def encode_recovery_key(key: bytes) -> str:
    return base64.urlsafe_b64encode(key).decode().rstrip("=")


def decode_recovery_key(text: str) -> bytes:
    t = text.strip().replace(" ", "")
    key = base64.urlsafe_b64decode(t + "=" * (-len(t) % 4))
    if len(key) != 32:
        raise ValueError("Invalid recovery key")
    return key


class Repo:
    def __init__(self, path: Path, key: bytes | None):
        self.path = path
        self.key = key
        self.meta = json.loads((path / "repo.json").read_text()) if (path / "repo.json").exists() else None

    @classmethod
    def for_plan(cls, plan, key: bytes | None = None) -> "Repo":
        path = Path(plan["destination"]) / f"PersonalCloudBackup-{plan['id']}"
        if plan["encrypted"] and key is None:
            key = secrets_store().get(key_name(plan["id"]))
            if key is None:
                raise RuntimeError("Backup key not found on this PC. Provide the recovery key to restore.")
        return cls(path, key if plan["encrypted"] else None)

    @property
    def encrypted(self) -> bool:
        return self.key is not None

    def init(self) -> None:
        (self.path / "blobs").mkdir(parents=True, exist_ok=True)
        (self.path / "snapshots").mkdir(parents=True, exist_ok=True)
        if self.meta is None:
            self.meta = {"format": 1, "encrypted": self.encrypted, "created": datetime.now().isoformat(),
                         "key_check": self._key_check()}
            (self.path / "repo.json").write_text(json.dumps(self.meta, indent=2))
        self.check_key()

    def _key_check(self) -> str | None:
        return hmac.new(self.key, b"pcloud-key-check", hashlib.sha256).hexdigest()[:16] if self.key else None

    def check_key(self) -> None:
        if self.meta and self.meta.get("encrypted") and self.meta.get("key_check") != self._key_check():
            raise RuntimeError("The backup key does not match this backup repository")

    # ------------------------------------------------------------- blobs
    def blob_id(self, sha256: str) -> str:
        if self.key:
            return hmac.new(self.key, sha256.encode(), hashlib.sha256).hexdigest()
        return sha256

    def blob_path(self, blob_id: str) -> Path:
        return self.path / "blobs" / blob_id[:2] / blob_id

    def put_file(self, src: str, sha256: str, ctx: JobContext | None = None) -> tuple[str, bool]:
        bid = self.blob_id(sha256)
        dest = self.blob_path(bid)
        if dest.exists():
            return bid, False
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        with open(src, "rb") as f, open(tmp, "wb") as out:
            if self.key:
                aes = AESGCM(self.key)
                out.write(MAGIC)
                i = 0
                while True:
                    if ctx:
                        ctx.check()
                    chunk = f.read(SEG)
                    nonce = os.urandom(12)
                    final = len(chunk) < SEG
                    ad = f"{bid}:{i}:{int(final)}".encode()
                    ct = aes.encrypt(nonce, chunk, ad)
                    out.write(struct.pack(">?I", final, len(ct)) + nonce + ct)
                    i += 1
                    if final:
                        break
            else:
                for chunk in iter(lambda: f.read(SEG), b""):
                    if ctx:
                        ctx.check()
                    out.write(chunk)
        os.replace(tmp, dest)
        return bid, True

    def read_blob(self, blob_id: str):
        """Yield plaintext chunks of a blob, authenticating each segment."""
        with open(self.blob_path(blob_id), "rb") as f:
            if not self.key:
                yield from iter(lambda: f.read(SEG), b"")
                return
            if f.read(4) != MAGIC:
                raise ValueError("Corrupt blob header")
            aes = AESGCM(self.key)
            i = 0
            while True:
                hdr = f.read(5)
                if len(hdr) < 5:
                    raise ValueError("Truncated blob")
                final, n = struct.unpack(">?I", hdr)
                nonce = f.read(12)
                ct = f.read(n)
                yield aes.decrypt(nonce, ct, f"{blob_id}:{i}:{int(final)}".encode())
                i += 1
                if final:
                    return

    # --------------------------------------------------------- snapshots
    def write_snapshot(self, manifest: dict) -> str:
        name = manifest["id"] + ".snap"
        data = json.dumps(manifest, separators=(",", ":")).encode()
        if self.key:
            nonce = os.urandom(12)
            data = MAGIC + nonce + AESGCM(self.key).encrypt(nonce, data, b"snapshot")
        p = self.path / "snapshots" / name
        tmp = p.with_suffix(".part")
        tmp.write_bytes(data)
        os.replace(tmp, p)
        return manifest["id"]

    def read_snapshot(self, snap_id: str) -> dict:
        raw = (self.path / "snapshots" / f"{snap_id}.snap").read_bytes()
        if raw.startswith(MAGIC):
            if not self.key:
                raise RuntimeError("Snapshot is encrypted - key required")
            raw = AESGCM(self.key).decrypt(raw[4:16], raw[16:], b"snapshot")
        return json.loads(raw)

    def list_snapshots(self) -> list[str]:
        d = self.path / "snapshots"
        if not d.exists():
            return []
        return sorted((p.stem for p in d.glob("*.snap")), reverse=True)


def _sha256(path: str, ctx: JobContext | None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(SEG), b""):
            if ctx:
                ctx.check()
            h.update(chunk)
    return h.hexdigest()


def run_backup(plan_id: int, ctx: JobContext | None = None) -> dict:
    plan = db.one("SELECT * FROM backup_plans WHERE id=?", (plan_id,))
    if plan is None:
        raise ValueError("Backup plan not found")
    repo = Repo.for_plan(plan)
    repo.init()
    prev: dict[tuple, dict] = {}
    snaps = repo.list_snapshots()
    if snaps:
        for f in repo.read_snapshot(snaps[0])["files"]:
            prev[(f["root_id"], f["path"])] = f
    snap_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    files_out, roots_out = [], []
    stats = {"files": 0, "bytes": 0, "new_blobs": 0, "new_bytes": 0, "skipped": 0}
    root_ids = db.loads(plan["root_ids"], [])
    todo: list[tuple] = []
    for rid in root_ids:
        root = get_root(int(rid))
        base = root.real
        roots_out.append({"root_id": root.id, "name": root.name, "path": root.path})
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in index.SKIP_DIRS - {"node_modules", ".git"}]
            for n in filenames:
                p = os.path.join(dirpath, n)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                todo.append((root.id, p, os.path.relpath(p, base).replace("\\", "/"), st.st_size, st.st_mtime))
    total_bytes = sum(t[3] for t in todo) or 1
    done_bytes = 0
    for rid, p, rel, size, mtime in todo:
        if ctx:
            ctx.check()
        old = prev.get((rid, rel))
        try:
            if old and old["size"] == size and abs(old["mtime"] - mtime) < 1 and repo.blob_path(old["blob"]).exists():
                sha, bid, new = old["sha256"], old["blob"], False
            else:
                sha = _sha256(p, ctx)
                bid, new = repo.put_file(p, sha, ctx)
        except (OSError, PermissionError) as e:
            stats["skipped"] += 1
            if ctx:
                ctx.log(f"skipped {rel}: {e}")
            continue
        files_out.append({"root_id": rid, "path": rel, "size": size, "mtime": mtime, "sha256": sha, "blob": bid})
        stats["files"] += 1
        stats["bytes"] += size
        if new:
            stats["new_blobs"] += 1
            stats["new_bytes"] += size
        done_bytes += size
        if ctx:
            ctx.progress(done_bytes / total_bytes, f"Backed up {stats['files']}/{len(todo)} files")
    repo.write_snapshot({"id": snap_id, "created": datetime.now().isoformat(), "plan": plan["name"],
                         "roots": roots_out, "files": files_out, "stats": stats})
    removed = prune(repo, int(plan["keep_snapshots"]))
    stats["pruned_snapshots"] = removed
    stats["snapshot"] = snap_id
    db.execute("UPDATE backup_plans SET last_run=?, last_status='ok', last_error=NULL WHERE id=?", (db.now(), plan_id))
    return stats


def prune(repo: Repo, keep: int) -> int:
    snaps = repo.list_snapshots()
    old = snaps[keep:]
    for s in old:
        (repo.path / "snapshots" / f"{s}.snap").unlink(missing_ok=True)
    if old:
        live = {f["blob"] for s in snaps[:keep] for f in repo.read_snapshot(s)["files"]}
        for blob in (repo.path / "blobs").glob("*/*"):
            if blob.name not in live and not blob.name.endswith(".part"):
                blob.unlink(missing_ok=True)
    return len(old)


def verify(plan_id: int, snap_id: str | None, ctx: JobContext | None = None, key: bytes | None = None) -> dict:
    plan = db.one("SELECT * FROM backup_plans WHERE id=?", (plan_id,))
    repo = Repo.for_plan(plan, key)
    repo.check_key()
    snaps = repo.list_snapshots()
    if not snaps:
        raise RuntimeError("No snapshots to verify yet")
    snap = repo.read_snapshot(snap_id or snaps[0])
    bad = []
    for i, f in enumerate(snap["files"]):
        if ctx:
            ctx.check()
            ctx.progress((i + 1) / max(1, len(snap["files"])), f"Verified {i + 1}/{len(snap['files'])}")
        h = hashlib.sha256()
        try:
            for chunk in repo.read_blob(f["blob"]):
                h.update(chunk)
            if h.hexdigest() != f["sha256"]:
                bad.append({"path": f["path"], "error": "checksum mismatch"})
        except Exception as e:
            bad.append({"path": f["path"], "error": str(e) or type(e).__name__})
    return {"snapshot": snap["id"], "files": len(snap["files"]), "errors": bad[:200], "ok": not bad}


def restore(plan_id: int, snap_id: str, target_root_id: int, target_rel: str, prefix: str = "",
            source_root_id: int | None = None, ctx: JobContext | None = None, key: bytes | None = None) -> dict:
    plan = db.one("SELECT * FROM backup_plans WHERE id=?", (plan_id,))
    repo = Repo.for_plan(plan, key)
    repo.check_key()
    snap = repo.read_snapshot(snap_id)
    root = get_root(target_root_id)
    folder = f"Restored {snap_id}"
    base_rel = f"{target_rel.strip('/')}/{folder}" if target_rel.strip("/") else folder
    base_abs, base_norm = resolve(root, base_rel)
    names = {r["root_id"]: r["name"] for r in snap["roots"]}
    files = [f for f in snap["files"] if (source_root_id is None or f["root_id"] == source_root_id)
             and (not prefix or f["path"] == prefix.strip("/") or f["path"].startswith(prefix.strip("/") + "/"))]
    restored, errors = 0, []
    for i, f in enumerate(files):
        if ctx:
            ctx.check()
            ctx.progress((i + 1) / max(1, len(files)), f"Restoring {f['path']}")
        out_abs, _ = resolve(root, f"{base_norm}/{names.get(f['root_id'], 'root')}/{f['path']}")
        os.makedirs(os.path.dirname(out_abs), exist_ok=True)
        h = hashlib.sha256()
        tmp = out_abs + ".part"
        try:
            with open(tmp, "wb") as out:
                for chunk in repo.read_blob(f["blob"]):
                    h.update(chunk)
                    out.write(chunk)
            if h.hexdigest() != f["sha256"]:
                raise ValueError("checksum mismatch")
            os.replace(tmp, out_abs)
            os.utime(out_abs, (f["mtime"], f["mtime"]))
            restored += 1
        except Exception as e:
            errors.append({"path": f["path"], "error": str(e)})
            if os.path.exists(tmp):
                os.remove(tmp)
    index.upsert(root, base_abs, base_norm, recursive=True)
    return {"restored": restored, "errors": errors[:200], "output": base_norm}


@handler("backup", "Backup", resumable=False)
def job_backup(ctx: JobContext, p: dict):
    try:
        return run_backup(int(p["plan_id"]), ctx)
    except Exception as e:
        db.execute("UPDATE backup_plans SET last_run=?, last_status='error', last_error=? WHERE id=?",
                   (db.now(), str(e)[:500], int(p["plan_id"])))
        raise


@handler("backup_verify", "Verify backup")
def job_verify(ctx: JobContext, p: dict):
    res = verify(int(p["plan_id"]), p.get("snapshot"), ctx)
    if not res["ok"]:
        raise RuntimeError(f"{len(res['errors'])} file(s) failed verification: {res['errors'][:3]}")
    return res


# Recovery keys typed by the user are kept in memory only (never written to the jobs table).
TRANSIENT_KEYS: dict[str, bytes] = {}


@handler("backup_restore", "Restore from backup")
def job_restore(ctx: JobContext, p: dict):
    key = TRANSIENT_KEYS.pop(p["key_ref"], None) if p.get("key_ref") else None
    if p.get("key_ref") and key is None:
        raise RuntimeError("Recovery key is no longer in memory (server restarted). Start the restore again.")
    return restore(int(p["plan_id"]), p["snapshot"], int(p["target_root_id"]), p.get("target_dir", ""),
                   p.get("prefix", ""), p.get("source_root_id"), ctx, key)
