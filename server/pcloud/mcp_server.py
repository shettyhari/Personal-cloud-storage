"""Model Context Protocol server exposing approved files to AI assistants (Claude).

Transports
  * Streamable HTTP at /mcp on the main server (Bearer token = an "AI (MCP)" token created in the dashboard).
  * stdio via `python -m pcloud.cli mcp-stdio` with PCLOUD_MCP_TOKEN set (Claude Desktop on this PC).

Guarantees
  * Only storage locations with "AI can read" enabled are visible; writes need "AI can write".
  * Creating new files is allowed in writable locations; moving, renaming, deleting, overwriting, bulk changes and
    running scripts create an approval request that the owner must approve in the dashboard.
  * Every tool call is written to the activity log as an AI action.
  * No tool executes arbitrary commands.
"""
from __future__ import annotations

import base64
import contextvars
import os
import time
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from . import audit, auth, db, settings_store
from .paths import NotFound, PathError, Root, list_roots, normalize_rel, resolve
from .services import approvals, extract, fileops, index, local_ai, tasks
from .services.fileops import Conflict
from .services.jobs import get_manager, job_dict

current_principal: contextvars.ContextVar[auth.Principal | None] = contextvars.ContextVar("mcp_principal", default=None)

INSTRUCTIONS = """You are connected to the owner's personal cloud running on their home PC.
Use list_locations first to see which storage locations you may access. Paths are relative to a location.
search_files supports filename and full-text search plus date/extension filters - translate natural-language
requests (e.g. "marketing reports from October") into a query + filters, then read_document the hits.
Creating new files (save_document, create_folder) is allowed in writable locations. Moving, renaming, deleting,
overwriting and bulk reorganizing require the owner's approval: those tools return an approval_id; tell the user
to approve it in the dashboard, then check get_approval_status. Never claim a change happened before it is executed."""

mcp = FastMCP("personal-cloud", instructions=INSTRUCTIONS, stateless_http=True, json_response=True,
              transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))


class ToolError(Exception):
    pass


def _p() -> auth.Principal:
    p = current_principal.get()
    if p is None:
        raise ToolError("Not authenticated")
    if not settings_store.get("mcp_enabled"):
        raise ToolError("AI integration is disabled by the owner")
    return p


def _roots(p: auth.Principal) -> list[Root]:
    return [r for r in list_roots() if p.root_access(r)]


def _root(p: auth.Principal, location: str, write: bool = False) -> Root:
    for r in _roots(p):
        if r.name.lower() == (location or "").strip().lower() or str(r.id) == str(location):
            if write and p.root_access(r) != "rw":
                raise ToolError(f"Location '{r.name}' is read-only for AI. The owner can enable 'AI can write'.")
            return r
    raise ToolError(f"Unknown or unauthorized location '{location}'. Call list_locations.")


def _log(action: str, target: str | None = None, status: str = "ok", details: dict | None = None):
    p = current_principal.get()
    if p:
        audit.from_principal(p, f"ai.{action}", target, status, details)


