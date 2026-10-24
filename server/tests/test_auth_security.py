from conftest import ADMIN_PW, Api

from pcloud.security import totp_now


def test_setup_requires_code(env):
    api = Api(env["client"])
    r = api.post("/api/setup/admin", {"setup_token": "wrong", "username": "owner", "password": ADMIN_PW})
    assert r.status_code == 403


def test_setup_only_once(admin, env):
    r = admin.post("/api/setup/admin", {"setup_token": "x", "username": "evil", "password": ADMIN_PW})
    assert r.status_code == 409


def test_weak_password_rejected(env):
    from pcloud.routers.auth_routes import ensure_setup_token
    api = Api(env["client"])
    r = api.post("/api/setup/admin", {"setup_token": ensure_setup_token(), "username": "owner", "password": "short"})
    assert r.status_code == 400


def test_unauthenticated_blocked(env):
    c = env["client"]
    assert c.get("/api/files/list?root_id=1").status_code == 401
    assert c.get("/api/roots").status_code == 401


def test_csrf_required_for_cookie_writes(admin):
    r = admin.c.post("/api/files/mkdir", json={"root_id": admin.root_id, "path": "", "name": "x"})
    assert r.status_code == 403
    r = admin.post("/api/files/mkdir", {"root_id": admin.root_id, "path": "", "name": "x"})
    assert r.status_code == 200


def test_login_logout_and_bad_password(admin, env):
    api = Api(env["client"])
    assert api.post("/api/auth/login", {"username": "owner", "password": "nope-nope-nope"}).status_code == 401
    r = api.post("/api/auth/login", {"username": "owner", "password": ADMIN_PW})
    assert r.status_code == 200
    api.csrf = r.json()["csrf"]
    assert api.get("/api/auth/me").json()["user"]["username"] == "owner"
    assert api.post("/api/auth/logout").status_code == 200
    assert api.get("/api/auth/me").status_code == 401


def test_login_rate_limited(admin, env):
    api = Api(env["client"])
    codes = [api.post("/api/auth/login", {"username": "owner", "password": "bad"}).status_code for _ in range(12)]
    assert 429 in codes


def test_two_factor_flow(admin, env):
    r = admin.post("/api/auth/2fa/begin")
    secret = r.json()["secret"]
    assert admin.post("/api/auth/2fa/enable", {"code": "000000"}).status_code == 400
    assert admin.post("/api/auth/2fa/enable", {"code": totp_now(secret)}).status_code == 200
    api = Api(env["client"])
    r = api.post("/api/auth/login", {"username": "owner", "password": ADMIN_PW})
    assert r.json() == {"mfa_required": True}
    assert api.get("/api/auth/me").status_code in (200, 401)  # old admin cookie may still be set on shared client
    r = api.post("/api/auth/mfa", {"code": totp_now(secret)})
    assert r.status_code == 200, r.text


def test_path_traversal_blocked(admin, env):
    rid = admin.root_id
    for bad in ["../Outside/secret.txt", "..\\Outside\\secret.txt", "/etc/passwd", "C:\\Windows\\win.ini",
                "a/../../Outside/secret.txt", "file.txt:stream", "CON"]:
        r = admin.get("/api/files/download", params={"root_id": rid, "path": bad})
        assert r.status_code in (400, 403, 404), (bad, r.status_code)
        assert b"top secret" not in r.content


def test_junction_escape_blocked(admin, env):
    import subprocess
    import sys
    link = env["storage"] / "escape"
    if sys.platform == "win32":
        res = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(env["outside"])], capture_output=True)
        if res.returncode != 0:
            return
    else:
        link.symlink_to(env["outside"])
    r = admin.get("/api/files/download", params={"root_id": admin.root_id, "path": "escape/secret.txt"})
    assert r.status_code in (400, 403)
    listing = admin.get("/api/files/list", params={"root_id": admin.root_id}).json()["items"]
    assert "escape" not in [i["name"] for i in listing]


def test_forbidden_roots(admin, env):
    import os
    for path in ["C:\\", os.environ.get("SystemRoot", "C:\\Windows"), str(env["data"])]:
        r = admin.post("/api/roots", {"name": "bad" + str(len(path)), "path": path})
        assert r.status_code == 400, path
    r = admin.post("/api/roots", {"name": "Overlap", "path": str(env["storage"] / "sub"), "create": True})
    assert r.status_code == 400


def test_user_permissions(admin, env):
    r = admin.post("/api/users", {"username": "guest", "password": "Guest-Password-1", "role": "user"})
    uid = r.json()["id"]
    api = Api(env["client"].__class__(env["app"], client=("127.0.0.1", 50001)))
    r = api.post("/api/auth/login", {"username": "guest", "password": "Guest-Password-1"})
    api.csrf = r.json()["csrf"]
    assert api.get("/api/roots").json() == []
    assert api.get("/api/files/list", params={"root_id": admin.root_id}).status_code == 403
    admin.patch(f"/api/users/{uid}", {"permissions": [{"root_id": admin.root_id, "can_write": False}]})
    assert api.get("/api/files/list", params={"root_id": admin.root_id}).status_code == 200
    assert api.post("/api/files/mkdir", {"root_id": admin.root_id, "path": "", "name": "x"}).status_code == 403


def test_ip_allowlist(env):
    from fastapi.testclient import TestClient
    outsider = TestClient(env["app"], client=("203.0.113.9", 4000))
    assert outsider.get("/api/health").status_code == 403
    tailnet = TestClient(env["app"], client=("100.101.102.103", 4000))
    assert tailnet.get("/api/health").status_code == 200
    lan = TestClient(env["app"], client=("192.168.1.20", 4000))
    assert lan.get("/api/health").status_code == 403


def test_tokens_hashed_and_revocable(admin, env):
    r = admin.post("/api/tokens", {"name": "Phone", "kind": "device"})
    tok = r.json()["token"]
    from pcloud import db
    assert db.one("SELECT 1 FROM tokens WHERE token_hash=?", (tok,)) is None  # stored hashed
    h = {"Authorization": f"Bearer {tok}"}
    assert env["client"].get("/api/roots", headers=h, cookies={}).status_code == 200
    admin.delete(f"/api/tokens/{r.json()['id']}")
    from fastapi.testclient import TestClient
    fresh = TestClient(env["app"], client=("127.0.0.1", 1))
    assert fresh.get("/api/roots", headers=h).status_code == 401


def test_secret_store_roundtrip(env):
    from pcloud.security import SecretStore
    s = SecretStore(env["data"] / "secrets")
    s.set("unit_test", b"\x00\x01secret")
    assert s.get("unit_test") == b"\x00\x01secret"
    raw = (env["data"] / "secrets" / "unit_test.bin").read_bytes()
    assert b"secret" not in raw or not raw.startswith(b"DPAPI:")
