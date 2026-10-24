"""Authentication: sessions, API/device tokens, permissions and FastAPI dependencies."""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request

from . import audit, db, settings_store
from .config import get_config
from .paths import Root, get_root, list_roots
from .security import hash_token, new_token

SESSION_COOKIE = "pcloud_session"
CSRF_HEADER = "x-csrf-token"
TOKEN_KINDS = {"device", "webdav", "mcp", "api"}


@dataclass
class Principal:
    user_id: int
    username: str
    role: str
    via: str                       # session | token
    token_id: int | None = None
    token_kind: str | None = None
    token_name: str | None = None
    read_only: bool = False
    ip: str | None = None
    session_id: str | None = None
    csrf: str | None = None
    scopes: list[str] = field(default_factory=list)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin" and not self.read_only

    @property
    def actor_type(self) -> str:
        if self.token_kind == "mcp":
            return "ai"
        if self.token_kind in ("device", "webdav"):
            return "device"
        return "user"

    @property
    def label(self) -> str:
        if self.token_name:
            return f"{self.username}/{self.token_name}"
        return self.username

    # ------------------------------------------------------------ permissions
    def root_access(self, root: Root) -> str | None:
        """Return None (no access), 'r' or 'rw'."""
        if self.token_kind == "mcp":
            if not root.ai_read:
                return None
            access = "rw" if root.ai_write else "r"
        elif self.role == "admin":
            access = "rw"
        else:
            row = db.one("SELECT can_write FROM root_permissions WHERE user_id=? AND root_id=?",
                         (self.user_id, root.id))
            if row is None:
                return None
            access = "rw" if row["can_write"] else "r"
        if self.token_kind == "webdav" and not root.webdav:
            return None
        if root.read_only or self.read_only or self.role == "readonly":
            access = "r"
        return access

    def visible_roots(self) -> list[Root]:
        return [r for r in list_roots() if self.root_access(r)]

    def require_root(self, root_id: int, write: bool = False) -> Root:
        try:
            root = get_root(root_id)
        except LookupError:
            raise HTTPException(404, "Storage location not found")
        access = self.root_access(root)
        if access is None:
            raise HTTPException(403, "You do not have access to this storage location")
        if write and access != "rw":
            raise HTTPException(403, "This storage location is read-only for you")
        return root


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------- sessions
def create_session(user_id: int, ip: str, user_agent: str, mfa_pending: bool = False) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    t = db.now()
    hours = get_config().session_hours
    db.execute("INSERT INTO sessions(id, user_id, csrf, created_at, expires_at, last_seen, ip, user_agent, mfa_pending) "
               "VALUES(?,?,?,?,?,?,?,?,?)",
               (hash_token(token), user_id, csrf, t, t + (600 if mfa_pending else hours * 3600), t, ip,
                (user_agent or "")[:300], int(mfa_pending)))
    return token, csrf


def revoke_session(session_id: str) -> None:
    db.execute("DELETE FROM sessions WHERE id=?", (session_id,))


def revoke_user_sessions(user_id: int, except_id: str | None = None) -> None:
    db.execute("DELETE FROM sessions WHERE user_id=? AND id IS NOT ?", (user_id, except_id))


def cleanup_sessions() -> None:
    idle = float(settings_store.get("session_idle_minutes") or 120) * 60
    t = db.now()
    db.execute("DELETE FROM sessions WHERE expires_at < ? OR last_seen < ?", (t, t - idle))


def _session_principal(token: str, ip: str) -> Principal | None:
    sid = hash_token(token)
    row = db.one("SELECT s.*, u.username, u.role, u.disabled FROM sessions s JOIN users u ON u.id=s.user_id "
                 "WHERE s.id=?", (sid,))
    if row is None or row["disabled"] or row["mfa_pending"]:
        return None
    t = db.now()
    idle = float(settings_store.get("session_idle_minutes") or 120) * 60
    if row["expires_at"] < t or row["last_seen"] < t - idle:
        revoke_session(sid)
        return None
    if t - row["last_seen"] > 30:
        db.execute("UPDATE sessions SET last_seen=? WHERE id=?", (t, sid))
    return Principal(user_id=row["user_id"], username=row["username"], role=row["role"], via="session",
                     ip=ip, session_id=sid, csrf=row["csrf"])


# ------------------------------------------------------------------ tokens
def create_token(user_id: int, name: str, kind: str, read_only: bool = False,
                 expires_days: int | None = None) -> tuple[str, int]:
    if kind not in TOKEN_KINDS:
        raise ValueError("invalid token kind")
    token = new_token({"mcp": "pcai", "webdav": "pcdav"}.get(kind, "pcdev"))
    t = db.now()
    cur = db.execute("INSERT INTO tokens(user_id, name, kind, token_hash, prefix, read_only, created_at, expires_at) "
                     "VALUES(?,?,?,?,?,?,?,?)",
                     (user_id, name.strip()[:80] or kind, kind, hash_token(token), token[:10], int(read_only), t,
                      t + expires_days * 86400 if expires_days else None))
    return token, cur.lastrowid


def token_principal(token: str, ip: str, kinds: set[str] | None = None) -> Principal | None:
    row = db.one("SELECT t.*, u.username, u.role, u.disabled FROM tokens t JOIN users u ON u.id=t.user_id "
                 "WHERE t.token_hash=?", (hash_token(token),))
    if row is None or row["revoked"] or row["disabled"]:
        return None
    if row["expires_at"] and row["expires_at"] < db.now():
        return None
    if kinds and row["kind"] not in kinds:
        return None
    t = db.now()
    if not row["last_used"] or t - row["last_used"] > 60 or row["last_ip"] != ip:
        db.execute("UPDATE tokens SET last_used=?, last_ip=? WHERE id=?", (t, ip, row["id"]))
    return Principal(user_id=row["user_id"], username=row["username"], role=row["role"], via="token",
                     token_id=row["id"], token_kind=row["kind"], token_name=row["name"],
                     read_only=bool(row["read_only"]), ip=ip)


# ---------------------------------------------------------- dependencies
def optional_principal(request: Request) -> Principal | None:
    cached = getattr(request.state, "principal", None)
    if cached is not None:
        return cached
    ip = client_ip(request)
    p = None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        p = token_principal(auth[7:].strip(), ip, kinds={"device", "api"})
    else:
        cookie = request.cookies.get(SESSION_COOKIE)
        if cookie:
            p = _session_principal(cookie, ip)
            if p and request.method not in ("GET", "HEAD", "OPTIONS"):
                sent = request.headers.get(CSRF_HEADER, "")
                if not sent or not secrets.compare_digest(sent, p.csrf or ""):
                    raise HTTPException(403, "Missing or invalid CSRF token. Reload the page and try again.")
    request.state.principal = p
    return p


def current(request: Request) -> Principal:
    p = optional_principal(request)
    if p is None:
        raise HTTPException(401, "Sign in required")
    return p


def admin(p: Principal = Depends(current)) -> Principal:
    if not p.is_admin:
        raise HTTPException(403, "Administrator access required")
    if p.via == "token":
        raise HTTPException(403, "Administrative changes require an interactive sign-in")
    return p


def writer(p: Principal = Depends(current)) -> Principal:
    if p.read_only or p.role == "readonly":
        raise HTTPException(403, "Read-only account")
    return p


def log(p: Principal, action: str, target: str | None = None, status: str = "ok", details: dict | None = None):
    audit.from_principal(p, action, target, status, details)
