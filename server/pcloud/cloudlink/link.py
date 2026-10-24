"""Outbound connection to the hosted dashboard: sign-in, heartbeat, request channel, auto-reconnect."""
from __future__ import annotations

import asyncio
import logging
import platform
import socket
import sys
import threading
import time

import httpx

from .. import __version__, audit
from ..paths import list_roots
from . import config as linkcfg
from .tunnel import Tunnel

log = logging.getLogger("pcloud.cloud")
HEARTBEAT_S = 30


def server_kind() -> str:
    return "windows_pc" if sys.platform == "win32" else "linux_vps"


def enroll(link_code: str, name: str, linked_user_id: int) -> linkcfg.LinkConfig:
    """Exchange a one-time link code for this server's own cloud credentials (stored DPAPI-protected)."""
    info = linkcfg.parse_link_code(link_code)
    r = httpx.post(f"{info['supabase_url']}/functions/v1/enroll-server",
                   headers={"apikey": info["anon_key"], "Authorization": f"Bearer {info['anon_key']}"},
                   json={"code": info["code"], "name": name, "kind": server_kind(), "hostname": socket.gethostname(),
                         "os": f"{platform.system()} {platform.release()}", "version": __version__}, timeout=30)
    if r.status_code != 200:
        try:
            detail = r.json().get("error") or r.text
        except ValueError:
            detail = r.text
        raise ValueError(f"Linking failed: {detail}")
    data = r.json()
    cfg = linkcfg.LinkConfig(supabase_url=info["supabase_url"], anon_key=info["anon_key"],
                             server_id=data["server_id"], email=data["email"], password=data["password"],
                             linked_user_id=linked_user_id, name=name)
    linkcfg.save(cfg)
    audit.record("system", "cloud", "cloud.link", name, details={"server_id": cfg.server_id})
    return cfg


class SupabaseStorage:
    def __init__(self, client):
        self.bucket = client.storage.from_("relay")

    async def upload(self, path: str, data: bytes) -> None:
        await self.bucket.upload(path, data, {"content-type": "application/octet-stream", "upsert": "true"})

    async def download(self, path: str) -> bytes:
        return await self.bucket.download(path)

    async def signed_url(self, path: str, expires: int) -> str:
        res = await self.bucket.create_signed_url(path, expires)
        return res.get("signedURL") or res.get("signedUrl") or res["signed_url"]

    async def remove(self, paths: list[str]) -> None:
        if paths:
            await self.bucket.remove(paths)

    async def list(self, prefix: str) -> list[str]:
        return [f"{prefix}/{o['name']}" for o in await self.bucket.list(prefix)]


