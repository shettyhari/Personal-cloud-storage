import asyncio
import json
import os

import pytest

from pcloud.cloudlink import config as linkcfg
from pcloud.cloudlink import tunnel as tunnel_mod
from pcloud.cloudlink.tunnel import Tunnel, is_local_only


class FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    async def upload(self, path, data):
        self.objects[path] = bytes(data)

    async def download(self, path):
        return self.objects[path]

    async def signed_url(self, path, expires):
        return f"https://relay.test/{path}?ttl={expires}"

    async def remove(self, paths):
        for p in paths:
            self.objects.pop(p, None)

    async def list(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]


def reassemble(parts):
    assert parts == sorted(parts, key=lambda p: p["part"])
    return parts[0]["status"], json.loads("".join(p["chunk"] for p in parts))


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def tun(admin, env):
    from pcloud import db
    uid = db.one("SELECT id FROM users WHERE username='owner'")["id"]
    return Tunnel(env["app"], uid, "srv-1", FakeStorage())


def call(tun, method, path, body=None):
    return reassemble(run(tun.handle({"id": "r1", "method": method, "path": path, "body": body})))


def test_tunnel_reaches_api_as_linked_user(tun, admin, env):
    (env["storage"] / "hello.txt").write_text("hi")
    status, body = call(tun, "GET", f"/api/files/list?root_id={admin.root_id}")
    assert status == 200 and [i["name"] for i in body["items"]] == ["hello.txt"]
    status, body = call(tun, "POST", "/api/files/mkdir", {"root_id": admin.root_id, "path": "", "name": "Remote"})
    assert status == 200 and (env["storage"] / "Remote").is_dir()
    acts = admin.get("/api/activity").json()
    assert any(a["action"] == "file.mkdir" and a["actor"] == "owner/cloud" for a in acts)


def test_tunnel_keeps_path_safety(tun, admin):
    status, _ = call(tun, "GET", f"/api/files/download?root_id={admin.root_id}&path=../Outside/secret.txt")
    assert status in (400, 403)


def test_local_only_endpoints_refused(tun):
    for method, path in [("GET", "/api/fs/browse"), ("POST", "/api/roots"), ("POST", "/api/system/shutdown"),
                         ("POST", "/api/users"), ("PATCH", "/api/settings"), ("POST", "/api/cloud/unlink"),
                         ("POST", "/api/apps")]:
        status, _ = call(tun, method, path, {})
        assert status == 403, path
    assert is_local_only("GET", "/api/fs/browse?path=C:\\")
    assert not is_local_only("PATCH", "/api/roots/1")


def test_forged_tunnel_header_rejected(env, admin):
    from fastapi.testclient import TestClient
    c = TestClient(env["app"], client=("127.0.0.1", 1))
    r = c.get("/api/roots", headers={"x-pcloud-tunnel": "guess", "x-pcloud-tunnel-user": "1"})
    assert r.status_code == 401


def test_large_reply_is_chunked(tun, admin, env):
    for i in range(400):
        (env["storage"] / f"file-with-a-long-name-{i:04d}.txt").write_text("x")
    parts = run(tun.handle({"id": "r2", "method": "GET", "path": f"/api/files/list?root_id={admin.root_id}"}))
    assert len(parts) > 1 and all(len(p["chunk"]) <= tunnel_mod.CHUNK for p in parts)
    status, body = reassemble(parts)
    assert status == 200 and len(body["items"]) == 400


def test_download_is_staged_in_parts(tun, admin, env, monkeypatch):
    monkeypatch.setattr(tunnel_mod, "PART_SIZE", 1000)
    data = os.urandom(2500)
    (env["storage"] / "big.bin").write_bytes(data)
    status, body = call(tun, "POST", "/cloud/transfer/download", {"root_id": admin.root_id, "path": "big.bin"})
    assert status == 200, body
    assert [p["size"] for p in body["parts"]] == [1000, 1000, 500]
    staged = b"".join(tun.storage.objects[k] for k in sorted(tun.storage.objects))
    assert staged == data


def test_upload_through_relay(tun, admin, env, monkeypatch):
    monkeypatch.setattr(tunnel_mod, "PART_SIZE", 1000)
    data = os.urandom(2100)
    status, slot = call(tun, "POST", "/cloud/transfer/begin",
                        {"root_id": admin.root_id, "dir": "", "filename": "up.bin", "size": len(data)})
    assert status == 200 and slot["parts"] == 3
    # resuming the same upload returns the same slot
    _, again = call(tun, "POST", "/cloud/transfer/begin",
                    {"root_id": admin.root_id, "dir": "", "filename": "up.bin", "size": len(data)})
    assert again["transfer_id"] == slot["transfer_id"] and again["resumed"]
    for i in range(3):  # what the browser does
        run(tun.storage.upload(f"{slot['prefix']}/part-{i:05d}", data[i * 1000:(i + 1) * 1000]))
    status, entry = call(tun, "POST", "/cloud/transfer/commit", {"transfer_id": slot["transfer_id"]})
    assert status == 200, entry
    assert (env["storage"] / "up.bin").read_bytes() == data
    assert not tun.storage.objects  # relay cleaned up


def test_upload_rejects_traversal_and_incomplete(tun, admin, monkeypatch):
    status, _ = call(tun, "POST", "/cloud/transfer/begin",
                     {"root_id": admin.root_id, "dir": "../..", "filename": "x", "size": 1})
    assert status == 400
    monkeypatch.setattr(tunnel_mod, "PART_SIZE", 1000)
    _, slot = call(tun, "POST", "/cloud/transfer/begin", {"root_id": admin.root_id, "filename": "y.bin", "size": 1500})
    run(tun.storage.upload(f"{slot['prefix']}/part-00000", b"a" * 1000))
    run(tun.storage.upload(f"{slot['prefix']}/part-00001", b"b" * 100))
    status, body = call(tun, "POST", "/cloud/transfer/commit", {"transfer_id": slot["transfer_id"]})
    assert status == 400 and "incomplete" in body["detail"]


def test_link_code_roundtrip_and_credentials_protected(env):
    code = linkcfg.make_link_code("https://abc.supabase.co", "sb_publishable_x", "otp123")
    assert linkcfg.parse_link_code(code) == {"supabase_url": "https://abc.supabase.co",
                                              "anon_key": "sb_publishable_x", "code": "otp123"}
    with pytest.raises(ValueError):
        linkcfg.parse_link_code(linkcfg.make_link_code("http://evil", "k", "c"))
    linkcfg.save(linkcfg.LinkConfig("https://abc.supabase.co", "k", "srv", "e@x", "secret-pw", 1, "PC"))
    raw = (env["data"] / "secrets" / "cloud_link.bin").read_bytes()
    assert b"secret-pw" not in raw or not raw.startswith(b"DPAPI:")
    assert linkcfg.load().password == "secret-pw"
    linkcfg.clear()
    assert linkcfg.load() is None
