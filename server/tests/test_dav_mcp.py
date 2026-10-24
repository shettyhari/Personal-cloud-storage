import base64
import json

from fastapi.testclient import TestClient


def dav_client(env, token, user="owner"):
    c = TestClient(env["app"], client=("100.100.1.2", 4000))  # a Tailscale address
    c.headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:{token}".encode()).decode()
    return c


def test_webdav_full_cycle(admin, env):
    tok = admin.post("/api/tokens", {"name": "Android", "kind": "webdav"}).json()["token"]
    c = dav_client(env, tok)
    r = c.request("PROPFIND", "/dav/", headers={"Depth": "1"})
    assert r.status_code == 207, r.text
    assert "Cloud" in r.text
    assert c.request("MKCOL", "/dav/Cloud/Photos").status_code == 201
    assert c.put("/dav/Cloud/Photos/pic.jpg", content=b"jpegdata").status_code in (201, 204)
    assert (env["storage"] / "Photos" / "pic.jpg").read_bytes() == b"jpegdata"
    assert c.get("/dav/Cloud/Photos/pic.jpg").content == b"jpegdata"
    r = c.request("PROPFIND", "/dav/Cloud/Photos/", headers={"Depth": "1"})
    assert "pic.jpg" in r.text and "getcontentlength" in r.text
    # overwrite keeps a version
    assert c.put("/dav/Cloud/Photos/pic.jpg", content=b"jpegdata v2").status_code in (201, 204)
    vers = admin.get("/api/files/versions", params={"root_id": admin.root_id, "path": "Photos/pic.jpg"}).json()
    assert len(vers) == 1
    r = c.request("COPY", "/dav/Cloud/Photos/pic.jpg", headers={"Destination": "/dav/Cloud/copy.jpg"})
    assert r.status_code in (201, 204), r.text
    r = c.request("MOVE", "/dav/Cloud/copy.jpg", headers={"Destination": "http://x/dav/Cloud/Photos/moved.jpg"})
    assert r.status_code in (201, 204), r.text
    assert (env["storage"] / "Photos" / "moved.jpg").exists()
    # DELETE goes to the recycle bin
    assert c.delete("/dav/Cloud/Photos/moved.jpg").status_code == 204
    assert not (env["storage"] / "Photos" / "moved.jpg").exists()
    assert any(t["rel_path"] == "Photos/moved.jpg" for t in admin.get("/api/trash").json())
    # LOCK/UNLOCK supported for Windows/macOS clients
    body = ('<?xml version="1.0"?><d:lockinfo xmlns:d="DAV:"><d:lockscope><d:exclusive/></d:lockscope>'
            '<d:locktype><d:write/></d:locktype></d:lockinfo>')
    r = c.request("LOCK", "/dav/Cloud/Photos/pic.jpg", content=body, headers={"Timeout": "Second-60"})
    assert r.status_code == 200, r.text
    lock = r.headers["Lock-Token"]
    assert c.request("UNLOCK", "/dav/Cloud/Photos/pic.jpg", headers={"Lock-Token": lock}).status_code == 204
    # activity logged
    acts = [a["action"] for a in admin.get("/api/activity").json()]
    assert "dav.put" in acts and "dav.delete" in acts


def test_webdav_auth_and_isolation(admin, env):
    c = TestClient(env["app"], client=("100.100.1.2", 4000))
    assert c.request("PROPFIND", "/dav/").status_code == 401
    # account password is NOT accepted over WebDAV
    c.headers["Authorization"] = "Basic " + base64.b64encode(b"owner:Correct-Horse-42").decode()
    assert c.request("PROPFIND", "/dav/").status_code == 401
    tok = admin.post("/api/tokens", {"name": "RO", "kind": "webdav", "read_only": True}).json()["token"]
    ro = dav_client(env, tok)
    assert ro.request("PROPFIND", "/dav/Cloud/", headers={"Depth": "1"}).status_code == 207
    assert ro.put("/dav/Cloud/x.txt", content=b"x").status_code == 403
    assert ro.delete("/dav/Cloud/anything").status_code in (403, 404)
    # wrong username for a valid token
    assert dav_client(env, tok, user="someone").request("PROPFIND", "/dav/").status_code == 401
    # traversal attempts
    full = dav_client(env, admin.post("/api/tokens", {"name": "F", "kind": "webdav"}).json()["token"])
    for bad in ["/dav/Cloud/../../Outside/secret.txt", "/dav/Cloud/%2e%2e/%2e%2e/Outside/secret.txt",
                "/dav/Outside/secret.txt"]:
        r = full.get(bad)
        assert r.status_code in (400, 403, 404, 503), bad
        assert b"top secret" not in r.content
    r = full.request("MOVE", "/dav/Cloud/", headers={"Destination": "/dav/Elsewhere/"})
    assert r.status_code in (403, 404)
    # disabling WebDAV for the location hides it
    admin.patch(f"/api/roots/{admin.root_id}", {"webdav": False})
    assert "Cloud" not in full.request("PROPFIND", "/dav/", headers={"Depth": "1"}).text


