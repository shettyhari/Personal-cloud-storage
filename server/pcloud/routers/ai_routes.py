"""AI workspace: approvals queue, AI activity, connection instructions, optional local model."""
from __future__ import annotations

import sys

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import auth, db, settings_store
from ..auth import Principal, admin, current
from ..config import PROJECT_ROOT
from ..paths import resolve
from ..services import approvals, extract, fileops, local_ai

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/approvals")
def list_approvals(status: str = "pending", p: Principal = Depends(admin)):
    approvals.expire_old()
    if status == "all":
        rows = db.all_("SELECT * FROM approvals ORDER BY created_at DESC LIMIT 200")
    else:
        rows = db.all_("SELECT * FROM approvals WHERE status=? ORDER BY created_at DESC LIMIT 200", (status,))
    return [approvals.to_dict(r) for r in rows]


class Decision(BaseModel):
    approve: bool


@router.post("/approvals/{approval_id}")
def decide(approval_id: int, body: Decision, p: Principal = Depends(admin)):
    try:
        return approvals.decide(approval_id, body.approve, p.label)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.get("/activity")
def ai_activity(limit: int = 100, p: Principal = Depends(current)):
    rows = db.all_("SELECT * FROM activity WHERE actor_type='ai' ORDER BY ts DESC LIMIT ?", (min(limit, 500),))
    return [dict(r) | {"details": db.loads(r["details"], None)} for r in rows]


@router.get("/connection")
def connection_info(request: Request, p: Principal = Depends(current)):
    base = (settings_store.get("public_url") or str(request.base_url)).rstrip("/")
    py = str(PROJECT_ROOT / "server" / ".venv" / "Scripts" / "python.exe") if sys.platform == "win32" else sys.executable
    return {
        "mcp_enabled": settings_store.get("mcp_enabled"),
        "mcp_url": f"{base}/mcp",
        "ai_roots": [r.to_dict() for r in p.visible_roots() if r.ai_read],
        "output_folder": settings_store.get("ai_output_folder"),
        "claude_desktop_local": {
            "mcpServers": {"personal-cloud": {
                "command": py,
                "args": ["-m", "pcloud.cli", "mcp-stdio"],
                "env": {"PCLOUD_MCP_TOKEN": "<paste MCP token>", "PYTHONPATH": str(PROJECT_ROOT / "server")},
            }}},
        "claude_desktop_remote": {
            "mcpServers": {"personal-cloud": {
                "command": "npx",
                "args": ["-y", "mcp-remote@latest", f"{base}/mcp", "--header", "Authorization:${PCLOUD_AUTH}"],
                "env": {"PCLOUD_AUTH": "Bearer <paste MCP token>"},
            }}},
        "claude_code": f'claude mcp add --transport http personal-cloud {base}/mcp --header "Authorization: Bearer <MCP token>"',
        "local_ai": local_ai.status(),
    }


class LocalSummarize(BaseModel):
    root_id: int
    path: str = Field(max_length=4096)
    instructions: str = Field(default="", max_length=2000)
    save_to: str | None = Field(default=None, max_length=4096)


@router.post("/local/summarize")
def local_summarize(body: LocalSummarize, p: Principal = Depends(current)):
    root = p.require_root(body.root_id)
    abs_path, norm = resolve(root, body.path, must_exist=True)
    try:
        text = extract.extract_text(abs_path, 200_000)
        summary = local_ai.summarize(text, body.instructions)
    except ValueError as e:
        raise HTTPException(415, str(e))
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Local AI provider error: {e}")
    saved = None
    if body.save_to:
        root_w = p.require_root(body.root_id, write=True)
        saved = fileops.write_bytes(root_w, body.save_to, summary.encode(), overwrite=False)["path"]
    auth.log(p, "ai.local_summarize", f"{root.name}/{norm}", details={"saved": saved})
    return {"summary": summary, "saved": saved}
