"""Device / app tokens: WebDAV app passwords, MCP (AI) tokens, API tokens for scripts and devices."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import auth, db, settings_store
from ..auth import Principal, current

router = APIRouter(prefix="/api/tokens", tags=["devices"])


class NewToken(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: str = Field(pattern=r"^(device|webdav|mcp|api)$")
    read_only: bool = False
    expires_days: int | None = Field(default=None, ge=1, le=3650)


def _interactive(p: Principal) -> None:
    if p.via != "session":
        raise HTTPException(403, "Tokens can only be managed from an interactive sign-in")


@router.get("")
def list_tokens(all_users: bool = False, p: Principal = Depends(current)):
    _interactive(p)
    if all_users and p.is_admin:
        rows = db.all_("SELECT t.*, u.username FROM tokens t JOIN users u ON u.id=t.user_id ORDER BY t.created_at DESC")
    else:
        rows = db.all_("SELECT t.*, u.username FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.user_id=? "
                       "ORDER BY t.created_at DESC", (p.user_id,))
    return [{k: r[k] for k in ("id", "name", "kind", "prefix", "read_only", "created_at", "last_used", "last_ip",
                               "expires_at", "revoked", "username")} for r in rows]


@router.post("")
def create_token(body: NewToken, request: Request, p: Principal = Depends(current)):
    _interactive(p)
    if body.kind == "mcp":
        if not p.is_admin:
            raise HTTPException(403, "Only administrators can connect AI assistants")
        if not settings_store.get("mcp_enabled"):
            raise HTTPException(400, "AI integration (MCP) is disabled in Settings")
    token, tid = auth.create_token(p.user_id, body.name, body.kind, body.read_only, body.expires_days)
    auth.log(p, "token.create", body.name, details={"kind": body.kind, "id": tid, "read_only": body.read_only})
    base = (settings_store.get("public_url") or str(request.base_url)).rstrip("/")
    return {"id": tid, "token": token, "username": p.username,
            "webdav_url": f"{base}/dav/", "mcp_url": f"{base}/mcp",
            "notice": "Copy this secret now - it will not be shown again."}


@router.delete("/{token_id}")
def revoke(token_id: int, p: Principal = Depends(current)):
    _interactive(p)
    row = db.one("SELECT * FROM tokens WHERE id=?", (token_id,))
    if row is None or (row["user_id"] != p.user_id and not p.is_admin):
        raise HTTPException(404, "Token not found")
    db.execute("UPDATE tokens SET revoked=1 WHERE id=?", (token_id,))
    auth.log(p, "token.revoke", row["name"], details={"kind": row["kind"], "id": token_id})
    return {"ok": True}
