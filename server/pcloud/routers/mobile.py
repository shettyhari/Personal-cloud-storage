"""Connect-a-phone dashboard: connection checklist, devices, and QR sign-in pairing.

Pairing: a signed-in user asks for a one-time code; the dashboard shows it as a QR code pointing at
`<server>/pair?code=…`. The phone scans it, opens that page (over Tailscale) and redeems the code, which
signs the phone in as that user. Codes are random, single use, valid for 10 minutes, kept only in
memory (hashed), and redeeming is rate limited. The network allow-list still applies, so a code is only
usable from this PC or the user's Tailscale devices.
"""
from __future__ import annotations

import io
import secrets
import threading
import time

import segno
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import audit, auth, db, settings_store
from ..auth import Principal, client_ip, current
from ..config import get_config
from ..security import TAILSCALE_NETS, hash_token, limiter
from ..services import remote
from .auth_routes import _set_cookie

router = APIRouter(prefix="/api/mobile", tags=["mobile"])
PAIR_TTL = 600
_codes: dict[str, dict] = {}
_lock = threading.Lock()


def _base_url(request: Request) -> str:
    return (settings_store.get("public_url") or str(request.base_url)).rstrip("/")


def qr_svg(text: str) -> str:
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=6, border=2, dark="#111111", light="#ffffff",
                                    xmldecl=False, svgns=True)
    return buf.getvalue().decode()


def _purge() -> None:
    t = time.time()
    with _lock:
        for k in [k for k, v in _codes.items() if v["expires"] < t]:
            _codes.pop(k, None)


def _is_tailscale(ip: str) -> bool:
    import ipaddress
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in n for n in TAILSCALE_NETS if n.version == a.version)


@router.get("/status")
def status(request: Request, p: Principal = Depends(current)):
    """Everything the phone needs, checked live: server, Tailscale, HTTPS, phone on the tailnet, phone signed in."""
    cfg = get_config()
    ts = remote.tailscale_status()
    serve = remote.tailscale_serve_status(cfg.port) if ts.get("installed") else {"configured": False}
    public_url = settings_store.get("public_url")
    peers = ts.get("peers") or []
    phones = [x for x in peers if (x.get("os") or "").lower() in ("ios", "android")]
    t = db.now()
    tokens = [dict(r) for r in db.all_(
        "SELECT id, name, kind, read_only, last_used, last_ip, created_at FROM tokens "
        "WHERE user_id=? AND revoked=0 AND kind IN ('webdav','device') ORDER BY last_used DESC", (p.user_id,))]
    sessions = [dict(r) for r in db.all_(
        "SELECT created_at, last_seen, ip, user_agent FROM sessions WHERE user_id=? AND mfa_pending=0 "
        "ORDER BY last_seen DESC LIMIT 20", (p.user_id,))]
    mobile_sessions = [s for s in sessions if any(w in (s["user_agent"] or "") for w in ("Android", "iPhone", "iPad", "Mobile"))]
    remote_seen = any(_is_tailscale(s["ip"] or "") for s in sessions) or \
        any(_is_tailscale(x["last_ip"] or "") or (x["last_ip"] == "127.0.0.1" and serve.get("configured"))
            for x in tokens if x["last_used"])
    files_app = any(x["kind"] == "webdav" and x["last_used"] for x in tokens)
    suggested = serve.get("url") or ts.get("suggested_url")
    steps = [
        {"id": "server", "label": "Home PC server running", "ok": True,
         "detail": f"{settings_store.get('server_name')} is online"},
        {"id": "tailscale", "label": "Tailscale connected on the home PC",
         "ok": bool(ts.get("running")),
         "detail": (f"Signed in as {ts.get('dns_name')}" if ts.get("running")
                    else "Installed but not signed in" if ts.get("installed") else "Not installed")},
        {"id": "https", "label": "Private HTTPS address enabled",
         "ok": bool(serve.get("configured")),
         "detail": (serve.get("url") or "tailscale serve is on") if serve.get("configured")
         else f"Run: tailscale serve --bg http://127.0.0.1:{cfg.port}"},
        {"id": "address", "label": "Address saved in Settings",
         "ok": bool(public_url), "detail": public_url or (f"Save {suggested}" if suggested else "Not set yet")},
        {"id": "phone", "label": "Phone joined your Tailscale network",
         "ok": bool(phones) if ts.get("running") else None,
         "detail": (", ".join(f"{x['name']} ({'online' if x.get('online') else 'offline'})" for x in phones)
                    if phones else "Install Tailscale on the phone and sign in with the same account")},
        {"id": "signed_in", "label": "Phone opened the dashboard",
         "ok": bool(mobile_sessions) or None,
         "detail": "Signed in from a phone" if mobile_sessions else "Scan the QR code below with the phone"},
        {"id": "files_app", "label": "File manager app connected",
         "ok": files_app or None,
         "detail": "WebDAV app password in use" if files_app else "Create an app password for Solid Explorer, Documents, …"},
    ]
    return {
        "steps": steps,
        "ready": all(s["ok"] for s in steps[:4]),
        "base_url": public_url or suggested or _base_url(request),
        "remote_access_seen": remote_seen,
        "tailscale": {k: ts.get(k) for k in ("installed", "running", "dns_name", "ips", "tailnet")},
        "serve": serve,
        "peers": [{"name": x.get("name"), "os": x.get("os"), "online": x.get("online"), "last_seen": x.get("last_seen"),
                   "ips": x.get("ips")} for x in peers],
        "devices": tokens,
        "mobile_sessions": [{"last_seen": s["last_seen"], "ip": s["ip"], "user_agent": (s["user_agent"] or "")[:120]}
                            for s in mobile_sessions],
        "now": t,
    }


