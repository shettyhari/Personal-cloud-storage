"""Request tunnel: cloud dashboard → this agent's REST API, in-process.

Wire protocol on the private Realtime channel `server:<id>`:
  browser → agent  event "req": {"id", "method", "path", "body"}          path includes the query string
  agent → browser  event "res": {"id", "status", "part", "parts", "chunk"} JSON body split into ≤ CHUNK chars

`/api/...` paths go to the normal FastAPI routes with the linked local account, so every existing
permission check, path-safety rule and audit entry applies. `/cloud/...` paths are file transfers that
stage data through the private `relay` storage bucket in PART_SIZE pieces:
  POST /cloud/transfer/download {root_id, path}        → parts staged, signed URLs returned
  POST /cloud/transfer/zip      {items, name}          → ZIP built, then staged like a download
  POST /cloud/transfer/begin    {root_id, dir, filename, size, overwrite} → upload slot (browser uploads parts)
  POST /cloud/transfer/commit   {transfer_id}          → agent pulls the parts and places the file
  GET  /cloud/ping                                      → agent identity and version
A short list of powerful endpoints is refused through the tunnel (LOCAL_ONLY): they work only on the PC.
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import re
import time
import uuid
from typing import Any, Protocol

import anyio
import httpx

from .. import __version__, audit, auth
from ..paths import NotFound, PathError, normalize_rel, resolve, validate_name
from ..services import fileops

log = logging.getLogger("pcloud.cloud")
CHUNK = 60_000
PART_SIZE = 40 * 1024 * 1024          # Supabase free tier caps a single object at 50 MB
SIGNED_URL_TTL = 600
STAGED_TTL = 900

LOCAL_ONLY = [
    ("*", re.compile(r"^/api/fs/browse")),
    ("POST", re.compile(r"^/api/roots/?$")),
    ("DELETE", re.compile(r"^/api/roots/")),
    ("POST", re.compile(r"^/api/apps/?$")),
    ("*", re.compile(r"^/api/system/shutdown")),
    ("*", re.compile(r"^/api/users")),
    ("*", re.compile(r"^/api/setup/")),
    ("*", re.compile(r"^/api/auth/(login|mfa|logout|password|2fa)")),
    ("*", re.compile(r"^/api/mobile/pair")),
    ("PATCH", re.compile(r"^/api/settings")),
    ("*", re.compile(r"^/api/cloud/")),
]


class Storage(Protocol):
    async def upload(self, path: str, data: bytes) -> None: ...
    async def download(self, path: str) -> bytes: ...
    async def signed_url(self, path: str, expires: int) -> str: ...
    async def remove(self, paths: list[str]) -> None: ...
    async def list(self, prefix: str) -> list[str]: ...


def is_local_only(method: str, path: str) -> bool:
    p = path.split("?", 1)[0]
    return any((m == "*" or m == method.upper()) and rx.search(p) for m, rx in LOCAL_ONLY)


def split_reply(req_id: str, status: int, body: Any) -> list[dict]:
    text = json.dumps(body, separators=(",", ":"), default=str)
    chunks = [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)] or [""]
    return [{"id": req_id, "status": status, "part": i, "parts": len(chunks), "chunk": c} for i, c in enumerate(chunks)]


class Tunnel:
    def __init__(self, app, user_id: int, server_id: str, storage: Storage):
        self.app = app
        self.user_id = user_id
        self.server_id = server_id
        self.storage = storage
        self.uploads: dict[str, dict] = {}

    # ------------------------------------------------------------- dispatch
    async def handle(self, msg: dict) -> list[dict]:
        req_id = str(msg.get("id") or "")
        method = str(msg.get("method") or "GET").upper()
        path = str(msg.get("path") or "")
        body = msg.get("body")
        if not req_id or not path.startswith(("/api/", "/cloud/")):
            return split_reply(req_id, 400, {"detail": "Bad request"})
        if is_local_only(method, path):
            return split_reply(req_id, 403, {"detail": "For safety this action is only available on the server itself."})
        try:
            if path.startswith("/cloud/"):
                status, payload = await self._cloud(method, path, body or {})
            else:
                status, payload = await self._api(method, path, body)
        except (PathError, ValueError) as e:
            status, payload = 400, {"detail": str(e)}
        except NotFound as e:
            status, payload = 404, {"detail": str(e)}
        except PermissionError as e:
            status, payload = 403, {"detail": str(e)}
        except Exception as e:  # never let one request kill the link
            log.exception("tunnel request failed: %s %s", method, path)
            status, payload = 500, {"detail": f"Server error: {type(e).__name__}"}
        return split_reply(req_id, status, payload)

    async def _api(self, method: str, path: str, body: Any) -> tuple[int, Any]:
        transport = httpx.ASGITransport(app=self.app, client=("127.0.0.1", 0))
        headers = auth.tunnel_headers(self.user_id) | {"content-type": "application/json"}
        async with httpx.AsyncClient(transport=transport, base_url="http://tunnel.local") as c:
            r = await c.request(method, path, headers=headers,
                                content=None if body is None else json.dumps(body))
        ctype = r.headers.get("content-type", "")
        if "json" in ctype:
            payload: Any = r.json() if r.content else None
        else:
            payload = r.text
            if len(payload) > 2_000_000:
                payload = payload[:2_000_000]
        return r.status_code, payload

    # ------------------------------------------------------------ transfers
    def _principal(self):
        p = auth.tunnel_principal(self.user_id)
        if p is None:
            raise PermissionError("The linked account no longer exists")
        return p

    async def _cloud(self, method: str, path: str, body: dict) -> tuple[int, Any]:
        route = path.split("?", 1)[0]
        if route == "/cloud/ping":
            return 200, {"server_id": self.server_id, "version": __version__}
        if method != "POST":
            return 405, {"detail": "Method not allowed"}
        if route == "/cloud/transfer/download":
            return 200, await self._stage_download(body)
        if route == "/cloud/transfer/zip":
            return 200, await self._stage_zip(body)
        if route == "/cloud/transfer/begin":
            return 200, self._begin_upload(body)
        if route == "/cloud/transfer/commit":
            return 200, await self._commit_upload(body)
        return 404, {"detail": "Unknown transfer action"}

    async def _stage_file(self, abs_path: str, name: str, cleanup: str | None = None) -> dict:
        tid = uuid.uuid4().hex
        prefix = f"{self.server_id}/{tid}"
        size = os.path.getsize(abs_path)
        parts = []
        with open(abs_path, "rb") as f:
            i = 0
            while True:
                data = await anyio.to_thread.run_sync(f.read, PART_SIZE)
                if not data and i > 0:
                    break
                key = f"{prefix}/part-{i:05d}"
                await self.storage.upload(key, data)
                parts.append({"url": await self.storage.signed_url(key, SIGNED_URL_TTL), "size": len(data)})
                i += 1
                if len(data) < PART_SIZE:
                    break
        if cleanup:
            try:
                os.remove(cleanup)
            except OSError:
                pass
        asyncio.get_running_loop().call_later(STAGED_TTL, lambda: asyncio.ensure_future(self._purge(prefix, len(parts))))
        return {"transfer_id": tid, "name": name, "size": size,
                "mime": mimetypes.guess_type(name)[0] or "application/octet-stream", "parts": parts,
                "expires_in": SIGNED_URL_TTL}

    async def _purge(self, prefix: str, n: int) -> None:
        try:
            await self.storage.remove([f"{prefix}/part-{i:05d}" for i in range(n)])
        except Exception:
            log.debug("relay purge failed for %s", prefix)

    async def _stage_download(self, body: dict) -> dict:
        p = self._principal()
        root = p.require_root(int(body["root_id"]))
        abs_path, norm = resolve(root, body.get("path"), must_exist=True)
        if os.path.isdir(abs_path):
            raise ValueError("Folders are downloaded as ZIP")
        audit.from_principal(p, "file.download", f"{root.name}/{norm}", details={"via": "cloud"})
        return await self._stage_file(abs_path, os.path.basename(abs_path))

    async def _stage_zip(self, body: dict) -> dict:
        status, res = await self._api("POST", "/api/files/zip", {"items": body.get("items", []),
                                                                "name": body.get("name") or "download.zip"})
        if status != 200:
            raise ValueError(res.get("detail") if isinstance(res, dict) else "Could not build ZIP")
        from ..routers import files as files_router
        entry = files_router._zip_tickets.pop(res["ticket"], None)
        if entry is None:
            raise ValueError("ZIP expired")
        _, tmp, name, _ = entry
        return await self._stage_file(tmp, name, cleanup=tmp)

    def _begin_upload(self, body: dict) -> dict:
        p = self._principal()
        if p.read_only or p.role == "readonly":
            raise PermissionError("Read-only account")
        root = p.require_root(int(body["root_id"]), write=True)
        name = validate_name(str(body["filename"]))
        rel_dir = normalize_rel(body.get("dir"))
        resolve(root, rel_dir)
        size = int(body["size"])
        if size < 0:
            raise ValueError("Invalid size")
        # Resume: same target and size as an open slot → reuse it (the browser skips parts already in relay).
        for tid, u in self.uploads.items():
            if (u["root_id"], u["dir"], u["name"], u["size"]) == (root.id, rel_dir, name, size):
                u["touched"] = time.time()
                return {"transfer_id": tid, "prefix": u["prefix"], "part_size": PART_SIZE,
                        "parts": u["parts"], "resumed": True}
        tid = uuid.uuid4().hex
        parts = max(1, -(-size // PART_SIZE))
        self.uploads[tid] = {"root_id": root.id, "dir": rel_dir, "name": name, "size": size,
                             "overwrite": bool(body.get("overwrite")), "prefix": f"{self.server_id}/{tid}",
                             "parts": parts, "touched": time.time()}
        return {"transfer_id": tid, "prefix": f"{self.server_id}/{tid}", "part_size": PART_SIZE, "parts": parts,
                "resumed": False}

    async def _commit_upload(self, body: dict) -> dict:
        tid = str(body.get("transfer_id") or "")
        u = self.uploads.get(tid)
        if u is None:
            raise ValueError("Upload slot expired - start the upload again")
        p = self._principal()
        root = p.require_root(u["root_id"], write=True)
        tmp = str(fileops.safe_tmp_path(".part"))
        written = 0
        keys = [f"{u['prefix']}/part-{i:05d}" for i in range(u["parts"])]
        try:
            with open(tmp, "wb") as out:
                for key in keys:
                    data = await self.storage.download(key)
                    await anyio.to_thread.run_sync(out.write, data)
                    written += len(data)
            if written != u["size"]:
                raise ValueError(f"Upload incomplete: received {written} of {u['size']} bytes")
            entry = await anyio.to_thread.run_sync(lambda: fileops.place_file(root, tmp, u["dir"], u["name"],
                                                                              u["overwrite"]))
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        self.uploads.pop(tid, None)
        await self._purge(u["prefix"], u["parts"])
        audit.from_principal(p, "file.upload", f"{root.name}/{entry['path']}", details={"size": written, "via": "cloud"})
        return entry
