"""Background job queue. Jobs are persisted in SQLite so their state survives restarts."""
from __future__ import annotations

import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from .. import audit, db
from ..config import get_config

log = logging.getLogger("pcloud.jobs")

Handler = Callable[["JobContext", dict], Any]
HANDLERS: dict[str, Handler] = {}
RESUMABLE: set[str] = set()
TITLES: dict[str, str] = {}


def handler(name: str, title: str, resumable: bool = False):
    def deco(fn: Handler) -> Handler:
        HANDLERS[name] = fn
        TITLES[name] = title
        if resumable:
            RESUMABLE.add(name)
        return fn
    return deco


class Cancelled(Exception):
    pass


class JobContext:
    def __init__(self, job_id: int):
        self.job_id = job_id
        self._cancel = threading.Event()
        self._last_write = 0.0
        self.log_path = get_config().jobs_dir / f"{job_id}.log"

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()

    def progress(self, fraction: float | None = None, message: str | None = None, force: bool = False) -> None:
        t = time.monotonic()
        if not force and t - self._last_write < 0.5:
            return
        self._last_write = t
        sets, params = [], []
        if fraction is not None:
            sets.append("progress=?")
            params.append(max(0.0, min(1.0, float(fraction))))
        if message is not None:
            sets.append("message=?")
            params.append(message[:500])
        if sets:
            db.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", (*params, self.job_id))

    def log(self, line: str) -> None:
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + line.rstrip() + "\n")


class JobManager:
    def __init__(self, workers: int = 2):
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")
        self.contexts: dict[int, JobContext] = {}
        self.lock = threading.Lock()

    def recover(self) -> None:
        """Called at startup: jobs that were running when the agent stopped are marked and resumed if possible."""
        for r in db.all_("SELECT id, type FROM jobs WHERE status IN ('running', 'queued')"):
            if r["type"] in RESUMABLE:
                db.execute("UPDATE jobs SET status='queued', message='Resuming after restart' WHERE id=?", (r["id"],))
                self._dispatch(r["id"])
            else:
                db.execute("UPDATE jobs SET status='interrupted', finished_at=?, message='Server stopped while running' "
                           "WHERE id=?", (db.now(), r["id"]))

    def submit(self, job_type: str, params: dict, created_by: str, source: str = "user") -> int:
        if job_type not in HANDLERS:
            raise ValueError(f"Unknown job type '{job_type}'")
        cur = db.execute("INSERT INTO jobs(type, params, status, source, created_by, created_at, message) "
                         "VALUES(?,?,?,?,?,?,?)", (job_type, db.dumps(params), "queued", source, created_by,
                                                   db.now(), "Queued"))
        job_id = cur.lastrowid
        self._dispatch(job_id)
        return job_id

    def _dispatch(self, job_id: int) -> None:
        ctx = JobContext(job_id)
        with self.lock:
            self.contexts[job_id] = ctx
        self.pool.submit(self._run, job_id, ctx)

    def _run(self, job_id: int, ctx: JobContext) -> None:
        row = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if row is None or row["status"] == "cancelled":
            return
        db.execute("UPDATE jobs SET status='running', started_at=?, message='Running' WHERE id=?", (db.now(), job_id))
        ctx.log(f"started {row['type']}")
        try:
            result = HANDLERS[row["type"]](ctx, db.loads(row["params"], {}))
            db.execute("UPDATE jobs SET status='succeeded', progress=1, finished_at=?, result=?, message=? WHERE id=?",
                       (db.now(), db.dumps(result), "Completed", job_id))
            ctx.log("completed")
            audit.record("system", "jobs", f"job.{row['type']}", f"job {job_id}", "ok")
        except Cancelled:
            db.execute("UPDATE jobs SET status='cancelled', finished_at=?, message='Cancelled' WHERE id=?",
                       (db.now(), job_id))
            ctx.log("cancelled")
        except Exception as e:
            log.exception("job %s failed", job_id)
            ctx.log("FAILED: " + "".join(traceback.format_exception_only(type(e), e)))
            db.execute("UPDATE jobs SET status='failed', finished_at=?, message=? WHERE id=?",
                       (db.now(), str(e)[:500] or type(e).__name__, job_id))
            audit.record("system", "jobs", f"job.{row['type']}", f"job {job_id}", "error", details={"error": str(e)})
        finally:
            with self.lock:
                self.contexts.pop(job_id, None)
            db.close_thread_connection()

    def cancel(self, job_id: int) -> bool:
        with self.lock:
            ctx = self.contexts.get(job_id)
        row = db.one("SELECT status FROM jobs WHERE id=?", (job_id,))
        if row is None:
            return False
        if row["status"] == "queued":
            db.execute("UPDATE jobs SET status='cancelled', finished_at=?, message='Cancelled' WHERE id=?",
                       (db.now(), job_id))
        if ctx:
            ctx._cancel.set()
        return True

    def shutdown(self) -> None:
        with self.lock:
            for ctx in self.contexts.values():
                ctx._cancel.set()
        self.pool.shutdown(wait=False, cancel_futures=True)


manager: JobManager | None = None


def get_manager() -> JobManager:
    global manager
    if manager is None:
        manager = JobManager()
    return manager


def job_dict(r) -> dict:
    d = dict(r)
    d["params"] = db.loads(d["params"], {})
    d["result"] = db.loads(d["result"], None)
    d["title"] = TITLES.get(d["type"], d["type"])
    return d


def read_log(job_id: int, tail: int = 200_000) -> str:
    p = get_config().jobs_dir / f"{job_id}.log"
    if not p.exists():
        return ""
    data = p.read_bytes()[-tail:]
    return data.decode("utf-8", errors="replace")
