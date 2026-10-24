from fastapi.testclient import TestClient

from conftest import Api


def test_connect_status(admin):
    s = admin.get("/api/mobile/status").json()
    ids = [x["id"] for x in s["steps"]]
    assert ids[:4] == ["server", "tailscale", "https", "address"]
    assert s["steps"][0]["ok"] is True
    assert "base_url" in s


def test_qr_pairing_signs_in_phone_once(admin, env):
    r = admin.post("/api/mobile/pair", {"base_url": "https://home-pc.tail123.ts.net"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["url"].startswith("https://home-pc.tail123.ts.net/pair?code=")
    assert data["qr_svg"].startswith("<svg")
    code = data["url"].split("code=")[1]

    phone = Api(TestClient(env["app"], client=("100.90.1.7", 4000),
                           headers={"user-agent": "Mozilla/5.0 (Linux; Android 15) Mobile"}))
    assert phone.get("/api/auth/me").status_code == 401
    r = phone.post("/api/mobile/pair/redeem", {"code": code})
    assert r.status_code == 200, r.text
    phone.csrf = r.json()["csrf"]
    assert phone.get("/api/auth/me").json()["user"]["username"] == "owner"
    # single use
    other = Api(TestClient(env["app"], client=("100.90.1.8", 4000)))
    assert other.post("/api/mobile/pair/redeem", {"code": code}).status_code == 400
    # the dashboard now sees a phone session
    steps = {x["id"]: x for x in admin.get("/api/mobile/status").json()["steps"]}
    assert steps["signed_in"]["ok"] is True


def test_pairing_rejects_bad_codes_and_outsiders(admin, env):
    phone = Api(TestClient(env["app"], client=("100.90.1.9", 4000)))
    codes = [phone.post("/api/mobile/pair/redeem", {"code": "x" * 30}).status_code for _ in range(12)]
    assert codes[0] == 400 and 429 in codes
    code = admin.post("/api/mobile/pair", {}).json()["url"].split("code=")[1]
    outsider = TestClient(env["app"], client=("203.0.113.5", 4000))
    assert outsider.post("/api/mobile/pair/redeem", json={"code": code}).status_code == 403


def test_pair_requires_interactive_session(admin, env):
    tok = admin.post("/api/tokens", {"name": "script", "kind": "device"}).json()["token"]
    c = TestClient(env["app"], client=("127.0.0.1", 9))
    assert c.post("/api/mobile/pair", json={}, headers={"Authorization": f"Bearer {tok}"}).status_code == 403