# ------------------------------------------------------------------ MCP
def mcp_call(client, token, method, params=None, id_=1):
    r = client.post("/mcp", headers={"Authorization": f"Bearer {token}",
                                     "Accept": "application/json, text/event-stream",
                                     "Content-Type": "application/json"},
                    content=json.dumps({"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}))
    assert r.status_code == 200, r.text
    return r.json()


def tool(client, token, name, **args):
    res = mcp_call(client, token, "tools/call", {"name": name, "arguments": args})
    assert "result" in res, res
    content = res["result"].get("structuredContent")
    if content is not None:
        return content.get("result", content)
    return json.loads(res["result"]["content"][0]["text"])


def test_mcp_requires_token(admin, env):
    c = env["client"]
    assert c.post("/mcp", json={}).status_code == 401
    dav_tok = admin.post("/api/tokens", {"name": "dav", "kind": "webdav"}).json()["token"]
    assert c.post("/mcp", json={}, headers={"Authorization": f"Bearer {dav_tok}"}).status_code == 401


def test_mcp_tools_and_approvals(admin, env):
    (env["storage"] / "Marketing").mkdir()
    (env["storage"] / "Marketing" / "oct-report.csv").write_text("channel,revenue\nemail,100\nads,250\n")
    (env["storage"] / "Marketing" / "memo.txt").write_text("October marketing performance memo")
    from pcloud.paths import get_root
    from pcloud.services import index
    index.scan_root(get_root(admin.root_id))
    # a location the AI must not see
    priv = env["tmp"] / "Private"
    priv.mkdir()
    (priv / "diary.txt").write_text("private")
    admin.post("/api/roots", {"name": "Private", "path": str(priv), "ai_read": False})

    tok = admin.post("/api/tokens", {"name": "Claude", "kind": "mcp"}).json()["token"]
    c = TestClient(env["app"], client=("100.64.0.5", 1234))
    init = mcp_call(c, tok, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                           "clientInfo": {"name": "test", "version": "1"}})
    assert init["result"]["serverInfo"]["name"] == "personal-cloud"
    names = {t["name"] for t in mcp_call(c, tok, "tools/list")["result"]["tools"]}
    assert {"search_files", "read_document", "save_document", "delete_file", "organize_files"} <= names

    locs = tool(c, tok, "list_locations")
    assert [l["location"] for l in locs] == ["Cloud"]
    assert "error" in tool(c, tok, "list_files", location="Private")
    hits = tool(c, tok, "search_files", query="october marketing")
    assert any(h["path"] == "Marketing/memo.txt" for h in hits)
    doc = tool(c, tok, "read_document", location="Cloud", path="Marketing/memo.txt")
    assert "performance" in doc["text"]
    sheet = tool(c, tok, "analyze_spreadsheet", location="Cloud", path="Marketing/oct-report.csv")
    rev = next(col for col in sheet["columns"] if col["name"] == "revenue")
    assert rev["sum"] == 350
    assert "error" in tool(c, tok, "read_document", location="Cloud", path="../Private/diary.txt")

    saved = tool(c, tok, "save_document", location="Cloud", path="Marketing/summary.md", content="# Summary")
    assert saved["saved"] == "Marketing/summary.md"
    assert (env["storage"] / "Marketing" / "summary.md").read_text() == "# Summary"

    # destructive action -> approval, nothing happens until approved
    req = tool(c, tok, "delete_file", location="Cloud", path="Marketing/memo.txt")
    assert req["status"] == "pending"
    assert (env["storage"] / "Marketing" / "memo.txt").exists()
    pending = admin.get("/api/ai/approvals").json()
    assert pending[0]["kind"] == "delete"
    r = admin.post(f"/api/ai/approvals/{req['approval_id']}", {"approve": True})
    assert r.json()["status"] == "executed"
    assert not (env["storage"] / "Marketing" / "memo.txt").exists()
    assert tool(c, tok, "get_approval_status", approval_id=req["approval_id"])["status"] == "executed"

    # rejected request
    req = tool(c, tok, "rename_file", location="Cloud", path="Marketing/summary.md", new_name="x.md")
    admin.post(f"/api/ai/approvals/{req['approval_id']}", {"approve": False})
    assert (env["storage"] / "Marketing" / "summary.md").exists()

    # overwrite needs approval
    req = tool(c, tok, "save_document", location="Cloud", path="Marketing/summary.md", content="new", overwrite=True)
    assert req["status"] == "pending"

    # AI actions are audited
    ai_acts = admin.get("/api/ai/activity").json()
    assert any(a["action"] == "ai.read_document" for a in ai_acts)

    # write-protect: revoke AI write -> save fails
    admin.patch(f"/api/roots/{admin.root_id}", {"ai_write": False})
    assert "error" in tool(c, tok, "save_document", location="Cloud", path="z.md", content="z")

    # revoked token is rejected
    tid = [t for t in admin.get("/api/tokens").json() if t["kind"] == "mcp"][0]["id"]
    admin.delete(f"/api/tokens/{tid}")
    assert c.post("/mcp", headers={"Authorization": f"Bearer {tok}"}, json={}).status_code == 401