def tool(fn):
    """Wrap a tool: auth check, error translation and audit logging."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            _p()
            return fn(*args, **kwargs)
        except ToolError as e:
            _log(fn.__name__, status="denied", details={"error": str(e), "args": _short(kwargs)})
            return {"error": str(e)}
        except (PathError, NotFound, Conflict, ValueError) as e:
            _log(fn.__name__, status="error", details={"error": str(e), "args": _short(kwargs)})
            return {"error": str(e)}
        except PermissionError as e:
            return {"error": f"Access denied by Windows: {e}"}
    return mcp.tool()(wrapper)


def _short(kwargs: dict) -> dict:
    return {k: (v[:200] if isinstance(v, str) else v) for k, v in kwargs.items() if k != "content"}


def _parse_date(s: str | None) -> float | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            continue
    raise ToolError(f"Dates must look like 2026-10-01 (got '{s}')")


def _fmt(e: dict, root: Root | None = None) -> dict:
    return {"location": root.name if root else e.get("root_id"), "path": e["path"], "name": e["name"],
            "is_folder": e["is_dir"], "size": e["size"],
            "modified": datetime.fromtimestamp(e["modified"]).isoformat(timespec="seconds")}


# ---------------------------------------------------------------- reading
@tool
def list_locations() -> list[dict]:
    """List the storage locations (approved folders on the home PC) the AI may access, with permissions."""
    p = _p()
    out = [{"location": r.name, "access": "read-write" if p.root_access(r) == "rw" else "read-only",
            "available": os.path.isdir(r.path)} for r in _roots(p)]
    _log("list_locations")
    return out


@tool
def list_files(location: str, path: str = "", limit: int = 500) -> dict:
    """List files and folders in a folder of a storage location. path is relative ('' = top level)."""
    p = _p()
    r = _root(p, location)
    items = fileops.list_dir(r, path)
    items.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    _log("list_files", f"{r.name}/{path}")
    return {"location": r.name, "path": normalize_rel(path), "total": len(items),
            "items": [_fmt(e, r) for e in items[:limit]]}


@tool
def search_files(query: str = "", location: str = "", extensions: list[str] | None = None, folder: str = "",
                 modified_after: str = "", modified_before: str = "", search_content: bool = True,
                 limit: int = 50) -> list[dict]:
    """Search approved files by name and indexed content.
    query: words to find in file names or document text (PDF, DOCX, XLSX, text). Empty = filter only.
    extensions: e.g. ["pdf","docx"]. folder: restrict to a sub-folder path. Dates: YYYY-MM-DD."""
    p = _p()
    roots = [_root(p, location)] if location else _roots(p)
    rows = index.search([r.id for r in roots], query, extensions, folder or None, _parse_date(modified_after),
                        _parse_date(modified_before), content=search_content, limit=min(limit, 200))
    names = {r.id: r for r in roots}
    out = []
    for e in rows:
        d = _fmt(e, names.get(e["root_id"]))
        d["match"] = e.get("match")
        if e.get("snippet"):
            d["snippet"] = e["snippet"]
        out.append(d)
    _log("search_files", query, details={"results": len(out), "location": location or "*"})
    return out


@tool
def get_file_info(location: str, path: str, include_checksum: bool = False) -> dict:
    """Metadata for one file or folder; optionally its SHA-256 checksum."""
    p = _p()
    r = _root(p, location)
    e = fileops.stat_path(r, path)
    d = _fmt(e, r) | {"type": e["kind"], "mime": e["mime"]}
    if include_checksum and not e["is_dir"]:
        d["sha256"] = fileops.checksum(resolve(r, path)[0])
    _log("get_file_info", f"{r.name}/{path}")
    return d


@tool
def read_document(location: str, path: str, offset: int = 0, max_chars: int = 50_000) -> dict:
    """Read the text of a document: plain text/code/CSV/JSON/Markdown, PDF, DOCX or XLSX.
    Use offset to page through long documents."""
    p = _p()
    r = _root(p, location)
    abs_path, norm = resolve(r, path, must_exist=True)
    if os.path.isdir(abs_path):
        raise ToolError("That is a folder - use list_files")
    cap = int(settings_store.get("ai_max_read_chars"))
    max_chars = max(1, min(max_chars, cap))
    text = extract.extract_text(abs_path, offset + max_chars)
    chunk = text[offset:offset + max_chars]
    _log("read_document", f"{r.name}/{norm}", details={"chars": len(chunk), "offset": offset})
    return {"location": r.name, "path": norm, "offset": offset, "chars": len(chunk),
            "has_more": len(text) >= offset + max_chars, "text": chunk}


@tool
def analyze_spreadsheet(location: str, path: str, sheet: str = "") -> dict:
    """Summarize a CSV/TSV/XLSX: row count, column types, numeric sums/min/max/mean and sample rows."""
    p = _p()
    r = _root(p, location)
    abs_path, norm = resolve(r, path, must_exist=True)
    res = extract.analyze_table(abs_path, sheet or None)
    _log("analyze_spreadsheet", f"{r.name}/{norm}")
    return res


@tool
def find_duplicates(location: str, folder: str = "", min_size_bytes: int = 1024) -> dict:
    """Find files with identical content (by SHA-256) in a location or sub-folder."""
    p = _p()
    r = _root(p, location)
    groups = tasks.find_duplicates([r.id], folder, min_size=min_size_bytes)
    _log("find_duplicates", f"{r.name}/{folder}", details={"groups": len(groups)})
    return {"groups": [{"size": g["size"], "files": [f["path"] for f in g["files"]]} for g in groups[:100]],
            "group_count": len(groups),
            "reclaimable_bytes": sum(g["size"] * (len(g["files"]) - 1) for g in groups)}


@tool
def propose_organization(location: str, folder: str = "", strategy: str = "type") -> dict:
    """Propose (but do not apply) a folder structure for loose files. strategy: 'type' or 'year_month'.
    Apply it with organize_files, which requires the owner's approval."""
    p = _p()
    r = _root(p, location)
    items = [e for e in fileops.list_dir(r, folder) if not e["is_dir"]]
    plan = []
    for e in items:
        if strategy == "year_month":
            d = datetime.fromtimestamp(e["modified"])
            target = f"{d:%Y}/{d:%m}"
        else:
            target = {"image": "Images", "video": "Videos", "audio": "Audio", "pdf": "PDFs", "document": "Documents",
                      "spreadsheet": "Spreadsheets", "presentation": "Presentations", "archive": "Archives",
                      "code": "Code", "text": "Text"}.get(e["kind"], "Other")
        dest = f"{normalize_rel(folder)}/{target}".strip("/")
        plan.append({"path": e["path"], "dest_folder": dest})
    summary = Counter(m["dest_folder"] for m in plan)
    _log("propose_organization", f"{r.name}/{folder}", details={"files": len(plan)})
    return {"location": r.name, "moves": plan[:1000], "summary": dict(summary)}