class PairBody(BaseModel):
    base_url: str | None = Field(default=None, max_length=300)


@router.post("/pair")
def create_pair(body: PairBody, request: Request, p: Principal = Depends(current)):
    if p.via != "session":
        raise HTTPException(403, "Pairing must be started from a signed-in browser")
    if not limiter.hit(f"pair-create:{p.user_id}", 20, 600):
        raise HTTPException(429, "Too many pairing codes. Wait a few minutes.")
    _purge()
    code = secrets.token_urlsafe(24)
    base = (body.base_url or "").strip().rstrip("/") or _base_url(request)
    if not base.startswith(("https://", "http://")):
        raise HTTPException(400, "Invalid address")
    with _lock:
        _codes[hash_token(code)] = {"user_id": p.user_id, "username": p.username, "expires": time.time() + PAIR_TTL,
                                    "by": p.label}
    url = f"{base}/pair?code={code}"
    auth.log(p, "mobile.pair_code", details={"base": base})
    return {"url": url, "expires_in": PAIR_TTL, "qr_svg": qr_svg(url),
            "https": base.startswith("https://"), "base": base}


class RedeemBody(BaseModel):
    code: str = Field(min_length=10, max_length=100)


@router.post("/pair/redeem")
def redeem(body: RedeemBody, request: Request, response: Response):
    ip = client_ip(request)
    if not limiter.hit(f"pair-redeem:{ip}", 10, 300):
        raise HTTPException(429, "Too many attempts. Wait a few minutes.")
    with _lock:
        entry = _codes.pop(hash_token(body.code.strip()), None)
    if entry is None or entry["expires"] < time.time():
        audit.record("device", ip, "mobile.pair", None, "failed", ip)
        raise HTTPException(400, "This QR code has expired or was already used. Create a new one on the dashboard.")
    u = db.one("SELECT id, username, disabled FROM users WHERE id=?", (entry["user_id"],))
    if u is None or u["disabled"]:
        raise HTTPException(403, "Account not available")
    token, csrf = auth.create_session(u["id"], ip, request.headers.get("user-agent", ""))
    _set_cookie(response, token)
    audit.record("user", u["username"], "mobile.pair", request.headers.get("user-agent", "")[:120], "ok", ip,
                 {"paired_by": entry["by"]})
    return {"ok": True, "csrf": csrf, "username": u["username"]}


@router.get("/qr")
def qr(text: str, p: Principal = Depends(current)):
    """QR code for any address shown on the Connect page (e.g. the WebDAV URL)."""
    if len(text) > 500:
        raise HTTPException(400, "Too long")
    return Response(qr_svg(text), media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})

