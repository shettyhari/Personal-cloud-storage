"""Health checks used by `pcloud doctor` and the dashboard's diagnostics panel."""
from __future__ import annotations

import os
import shutil
import socket
import sqlite3
import sys

from . import __version__, db, settings_store
from .config import get_config
from .paths import list_roots
from .services import remote


def _check(name: str, ok: bool | None, detail: str, fix: str = "") -> dict:
    return {"check": name, "status": "ok" if ok else ("warn" if ok is None else "fail"), "detail": detail, "fix": fix}


def run_checks() -> dict:
    cfg = get_config()
    out = [_check("Version", True, f"Personal Cloud OS {__version__}, Python {sys.version.split()[0]}")]
    # data dir
    try:
        probe = cfg.data_dir / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        out.append(_check("Data folder", True, str(cfg.data_dir)))
    except OSError as e:
        out.append(_check("Data folder", False, f"{cfg.data_dir}: {e}", "Check folder permissions"))
    # database
    try:
        r = db.one("PRAGMA integrity_check")
        out.append(_check("Database", r[0] == "ok", f"integrity: {r[0]}", "Restore pcloud.db from backup"))
        out.append(_check("Full-text search", True, f"SQLite {sqlite3.sqlite_version} with FTS5"))
    except Exception as e:
        out.append(_check("Database", False, str(e)))
    # users
    has_admin = db.one("SELECT 1 FROM users WHERE role='admin' AND disabled=0") is not None
    out.append(_check("Administrator", has_admin, "admin account exists" if has_admin else "no admin yet",
                      "Open the dashboard and complete setup"))
    # roots
    roots = list_roots()
    if not roots:
        out.append(_check("Storage locations", None, "none configured", "Add folders under Drives & Storage"))
    for r in roots:
        ok = os.path.isdir(r.path)
        writable = os.access(r.path, os.W_OK) if ok else False
        detail = f"{r.path} ({'writable' if writable else 'read-only' if ok else 'MISSING'})"
        if ok:
            u = shutil.disk_usage(r.path)
            detail += f", {u.free / 1024**3:.1f} GB free"
        out.append(_check(f"Location '{r.name}'", ok if writable or r.read_only else None if ok else False, detail,
                          "Reconnect the drive or update the path" if not ok else ""))
    # web UI build
    web_ok = (cfg.web_dir / "index.html").exists()
    out.append(_check("Dashboard build", web_ok, str(cfg.web_dir), "Run scripts\\build-web.ps1"))
    # network
    ts = remote.tailscale_status()
    if not ts.get("installed"):
        out.append(_check("Tailscale", None, "not installed - remote access unavailable",
                          "Install from https://tailscale.com/download/windows and sign in"))
    else:
        out.append(_check("Tailscale", ts.get("running"), f"state={ts.get('state')} name={ts.get('dns_name')}",
                          "Open Tailscale and sign in"))
    pub = settings_store.get("public_url")
    out.append(_check("HTTPS remote URL", bool(pub) or None, pub or "not set",
                      "Run: tailscale serve --bg http://127.0.0.1:%d then set Public URL in Settings" % cfg.port))
    if cfg.host in ("0.0.0.0", "::"):
        out.append(_check("Bind address", None, f"{cfg.host}:{cfg.port} - reachable from other networks the PC is on; "
                          "IP allow-list is enforced", "Prefer PCLOUD_HOST=127.0.0.1 with tailscale serve"))
    else:
        out.append(_check("Bind address", True, f"{cfg.host}:{cfg.port}"))
    # admin rights
    if sys.platform == "win32":
        try:
            import ctypes
            elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            elevated = False
        out.append(_check("Least privilege", not elevated,
                          "running elevated (administrator)" if elevated else "running as a standard user",
                          "Run the agent without 'Run as administrator'"))
    # port
    with socket.socket() as s:
        s.settimeout(1)
        listening = s.connect_ex(("127.0.0.1", cfg.port)) == 0
    out.append(_check("Server port", listening or None, f"127.0.0.1:{cfg.port} {'is listening' if listening else 'not listening'}"))
    status = "fail" if any(c["status"] == "fail" for c in out) else "warn" if any(c["status"] == "warn" for c in out) else "ok"
    return {"status": status, "checks": out}
