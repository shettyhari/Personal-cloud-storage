"""Local controls for the cloud link (only reachable on the PC itself, never through the tunnel)."""
from __future__ import annotations

import socket

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import auth
from ..auth import Principal, admin
from ..cloudlink import config as linkcfg
from ..cloudlink.link import cloud_link, enroll

router = APIRouter(prefix="/api/cloud", tags=["cloud"])


@router.get("/status")
def status(p: Principal = Depends(admin)):
    return cloud_link.status()


class LinkBody(BaseModel):
    code: str = Field(min_length=20, max_length=2000)
    name: str = Field(default="", max_length=60)


@router.post("/link")
def link(body: LinkBody, p: Principal = Depends(admin)):
    if p.via != "session":
        raise HTTPException(403, "Link this server from the dashboard on the PC itself")
    if linkcfg.load():
        raise HTTPException(409, "Already linked. Unlink first to link to another dashboard.")
    try:
        cfg = enroll(body.code, body.name.strip() or socket.gethostname(), p.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    cloud_link.start()
    auth.log(p, "cloud.link", cfg.name, details={"server_id": cfg.server_id})
    return cloud_link.status()


@router.post("/unlink")
def unlink(p: Principal = Depends(admin)):
    cfg = linkcfg.load()
    linkcfg.clear()
    cloud_link.stop()
    auth.log(p, "cloud.unlink", cfg.name if cfg else None)
    return {"ok": True, "note": "Also remove the server in the web dashboard (Servers) to delete its cloud account."}