# ---------------------------------------------------------------- writing
def _approval_needed(kind: str, params: dict, summary: str) -> dict:
    p = _p()
    params["requested_by"] = p.label
    return approvals.request(kind, params, summary, p.label)


@tool
def create_folder(location: str, path: str) -> dict:
    """Create a folder (and missing parents). Allowed without approval in writable locations."""
    p = _p()
    r = _root(p, location, write=True)
    abs_path, norm = resolve(r, path)
    os.makedirs(abs_path, exist_ok=True)
    index.upsert(r, abs_path, norm)
    _log("create_folder", f"{r.name}/{norm}")
    return {"created": norm, "location": r.name}


@tool
def save_document(location: str, path: str, content: str, overwrite: bool = False) -> dict:
    """Save a new text document (e.g. a Markdown/CSV/TXT report) to a writable location.
    If the file exists and overwrite=true, an approval request is created instead (previous version is kept)."""
    p = _p()
    r = _root(p, location, write=True)
    abs_path, norm = resolve(r, path)
    if len(content) > 20 * 1024 * 1024:
        raise ToolError("Content too large (20 MB max)")
    if os.path.exists(abs_path):
        if not overwrite:
            raise ToolError("File already exists. Choose another name or set overwrite=true (requires approval).")
        return _approval_needed("overwrite", {"root_id": r.id, "path": norm,
                                              "content_b64": base64.b64encode(content.encode()).decode()},
                                f"Overwrite {r.name}/{norm} ({len(content)} chars)")
    e = fileops.write_bytes(r, norm, content.encode("utf-8"), overwrite=False)
    _log("save_document", f"{r.name}/{norm}", details={"chars": len(content)})
    return {"saved": e["path"], "location": r.name, "size": e["size"]}


@tool
def move_file(location: str, path: str, dest_folder: str, dest_location: str = "", new_name: str = "") -> dict:
    """Request moving a file/folder to another folder (needs owner approval)."""
    p = _p()
    r = _root(p, location, write=True)
    d = _root(p, dest_location or location, write=True)
    _, norm = resolve(r, path, must_exist=True)
    resolve(d, dest_folder)
    return _approval_needed("move", {"root_id": r.id, "path": norm, "dest_root_id": d.id,
                                     "dest_dir": normalize_rel(dest_folder), "new_name": new_name or None},
                            f"Move {r.name}/{norm} -> {d.name}/{normalize_rel(dest_folder)}")


