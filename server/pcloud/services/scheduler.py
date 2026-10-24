"""Periodic maintenance and scheduled sync/backup runs."""
from __future__ import annotations

import logging
import os
import shutil
import threading
import time

from .. import auth, db, settings_store
from ..config import get_config
from . import approvals, fileops
from .jobs import get_manager

log = logging.getLogger("pcloud.scheduler")


class Scheduler:
    def __init__(self, tick: float = 60):
        self.tick = tick
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_index = 0.0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _active(self, job_type: str, key: str, value: int) -> bool:
        rows = db.all_("SELECT params FROM jobs WHERE type=? AND status IN ('queued','running')", (job_type,))
        return any(db.loads(r["params"], {}).get(key) == value for r in rows)

    def run_once(self) -> None:
        auth.cleanup_sessions()
        from ..cloudlink.link import cloud_link
        cloud_link.start()  # picks up a link made with `pcloud link` while the server was running
        approvals.expire_old()
        fileops.purge_expired_trash()
        self._cleanup_uploads()
        mgr = get_manager()
        t = db.now()
        interval = int(settings_store.get("index_interval_minutes")) * 60
        if interval > 0 and t - self._last_index > interval:
            self._last_index = t
            if not db.one("SELECT 1 FROM jobs WHERE type='index_scan' AND status IN ('queued','running')"):
                mgr.submit("index_scan", {}, created_by="scheduler", source="scheduler")
        for p in db.all_("SELECT * FROM sync_pairs WHERE enabled=1"):
            if (p["last_run"] or 0) + p["interval_minutes"] * 60 <= t and not self._active("sync", "pair_id", p["id"]):
                mgr.submit("sync", {"pair_id": p["id"]}, created_by="scheduler", source="scheduler")
        for b in db.all_("SELECT * FROM backup_plans WHERE enabled=1"):
            if (b["last_run"] or 0) + b["interval_hours"] * 3600 <= t and not self._active("backup", "plan_id", b["id"]):
                mgr.submit("backup", {"plan_id": b["id"]}, created_by="scheduler", source="scheduler")
        # keep the activity log bounded (~1 year)
        db.execute("DELETE FROM activity WHERE ts < ?", (t - 365 * 86400,))

    def _cleanup_uploads(self) -> None:
        cfg = get_config()
        cutoff = db.now() - 7 * 86400
        for r in db.all_("SELECT id FROM uploads WHERE status='active' AND updated_at < ?", (cutoff,)):
            p = cfg.uploads_dir / f"{r['id']}.part"
            if p.exists():
                p.unlink()
            db.execute("UPDATE uploads SET status='aborted' WHERE id=?", (r["id"],))
        for f in cfg.tmp_dir.glob("*"):
            try:
                if time.time() - f.stat().st_mtime > 86400:
                    shutil.rmtree(f) if f.is_dir() else os.remove(f)
            except OSError:
                pass

    def _loop(self) -> None:
        self._last_index = db.now() - int(settings_store.get("index_interval_minutes")) * 60 + 20
        while not self._stop.wait(5 if self._last_index == 0 else self.tick):
            try:
                self.run_once()
            except Exception:
                log.exception("scheduler tick failed")
            finally:
                pass


scheduler = Scheduler()
