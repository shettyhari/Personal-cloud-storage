"""Command line interface.

    python -m pcloud.cli run [--supervise]     start the server (supervise = auto-restart on crash)
    python -m pcloud.cli doctor                run diagnostics
    python -m pcloud.cli setup-code            print the first-run setup code
    python -m pcloud.cli reset-password USER   set a new password (local recovery)
    python -m pcloud.cli disable-2fa USER      local recovery if the authenticator is lost
    python -m pcloud.cli mcp-stdio             MCP over stdio for Claude Desktop (needs PCLOUD_MCP_TOKEN)
    python -m pcloud.cli stop                  ask a running supervisor to stop
"""
from __future__ import annotations

import argparse
import getpass
import os
import subprocess
import sys
import time


def cmd_run(args) -> int:
    from .config import get_config
    cfg = get_config()
    flag = cfg.data_dir / "stop.flag"
    if args.supervise:
        flag.unlink(missing_ok=True)
        backoff = 2
        while True:
            started = time.time()
            code = subprocess.call([sys.executable, "-m", "pcloud.cli", "run"], cwd=os.path.dirname(os.path.dirname(__file__)))
            if flag.exists():
                print("Stop requested - supervisor exiting.")
                return 0
            if time.time() - started > 300:
                backoff = 2
            print(f"Server exited with code {code}; restarting in {backoff}s", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
    import uvicorn
    from .main import create_app
    from .routers import system
    app = create_app()
    config = uvicorn.Config(app, host=cfg.host, port=cfg.port, log_level="info", proxy_headers=False,
                            access_log=False, timeout_keep_alive=30, h11_max_incomplete_event_size=64 * 1024)
    server = uvicorn.Server(config)
    system.SHUTDOWN_HOOK.append(lambda: setattr(server, "should_exit", True))
    from .routers.auth_routes import ensure_setup_token
    token = ensure_setup_token()
    print(f"\n  Personal Cloud OS  ->  http://127.0.0.1:{cfg.port}")
    if token:
        print(f"  First-time setup code: {token}\n  Open http://127.0.0.1:{cfg.port}/setup?code={token}\n")
    server.run()
    return 0


def cmd_stop(args) -> int:
    from .config import get_config
    (get_config().data_dir / "stop.flag").write_text("stop requested from CLI")
    import psutil
    me = os.getpid()
    n = 0
    for p in psutil.process_iter(["pid", "cmdline"]):
        cl = " ".join(p.info.get("cmdline") or [])
        if p.info["pid"] != me and "pcloud.cli" in cl and " run" in cl:
            try:
                p.terminate()
                n += 1
            except psutil.Error:
                pass
    print(f"Stopped {n} process(es).")
    return 0


def cmd_doctor(args) -> int:
    from . import db
    from .diagnostics import run_checks
    db.migrate()
    res = run_checks()
    icons = {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]"}
    for c in res["checks"]:
        print(f"{icons[c['status']]} {c['check']}: {c['detail']}")
        if c["fix"] and c["status"] != "ok":
            print(f"        fix: {c['fix']}")
    print(f"\nOverall: {res['status'].upper()}")
    return 1 if res["status"] == "fail" else 0


def cmd_setup_code(args) -> int:
    from . import db
    from .routers.auth_routes import ensure_setup_token
    db.migrate()
    t = ensure_setup_token()
    print(t or "Setup already completed (an administrator exists).")
    return 0


def cmd_reset_password(args) -> int:
    from . import auth, db
    from .security import hash_password, password_problems
    db.migrate()
    u = db.one("SELECT id FROM users WHERE username=?", (args.username,))
    if u is None:
        print("No such user")
        return 1
    pw = getpass.getpass("New password: ")
    if pw != getpass.getpass("Repeat: "):
        print("Passwords differ")
        return 1
    problems = password_problems(pw)
    if problems:
        print(" ".join(problems))
        return 1
    db.execute("UPDATE users SET password_hash=?, password_changed_at=?, failed_logins=0, locked_until=0 WHERE id=?",
               (hash_password(pw), db.now(), u["id"]))
    auth.revoke_user_sessions(u["id"])
    from . import audit
    audit.record("system", "cli", "user.password_reset", args.username)
    print("Password updated; existing sessions signed out.")
    return 0


def cmd_disable_2fa(args) -> int:
    from . import audit, db
    db.migrate()
    db.execute("UPDATE users SET totp_enabled=0, totp_secret=NULL WHERE username=?", (args.username,))
    audit.record("system", "cli", "auth.2fa_disable", args.username)
    print("Two-factor authentication disabled.")
    return 0


def cmd_mcp_stdio(args) -> int:
    import logging
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    from . import db
    from .services import backup, remote, sync, tasks  # noqa: F401
    db.migrate()
    token = os.environ.get("PCLOUD_MCP_TOKEN", "").strip()
    if not token:
        print("Set PCLOUD_MCP_TOKEN to an AI (MCP) token created in the dashboard", file=sys.stderr)
        return 2
    from .mcp_server import run_stdio
    run_stdio(token)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pcloud", description="Personal Cloud OS server agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--supervise", action="store_true")
    r.set_defaults(fn=cmd_run)
    sub.add_parser("stop").set_defaults(fn=cmd_stop)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    sub.add_parser("setup-code").set_defaults(fn=cmd_setup_code)
    rp = sub.add_parser("reset-password")
    rp.add_argument("username")
    rp.set_defaults(fn=cmd_reset_password)
    d2 = sub.add_parser("disable-2fa")
    d2.add_argument("username")
    d2.set_defaults(fn=cmd_disable_2fa)
    sub.add_parser("mcp-stdio").set_defaults(fn=cmd_mcp_stdio)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