@tool
def copy_file(location: str, path: str, dest_folder: str, dest_location: str = "") -> dict:
    """Request copying a file/folder (needs owner approval)."""
    p = _p()
    r = _root(p, location)
    d = _root(p, dest_location or location, write=True)
    _, norm = resolve(r, path, must_exist=True)
    return _approval_needed("copy", {"root_id": r.id, "path": norm, "dest_root_id": d.id,
                                     "dest_dir": normalize_rel(dest_folder)},
                            f"Copy {r.name}/{norm} -> {d.name}/{normalize_rel(dest_folder)}")


@tool
def rename_file(location: str, path: str, new_name: str) -> dict:
    """Request renaming a file/folder (needs owner approval)."""
    p = _p()
    r = _root(p, location, write=True)
    _, norm = resolve(r, path, must_exist=True)
    fileops.validate_name(new_name)
    return _approval_needed("rename", {"root_id": r.id, "path": norm, "new_name": new_name},
                            f"Rename {r.name}/{norm} -> {new_name}")


@tool
def delete_file(location: str, path: str) -> dict:
    """Request deleting a file/folder to the recycle bin (needs owner approval)."""
    p = _p()
    r = _root(p, location, write=True)
    _, norm = resolve(r, path, must_exist=True)
    return _approval_needed("delete", {"root_id": r.id, "path": norm}, f"Delete {r.name}/{norm} (to recycle bin)")


@tool
def organize_files(location: str, moves: list[dict]) -> dict:
    """Request a bulk reorganization. moves: [{"path": "...", "dest_folder": "..."}] (needs owner approval)."""
    p = _p()
    r = _root(p, location, write=True)
    if not moves or len(moves) > 5000:
        raise ToolError("Provide between 1 and 5000 moves")
    clean = []
    for m in moves:
        _, norm = resolve(r, m["path"], must_exist=True)
        resolve(r, m["dest_folder"])
        clean.append({"root_id": r.id, "path": norm, "dest_root_id": r.id, "dest_dir": normalize_rel(m["dest_folder"])})
    folders = sorted({m["dest_dir"] for m in clean})
    return _approval_needed("bulk_move", {"moves": clean},
                            f"Reorganize {len(clean)} files in {r.name} into {len(folders)} folders: "
                            + ", ".join(folders[:8]) + ("..." if len(folders) > 8 else ""))


@tool
def get_approval_status(approval_id: int) -> dict:
    """Check whether the owner approved, rejected or executed a requested action."""
    p = _p()
    row = db.one("SELECT * FROM approvals WHERE id=? AND requested_by=?", (approval_id, p.label))
    if row is None:
        raise ToolError("Approval request not found")
    d = approvals.to_dict(row)
    return {"id": d["id"], "status": d["status"], "summary": d["summary"], "result": d["result"]}


# ------------------------------------------------------------- processing
AI_JOBS = {"inventory": True, "find_duplicates": False, "checksums": False, "compress": True,
           "batch_extract_text": True, "convert": True, "search_dir": False}


@tool
def run_task(task: str, location: str, params: dict | None = None) -> dict:
    """Start an approved background task on the home PC. task: inventory | find_duplicates | checksums |
    compress (params: paths[], dest_dir, archive_name) | batch_extract_text (path, dest_dir) |
    convert (path, target e.g. csv/xlsx/txt) | search_dir (path, pattern, contains). Returns a job id."""
    p = _p()
    if task not in AI_JOBS:
        raise ToolError(f"Task '{task}' is not available to AI. Options: {', '.join(AI_JOBS)}")
    r = _root(p, location, write=AI_JOBS[task])
    params = dict(params or {})
    params["root_id"] = r.id
    job_id = get_manager().submit(task, params, created_by=p.label, source="ai")
    _log("run_task", task, details={"job_id": job_id, "location": r.name})
    return {"job_id": job_id, "status": "queued"}


