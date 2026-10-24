"""Approved applications / scripts, remote-desktop status and Tailscale detection.

Nothing here accepts a free-form command line. Admins register an executable
with a fixed argument list; users (and optionally the AI) may only launch
those registered entries.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time

import psutil

from .. import db, settings_store
from ..config import get_config
from .jobs import JobContext, handler

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
SCRIPT_EXTS = {".ps1", ".py", ".bat", ".cmd", ".exe"}
_running: dict[int, subprocess.Popen] = {}
_lock = threading.Lock()


def validate_executable(path: str, kind: str) -> str:
    real = os.path.realpath(path)
    if not os.path.isfile(real):
        raise ValueError("Executable not found")
    ext = os.path.splitext(real)[1].lower()
    if ext not in SCRIPT_EXTS:
        raise ValueError(f"Unsupported executable type {ext}. Allowed: {', '.join(sorted(SCRIPT_EXTS))}")
    if kind == "script":
        scripts = os.path.normcase(str(get_config().scripts_dir.resolve()))
        if os.path.commonpath([os.path.normcase(real), scripts]) != scripts:
            raise ValueError(f"Scripts must live in the approved scripts folder: {get_config().scripts_dir}")
    return real


def build_command(executable: str, args: list[str]) -> list[str]:
    ext = os.path.splitext(executable)[1].lower()
    if ext == ".ps1":
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", executable, *args]
    if ext == ".py":
        return [sys.executable, "-I", executable, *args]
    if ext in (".bat", ".cmd"):
        # cmd.exe parses the line; argument strings are fixed by the admin at registration time.
        return ["cmd.exe", "/d", "/c", executable, *args]
    return [executable, *args]


def app_status(app_id: int) -> dict:
    with _lock:
        p = _running.get(app_id)
    if p is None:
        return {"running": False}
    code = p.poll()
    if code is not None:
        with _lock:
            _running.pop(app_id, None)
        return {"running": False, "exit_code": code}
    return {"running": True, "pid": p.pid}


def start_app(app: dict) -> dict:
    st = app_status(app["id"])
    if st["running"]:
        return st
    cmd = build_command(app["executable"], db.loads(app["args"], []))
    p = subprocess.Popen(cmd, cwd=app["working_dir"] or os.path.dirname(app["executable"]),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    with _lock:
        _running[app["id"]] = p
    return {"running": True, "pid": p.pid}


def stop_app(app_id: int) -> dict:
    with _lock:
        p = _running.get(app_id)
    if p is None:
        return {"running": False}
    try:
        parent = psutil.Process(p.pid)
        for child in parent.children(recursive=True):
            child.terminate()
        parent.terminate()
        p.wait(10)
    except (psutil.NoSuchProcess, subprocess.TimeoutExpired):
        try:
            p.kill()
        except Exception:
            pass
    with _lock:
        _running.pop(app_id, None)
    return {"running": False}


@handler("run_script", "Run approved script")
def job_run_script(ctx: JobContext, p: dict):
    row = db.one("SELECT * FROM apps WHERE id=? AND kind='script'", (int(p["app_id"]),))
    if row is None:
        raise ValueError("Script is not registered")
    exe = validate_executable(row["executable"], "script")
    cmd = build_command(exe, db.loads(row["args"], []))
    ctx.log("$ " + " ".join(cmd))
    proc = subprocess.Popen(cmd, cwd=row["working_dir"] or os.path.dirname(exe), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                            errors="replace", creationflags=CREATE_NO_WINDOW)
    deadline = time.monotonic() + row["timeout_seconds"]
    lines = 0
    assert proc.stdout is not None
    for line in proc.stdout:
        ctx.log(line)
        lines += 1
        ctx.progress(None, line.strip()[:200])
        if ctx.cancelled() or time.monotonic() > deadline:
            proc.kill()
            break
    code = proc.wait()
    ctx.check()
    if time.monotonic() > deadline:
        raise TimeoutError(f"Script exceeded its {row['timeout_seconds']}s time limit")
    if code != 0:
        raise RuntimeError(f"Script exited with code {code}")
    return {"exit_code": code, "output_lines": lines}


# ------------------------------------------------------------ tailscale
def tailscale_status() -> dict:
    exe = shutil.which("tailscale") or next((p for p in [r"C:\Program Files\Tailscale\tailscale.exe"]
                                             if os.path.exists(p)), None)
    if not exe:
        return {"installed": False}
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=8,
                             creationflags=CREATE_NO_WINDOW)
        data = json.loads(out.stdout or "{}")
    except Exception as e:
        return {"installed": True, "running": False, "error": str(e)}
    self_ = data.get("Self") or {}
    dns = (self_.get("DNSName") or "").rstrip(".")
    return {
        "installed": True,
        "running": data.get("BackendState") == "Running",
        "state": data.get("BackendState"),
        "hostname": self_.get("HostName"),
        "dns_name": dns,
        "ips": self_.get("TailscaleIPs") or [],
        "tailnet": (data.get("CurrentTailnet") or {}).get("Name"),
        "magic_dns": bool((data.get("CurrentTailnet") or {}).get("MagicDNSEnabled")),
        "peers": [{"name": p.get("HostName"), "os": p.get("OS"), "online": p.get("Online"),
                   "ips": p.get("TailscaleIPs"), "last_seen": p.get("LastSeen")}
                  for p in (data.get("Peer") or {}).values()],
        "suggested_url": f"https://{dns}" if dns else None,
    }


# ------------------------------------------------------- remote desktop
def _rdp_enabled() -> bool | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Terminal Server") as k:
            return winreg.QueryValueEx(k, "fDenyTSConnections")[0] == 0
    except OSError:
        return None


def remote_desktop_status() -> dict:
    cfg = settings_store.get("remote_desktop") or {}
    rustdesk_running = any((p.info.get("name") or "").lower().startswith("rustdesk")
                           for p in psutil.process_iter(["name"]))
    rustdesk_installed = bool(shutil.which("rustdesk")) or os.path.exists(r"C:\Program Files\RustDesk\rustdesk.exe")
    edition = None
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
                edition = winreg.QueryValueEx(k, "EditionID")[0]
        except OSError:
            pass
    rdp_listening = any(c.laddr and c.laddr.port == 3389 and c.status == "LISTEN"
                        for c in _safe_connections())
    return {
        "config": cfg,
        "rustdesk": {"installed": rustdesk_installed, "running": rustdesk_running},
        "rdp": {"enabled": _rdp_enabled(), "listening": rdp_listening, "windows_edition": edition,
                "supported": edition not in (None, "Core", "CoreSingleLanguage", "Home")},
    }


def _safe_connections():
    try:
        return psutil.net_connections(kind="tcp")
    except (psutil.AccessDenied, OSError):
        return []


def rdp_file(host: str) -> str:
    return "\r\n".join([
        f"full address:s:{host}", "prompt for credentials:i:1", "administrative session:i:0",
        "screen mode id:i:2", "use multimon:i:0", "authentication level:i:2", "redirectclipboard:i:1",
        "audiomode:i:0", "",
    ])


def _tailscale_exe() -> str | None:
    return shutil.which("tailscale") or next((p for p in [r"C:\Program Files\Tailscale\tailscale.exe"]
                                              if os.path.exists(p)), None)


def tailscale_serve_status(port: int) -> dict:
    """Is `tailscale serve` publishing this server over HTTPS inside the tailnet?"""
    exe = _tailscale_exe()
    if not exe:
        return {"configured": False, "reason": "tailscale not installed"}
    try:
        out = subprocess.run([exe, "serve", "status", "--json"], capture_output=True, text=True, timeout=8,
                             creationflags=CREATE_NO_WINDOW).stdout
    except Exception as e:
        return {"configured": False, "reason": str(e)}
    targets = (f"127.0.0.1:{port}", f"localhost:{port}")
    configured = any(t in (out or "") for t in targets)
    url = None
    try:
        web = (json.loads(out or "{}").get("Web") or {})
        for hostport in web:
            host = hostport.rsplit(":", 1)[0]
            url = f"https://{host}" if hostport.endswith(":443") else f"https://{hostport}"
            break
    except ValueError:
        pass
    return {"configured": configured, "url": url if configured else None}
