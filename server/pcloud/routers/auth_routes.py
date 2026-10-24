"""Setup wizard bootstrap, sign-in, 2FA, users and sessions."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import audit, auth, db, settings_store
from ..auth import Principal, SESSION_COOKIE, admin, client_ip, current
from ..config import get_config
from ..security import (burn_password_time, hash_password, limiter, new_totp_secret, password_problems, totp_uri,
                        verify_password, verify_totp)

router = APIRouter(prefix="/api", tags=["auth"])
MFA_COOKIE = "pcloud_mfa"


def setup_token_path():
    return get_config().data_dir / "setup-token.txt"


def ensure_setup_token() -> str | None:
    """Create a one-time setup token while no admin exists. Printed to the console and stored on disk."""
    if db.one("SELECT 1 FROM users LIMIT 1"):
        p = setup_token_path()
        if p.exists():
            p.unlink()
        return None
    p = setup_token_path()
    if p.exists():
        return p.read_text().strip()
    token = secrets.token_urlsafe(18)
    p.write_text(token)
    return token


def _set_cookie(resp: Response, token: str) -> None:
    cfg = get_config()
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict", secure=cfg.secure_cookies,
                    max_age=cfg.session_hours * 3600, path="/")


def _user_public(u) -> dict:
    return {"id": u["id"], "username": u["username"], "role": u["role"], "totp_enabled": bool(u["totp_enabled"]),
            "disabled": bool(u["disabled"]), "created_at": u["created_at"]}


# -------------------------------------------------------------- setup
@router.get("/setup/status")
def setup_status():
    has_users = db.one("SELECT 1 FROM users LIMIT 1") is not None
    return {"needs_admin": not has_users, "setup_complete": bool(settings_store.get("setup_complete")),
            "server_name": settings_store.get("server_name")}


class SetupAdmin(BaseModel):
    setup_token: str
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=1, max_length=256)
    server_name: str | None = Field(default=None, max_length=60)


@router.post("/setup/admin")
def setup_admin(body: SetupAdmin, request: Request, response: Response):
    ip = client_ip(request)
    if not limiter.hit(f"setup:{ip}", 10, 300):
        raise HTTPException(429, "Too many attempts. Wait a few minutes.")
    if db.one("SELECT 1 FROM users LIMIT 1"):
        raise HTTPException(409, "Setup has already been completed")
    expected = ensure_setup_token()
    if not expected or not secrets.compare_digest(body.setup_token.strip(), expected):
        audit.record("system", ip, "setup.admin", body.username, "denied", ip)
        raise HTTPException(403, "Invalid setup code. Find it in the server console or in setup-token.txt "
                                 "inside the data folder.")
    problems = password_problems(body.password)
    if problems:
        raise HTTPException(400, " ".join(problems))
    t = db.now()
    cur = db.execute("INSERT INTO users(username, password_hash, role, created_at, password_changed_at) "
                     "VALUES(?,?,?,?,?)", (body.username, hash_password(body.password), "admin", t, t))
    if body.server_name:
        settings_store.set("server_name", body.server_name.strip())
    setup_token_path().unlink(missing_ok=True)
    token, csrf = auth.create_session(cur.lastrowid, ip, request.headers.get("user-agent", ""))
    _set_cookie(response, token)
    audit.record("user", body.username, "setup.admin", body.username, "ok", ip)
    return {"ok": True, "csrf": csrf}


@router.post("/setup/complete")
def setup_complete(p: Principal = Depends(admin)):
    settings_store.set("setup_complete", True)
    auth.log(p, "setup.complete")
    return {"ok": True}


# -------------------------------------------------------------- login
class Login(BaseModel):
    username: str = Field(max_length=80)
    password: str = Field(max_length=256)


@router.post("/auth/login")
def login(body: Login, request: Request, response: Response):
    ip = client_ip(request)
    if not limiter.hit(f"login-ip:{ip}", 10, 60) or not limiter.hit(f"login-user:{body.username.lower()}", 20, 900):
        audit.record("user", body.username, "auth.login", None, "rate_limited", ip)
        raise HTTPException(429, "Too many sign-in attempts. Wait a minute and try again.")
    u = db.one("SELECT * FROM users WHERE username=?", (body.username.strip(),))
    if u is None:
        burn_password_time(body.password)
        audit.record("user", body.username, "auth.login", None, "failed", ip)
        raise HTTPException(401, "Incorrect username or password")
    if u["locked_until"] > db.now():
        raise HTTPException(423, "Account temporarily locked after repeated failures. Try again in 15 minutes.")
    if u["disabled"] or not verify_password(body.password, u["password_hash"]):
        fails = u["failed_logins"] + 1
        lock = db.now() + 900 if fails >= 8 else 0
        db.execute("UPDATE users SET failed_logins=?, locked_until=? WHERE id=?", (0 if lock else fails, lock, u["id"]))
        audit.record("user", u["username"], "auth.login", None, "failed", ip)
        raise HTTPException(401, "Incorrect username or password")
    db.execute("UPDATE users SET failed_logins=0, locked_until=0 WHERE id=?", (u["id"],))
    if u["totp_enabled"]:
        token, _ = auth.create_session(u["id"], ip, request.headers.get("user-agent", ""), mfa_pending=True)
        response.set_cookie(MFA_COOKIE, token, httponly=True, samesite="strict", secure=get_config().secure_cookies,
                            max_age=600, path="/api/auth")
        return {"mfa_required": True}
    token, csrf = auth.create_session(u["id"], ip, request.headers.get("user-agent", ""))
    _set_cookie(response, token)
    audit.record("user", u["username"], "auth.login", None, "ok", ip)
    return {"ok": True, "csrf": csrf, "needs_2fa_setup": u["role"] == "admin" and bool(settings_store.get("require_2fa_for_admin"))}


class MfaBody(BaseModel):
    code: str = Field(max_length=12)


@router.post("/auth/mfa")
def login_mfa(body: MfaBody, request: Request, response: Response):
    ip = client_ip(request)
    if not limiter.hit(f"mfa:{ip}", 6, 60):
        raise HTTPException(429, "Too many attempts")
    pending = request.cookies.get(MFA_COOKIE)
    if not pending:
        raise HTTPException(401, "Sign in again")
    from ..security import hash_token
    sid = hash_token(pending)
    row = db.one("SELECT s.*, u.username, u.totp_secret FROM sessions s JOIN users u ON u.id=s.user_id "
                 "WHERE s.id=? AND s.mfa_pending=1 AND s.expires_at>?", (sid, db.now()))
    if row is None:
        raise HTTPException(401, "Sign-in expired. Start again.")
    if not verify_totp(row["totp_secret"], body.code):
        audit.record("user", row["username"], "auth.mfa", None, "failed", ip)
        raise HTTPException(401, "Invalid code")
    auth.revoke_session(sid)
    token, csrf = auth.create_session(row["user_id"], ip, request.headers.get("user-agent", ""))
    response.delete_cookie(MFA_COOKIE, path="/api/auth")
    _set_cookie(response, token)
    audit.record("user", row["username"], "auth.login", None, "ok", ip, {"mfa": True})
    return {"ok": True, "csrf": csrf}


@router.post("/auth/logout")
def logout(response: Response, p: Principal = Depends(current)):
    if p.session_id:
        auth.revoke_session(p.session_id)
    response.delete_cookie(SESSION_COOKIE, path="/")
    auth.log(p, "auth.logout")
    return {"ok": True}


@router.get("/auth/me")
def me(p: Principal = Depends(current)):
    u = db.one("SELECT * FROM users WHERE id=?", (p.user_id,))
    return {"user": _user_public(u), "csrf": p.csrf, "via": p.via,
            "roots": [r.to_dict() | {"access": p.root_access(r)} for r in p.visible_roots()],
            "server_name": settings_store.get("server_name"),
            "setup_complete": bool(settings_store.get("setup_complete"))}


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


@router.post("/auth/password")
def change_password(body: PasswordChange, p: Principal = Depends(current)):
    if p.via != "session":
        raise HTTPException(403, "Interactive sign-in required")
    u = db.one("SELECT * FROM users WHERE id=?", (p.user_id,))
    if not verify_password(body.current_password, u["password_hash"]):
        raise HTTPException(400, "Current password is incorrect")
    problems = password_problems(body.new_password)
    if problems:
        raise HTTPException(400, " ".join(problems))
    db.execute("UPDATE users SET password_hash=?, password_changed_at=? WHERE id=?",
               (hash_password(body.new_password), db.now(), p.user_id))
    auth.revoke_user_sessions(p.user_id, except_id=p.session_id)
    auth.log(p, "auth.password_change")
    return {"ok": True}


# ---------------------------------------------------------------- 2FA
@router.post("/auth/2fa/begin")
def totp_begin(p: Principal = Depends(current)):
    if p.via != "session":
        raise HTTPException(403, "Interactive sign-in required")
    secret = new_totp_secret()
    db.execute("UPDATE users SET totp_secret=?, totp_enabled=0 WHERE id=? AND totp_enabled=0", (secret, p.user_id))
    u = db.one("SELECT totp_secret, totp_enabled FROM users WHERE id=?", (p.user_id,))
    if u["totp_enabled"]:
        raise HTTPException(409, "Two-factor authentication is already enabled")
    return {"secret": secret, "uri": totp_uri(secret, p.username)}


@router.post("/auth/2fa/enable")
def totp_enable(body: MfaBody, p: Principal = Depends(current)):
    u = db.one("SELECT totp_secret FROM users WHERE id=?", (p.user_id,))
    if not u["totp_secret"] or not verify_totp(u["totp_secret"], body.code):
        raise HTTPException(400, "Code did not match. Check your authenticator app's clock.")
    db.execute("UPDATE users SET totp_enabled=1 WHERE id=?", (p.user_id,))
    auth.log(p, "auth.2fa_enable")
    return {"ok": True}


class Disable2fa(BaseModel):
    password: str = Field(max_length=256)
    code: str = Field(max_length=12)


@router.post("/auth/2fa/disable")
def totp_disable(body: Disable2fa, p: Principal = Depends(current)):
    u = db.one("SELECT * FROM users WHERE id=?", (p.user_id,))
    if not verify_password(body.password, u["password_hash"]) or not verify_totp(u["totp_secret"] or "", body.code):
        raise HTTPException(400, "Password or code incorrect")
    db.execute("UPDATE users SET totp_enabled=0, totp_secret=NULL WHERE id=?", (p.user_id,))
    auth.log(p, "auth.2fa_disable")
    return {"ok": True}


# ---------------------------------------------------------- sessions
@router.get("/auth/sessions")
def my_sessions(p: Principal = Depends(current)):
    rows = db.all_("SELECT id, created_at, last_seen, expires_at, ip, user_agent FROM sessions "
                   "WHERE user_id=? AND mfa_pending=0 ORDER BY last_seen DESC", (p.user_id,))
    return [dict(r) | {"id": r["id"][:12], "current": r["id"] == p.session_id} for r in rows]


@router.delete("/auth/sessions/{sid_prefix}")
def revoke_my_session(sid_prefix: str, p: Principal = Depends(current)):
    rows = db.all_("SELECT id FROM sessions WHERE user_id=? AND id LIKE ?", (p.user_id, sid_prefix[:12] + "%"))
    for r in rows:
        auth.revoke_session(r["id"])
    auth.log(p, "auth.session_revoke", sid_prefix[:12])
    return {"revoked": len(rows)}


@router.post("/auth/sessions/revoke-others")
def revoke_others(p: Principal = Depends(current)):
    auth.revoke_user_sessions(p.user_id, except_id=p.session_id)
    auth.log(p, "auth.session_revoke_all")
    return {"ok": True}


# ------------------------------------------------------------- users
class NewUser(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(max_length=256)
    role: str = Field(pattern=r"^(admin|user|readonly)$")


@router.get("/users")
def list_users(p: Principal = Depends(admin)):
    users = [_user_public(u) for u in db.all_("SELECT * FROM users ORDER BY username")]
    perms = db.all_("SELECT * FROM root_permissions")
    for u in users:
        u["permissions"] = [{"root_id": r["root_id"], "can_write": bool(r["can_write"])}
                            for r in perms if r["user_id"] == u["id"]]
    return users


@router.post("/users")
def create_user(body: NewUser, p: Principal = Depends(admin)):
    problems = password_problems(body.password)
    if problems:
        raise HTTPException(400, " ".join(problems))
    if db.one("SELECT 1 FROM users WHERE username=?", (body.username,)):
        raise HTTPException(409, "Username already exists")
    t = db.now()
    cur = db.execute("INSERT INTO users(username, password_hash, role, created_at, password_changed_at) VALUES(?,?,?,?,?)",
                     (body.username, hash_password(body.password), body.role, t, t))
    auth.log(p, "user.create", body.username, details={"role": body.role})
    return {"id": cur.lastrowid}


class UserUpdate(BaseModel):
    role: str | None = Field(default=None, pattern=r"^(admin|user|readonly)$")
    disabled: bool | None = None
    password: str | None = Field(default=None, max_length=256)
    permissions: list[dict] | None = None


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, p: Principal = Depends(admin)):
    u = db.one("SELECT * FROM users WHERE id=?", (user_id,))
    if u is None:
        raise HTTPException(404, "User not found")
    if user_id == p.user_id and (body.disabled or (body.role and body.role != "admin")):
        raise HTTPException(400, "You cannot demote or disable your own account")
    if body.role:
        db.execute("UPDATE users SET role=? WHERE id=?", (body.role, user_id))
    if body.disabled is not None:
        db.execute("UPDATE users SET disabled=? WHERE id=?", (int(body.disabled), user_id))
        if body.disabled:
            auth.revoke_user_sessions(user_id)
            db.execute("UPDATE tokens SET revoked=1 WHERE user_id=?", (user_id,))
    if body.password:
        problems = password_problems(body.password)
        if problems:
            raise HTTPException(400, " ".join(problems))
        db.execute("UPDATE users SET password_hash=?, password_changed_at=? WHERE id=?",
                   (hash_password(body.password), db.now(), user_id))
        auth.revoke_user_sessions(user_id)
    if body.permissions is not None:
        with db.tx() as c:
            c.execute("DELETE FROM root_permissions WHERE user_id=?", (user_id,))
            for perm in body.permissions:
                c.execute("INSERT INTO root_permissions(user_id, root_id, can_write) VALUES(?,?,?)",
                          (user_id, int(perm["root_id"]), int(bool(perm.get("can_write")))))
    auth.log(p, "user.update", u["username"], details=body.model_dump(exclude={"password"}, exclude_none=True))
    return {"ok": True}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, p: Principal = Depends(admin)):
    if user_id == p.user_id:
        raise HTTPException(400, "You cannot delete your own account")
    u = db.one("SELECT username FROM users WHERE id=?", (user_id,))
    if u is None:
        raise HTTPException(404, "User not found")
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    auth.log(p, "user.delete", u["username"])
    return {"ok": True}