@tool
def list_scripts() -> list[dict]:
    """List scripts the owner has approved for AI use."""
    _p()
    return [{"id": r["id"], "name": r["name"]} for r in db.all_("SELECT * FROM apps WHERE kind='script' AND allow_ai=1")]


@tool
def run_script(script_id: int) -> dict:
    """Request running an owner-approved script (always requires approval)."""
    row = db.one("SELECT * FROM apps WHERE id=? AND kind='script' AND allow_ai=1", (script_id,))
    if row is None:
        raise ToolError("Script not found or not approved for AI")
    return _approval_needed("run_script", {"app_id": script_id}, f"Run script '{row['name']}'")


@tool
def get_job_status(job_id: int) -> dict:
    """Get progress and result of a background task."""
    p = _p()
    row = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if row is None or not row["created_by"].startswith(p.username):
        raise ToolError("Job not found")
    j = job_dict(row)
    return {k: j[k] for k in ("id", "type", "status", "progress", "message", "result")}


@tool
def local_model_summarize(location: str, path: str, instructions: str = "") -> dict:
    """Summarize a document with the owner's LOCAL model (Ollama) so its content never leaves the PC.
    Only works when the owner enabled Local AI."""
    p = _p()
    r = _root(p, location)
    abs_path, norm = resolve(r, path, must_exist=True)
    text = extract.extract_text(abs_path, 200_000)
    try:
        summary = local_ai.summarize(text, instructions)
    except RuntimeError as e:
        raise ToolError(str(e))
    _log("local_model_summarize", f"{r.name}/{norm}")
    return {"summary": summary}


@tool
def storage_overview() -> dict:
    """Totals per location: file counts, size, largest file types and recently modified files."""
    p = _p()
    roots = _roots(p)
    st = index.stats([r.id for r in roots])
    names = {r.id: r.name for r in roots}
    return {"files": st["files"], "folders": st["folders"],
            "by_location": [{"location": names.get(b["root_id"]), "size": b["size"], "files": b["files"]}
                            for b in st["by_root"]],
            "by_type": st["by_type"][:10],
            "recent": [{"location": names.get(x["root_id"]), "path": x["path"],
                        "modified": datetime.fromtimestamp(x["modified"]).isoformat(timespec="seconds")}
                       for x in st["recent"][:10]]}


# ------------------------------------------------------------- ASGI glue
class MCPEndpoint:
    """ASGI app for /mcp: bearer-token authentication, then the MCP streamable HTTP handler."""

    def __init__(self):
        from mcp.server.fastmcp.server import StreamableHTTPASGIApp  # type: ignore
        mcp._session_manager = None  # a session manager can only run once; one per app instance
        mcp.streamable_http_app()  # initializes the session manager
        self.inner = StreamableHTTPASGIApp(mcp.session_manager)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        ip = (scope.get("client") or ("unknown",))[0]
        authz = headers.get("authorization", "")
        p = None
        if authz.lower().startswith("bearer "):
            import anyio
            p = await anyio.to_thread.run_sync(lambda: auth.token_principal(authz[7:].strip(), ip, kinds={"mcp"}))
        if p is None or not settings_store.get("mcp_enabled"):
            from . import security
            security.limiter.hit(f"mcp-fail:{ip}", 1000, 60)
            body = b'{"error":"unauthorized","message":"Provide an AI (MCP) token: Authorization: Bearer <token>"}'
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")]})
            await send({"type": "http.response.body", "body": body})
            return
        token = current_principal.set(p)
        try:
            await self.inner(scope, receive, send)
        finally:
            current_principal.reset(token)


def run_stdio(token: str) -> None:
    p = auth.token_principal(token, "stdio", kinds={"mcp"})
    if p is None:
        raise SystemExit("Invalid or revoked MCP token (PCLOUD_MCP_TOKEN)")
    current_principal.set(p)
    mcp.run("stdio")