class CloudLink:
    def __init__(self):
        self.app = None
        self.thread: threading.Thread | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.stop_event = threading.Event()
        self.state = {"status": "not_linked", "error": None, "since": None, "requests": 0}

    def attach(self, app) -> None:
        self.app = app

    def status(self) -> dict:
        cfg = linkcfg.load()
        return self.state | {"linked": cfg is not None, "server_id": cfg.server_id if cfg else None,
                             "name": cfg.name if cfg else None,
                             "dashboard": cfg.supabase_url if cfg else None}

    def start(self) -> None:
        if linkcfg.load() is None or (self.thread and self.thread.is_alive()):
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="cloudlink", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        backoff = 2
        while not self.stop_event.is_set():
            cfg = linkcfg.load()
            if cfg is None:
                self.state.update(status="not_linked")
                return
            started = time.time()
            try:
                self.loop.run_until_complete(self._session(cfg))
            except Exception as e:
                log.warning("cloud link dropped: %s", e)
                self.state.update(status="reconnecting", error=str(e)[:300])
            if time.time() - started > 120:
                backoff = 2
            if self.stop_event.wait(backoff):
                break
            backoff = min(backoff * 2, 120)
        self.state.update(status="stopped")

    async def _session(self, cfg: linkcfg.LinkConfig) -> None:
        from supabase import acreate_client
        self.state.update(status="connecting", error=None)
        client = await acreate_client(cfg.supabase_url, cfg.anon_key)
        session = await client.auth.sign_in_with_password({"email": cfg.email, "password": cfg.password})
        await client.realtime.set_auth(session.session.access_token)
        tunnel = Tunnel(self.app, cfg.linked_user_id, cfg.server_id, SupabaseStorage(client))
        channel = client.channel(f"server:{cfg.server_id}", {"config": {"private": True,
                                                                        "broadcast": {"self": False, "ack": False}}})
        joined = asyncio.Event()
        failed: list[str] = []

        async def respond(payload: dict) -> None:
            self.state["requests"] += 1
            for part in await tunnel.handle(payload):
                await channel.send_broadcast("res", part)

        def on_req(message) -> None:
            payload = message.get("payload") if isinstance(message, dict) else None
            if isinstance(payload, dict):
                asyncio.ensure_future(respond(payload))

        def on_state(state, err=None) -> None:
            name = str(getattr(state, "value", state))
            if name.upper().endswith("SUBSCRIBED"):
                joined.set()
            elif "ERROR" in name.upper() or "CLOSED" in name.upper() or "TIMED" in name.upper():
                failed.append(f"{name}: {err}")
                joined.set()

        channel.on_broadcast("req", on_req)
        await channel.subscribe(on_state)
        await asyncio.wait_for(joined.wait(), 20)
        if failed:
            raise RuntimeError(f"Could not join the server channel ({failed[0]}). Is this server still registered?")
        self.state.update(status="online", since=time.time(), error=None)
        log.info("cloud link online as server %s", cfg.server_id)
        last_janitor = 0.0
        try:
            while not self.stop_event.is_set():
                await self._heartbeat(client)
                if time.time() - last_janitor > 3600:
                    last_janitor = time.time()
                    await self._janitor(client, cfg.server_id)
                for _ in range(HEARTBEAT_S):
                    if self.stop_event.is_set() or channel.is_closed or channel.is_errored:
                        break
                    await asyncio.sleep(1)
                if channel.is_closed or channel.is_errored:
                    raise RuntimeError("channel closed")
                if linkcfg.load() is None:
                    break
        finally:
            try:
                await client.rpc("agent_heartbeat", {"p_status": "offline", "p_metrics": {}, "p_roots": [],
                                                     "p_version": __version__}).execute()
            except Exception:
                pass
            try:
                await client.remove_all_channels()
            except Exception:
                pass

    async def _janitor(self, client, server_id: str) -> None:
        """Delete relay transfers older than an hour (abandoned uploads, expired downloads)."""
        from datetime import datetime, timedelta, timezone
        bucket = client.storage.from_("relay")
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        try:
            for folder in await bucket.list(server_id):
                prefix = f"{server_id}/{folder['name']}"
                objs = await bucket.list(prefix)
                old = [f"{prefix}/{o['name']}" for o in objs
                       if o.get("created_at") and datetime.fromisoformat(o["created_at"].replace("Z", "+00:00")) < cutoff]
                if old:
                    await bucket.remove(old)
        except Exception as e:
            log.debug("relay janitor: %s", e)

    async def _heartbeat(self, client) -> None:
        from ..services.monitor import monitor
        snap = monitor.snapshot()
        metrics = {"cpu": snap["cpu"]["percent"], "mem": snap["memory"]["percent"],
                   "mem_total": snap["memory"]["total"], "up_bps": snap["network"]["up_bps"],
                   "down_bps": snap["network"]["down_bps"], "boot_time": snap["boot_time"],
                   "disks": [{"mount": d["mount"], "total": d["total"], "free": d["free"]} for d in snap["disks"]],
                   "hostname": snap["hostname"], "os": f"{platform.system()} {platform.release()}"}
        roots = [{"id": r.id, "name": r.name} for r in list_roots()]
        await client.rpc("agent_heartbeat", {"p_status": "online", "p_metrics": metrics, "p_roots": roots,
                                             "p_version": __version__}).execute()


cloud_link = CloudLink()
