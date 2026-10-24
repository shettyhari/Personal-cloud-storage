"""Human-in-the-loop approval queue for destructive or bulk AI actions."""
from __future__ import annotations

import base64

from .. import audit, db, settings_store
from ..paths import get_root
from . import fileops
from .jobs import get_manager

KINDS = {"move", "rename", "copy", "delete", "overwrite", "bulk_move", "run_script"}


def request(kind: str, params: dict, summary: str, requested_by: str) -> dict:
    if kind not in KINDS:
        raise ValueError("unknown approval kind")
    cur = db.execute("INSERT INTO approvals(kind, params, summary, requested_by, created_at) VALUES(?,?,?,?,?)",
                     (kind, db.dumps(params), summary, requested_by, db.now()))
    audit.record("ai", requested_by, f"approval.request.{kind}", summary, "pending", details={"approval_id": cur.lastrowid})
    return {"approval_id": cur.lastrowid, "status": "pending",
            "message": "This action needs the owner's approval. They can approve it in Personal Cloud OS > "
                       "AI Workspace > Pending approvals. Check back with get_approval_status."}


def expire_old() -> None:
    hours = float(settings_store.get("ai_approval_expiry_hours"))
    db.execute("UPDATE approvals SET status='expired' WHERE status='pending' AND created_at < ?",
               (db.now() - hours * 3600,))


def _require_ai_write(root_id: int):
    root = get_root(root_id)
    if not (root.ai_read and root.ai_write) or root.read_only:
        raise PermissionError(f"AI write access to '{root.name}' has been revoked")
    return root


def _execute(kind: str, p: dict) -> dict:
    if kind == "delete":
        root = _require_ai_write(p["root_id"])
        return fileops.delete(root, p["path"], actor="ai")
    if kind == "rename":
        root = _require_ai_write(p["root_id"])
        return fileops.rename(root, p["path"], p["new_name"])
    if kind in ("move", "copy"):
        src = _require_ai_write(p["root_id"]) if kind == "move" else get_root(p["root_id"])
        dst = _require_ai_write(p["dest_root_id"])
        return fileops.transfer(src, p["path"], dst, p["dest_dir"], copy=kind == "copy",
                                on_conflict="rename", new_name=p.get("new_name"))
    if kind == "bulk_move":
        results = []
        for m in p["moves"]:
            src, dst = _require_ai_write(m["root_id"]), _require_ai_write(m["dest_root_id"])
            fileops.make_dirs(dst, m["dest_dir"])
            try:
                results.append({"path": m["path"], "ok": True,
                                "result": fileops.transfer(src, m["path"], dst, m["dest_dir"], copy=False)["path"]})
            except Exception as e:
                results.append({"path": m["path"], "ok": False, "error": str(e)})
        return {"moved": sum(r["ok"] for r in results), "results": results}
    if kind == "overwrite":
        root = _require_ai_write(p["root_id"])
        data = base64.b64decode(p["content_b64"])
        return fileops.write_bytes(root, p["path"], data, overwrite=True, reason="AI overwrite (approved)")
    if kind == "run_script":
        job_id = get_manager().submit("run_script", {"app_id": p["app_id"]}, created_by=p.get("requested_by", "ai"),
                                      source="ai")
        return {"job_id": job_id}
    raise ValueError("unknown kind")


def decide(approval_id: int, approve: bool, decided_by: str) -> dict:
    row = db.one("SELECT * FROM approvals WHERE id=?", (approval_id,))
    if row is None:
        raise LookupError("Approval request not found")
    if row["status"] != "pending":
        raise ValueError(f"Request is already {row['status']}")
    if not approve:
        db.execute("UPDATE approvals SET status='rejected', decided_at=?, decided_by=? WHERE id=?",
                   (db.now(), decided_by, approval_id))
        audit.record("user", decided_by, f"approval.reject.{row['kind']}", row["summary"])
        return {"status": "rejected"}
    db.execute("UPDATE approvals SET status='approved', decided_at=?, decided_by=? WHERE id=?",
               (db.now(), decided_by, approval_id))
    try:
        result = _execute(row["kind"], db.loads(row["params"], {}))
        db.execute("UPDATE approvals SET status='executed', result=? WHERE id=?", (db.dumps(result), approval_id))
        audit.record("ai", row["requested_by"], f"ai.{row['kind']}", row["summary"], "ok",
                     details={"approval_id": approval_id, "approved_by": decided_by})
        return {"status": "executed", "result": result}
    except Exception as e:
        db.execute("UPDATE approvals SET status='failed', result=? WHERE id=?", (db.dumps({"error": str(e)}), approval_id))
        audit.record("ai", row["requested_by"], f"ai.{row['kind']}", row["summary"], "error", details={"error": str(e)})
        return {"status": "failed", "error": str(e)}


def to_dict(r) -> dict:
    d = dict(r)
    params = db.loads(d.pop("params"), {})
    params.pop("content_b64", None)
    d["params"] = params
    d["result"] = db.loads(d["result"], None)
    return d
