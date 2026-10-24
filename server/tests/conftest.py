import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from pcloud import config as cfgmod  # noqa: E402

ADMIN_PW = "Correct-Horse-42"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    storage = tmp_path / "Storage"
    outside = tmp_path / "Outside"
    for d in (storage, outside):
        d.mkdir()
    (outside / "secret.txt").write_text("top secret")
    cfg = cfgmod.Config(data_dir=data, web_dir=tmp_path / "noweb")
    cfgmod.set_config(cfg)
    from pcloud import db
    db.close_thread_connection()
    from pcloud.security import limiter
    limiter._hits.clear()
    from pcloud.main import create_app
    app = create_app(start_background=False)
    client = TestClient(app, client=("127.0.0.1", 50000))
    client.__enter__()
    yield {"app": app, "client": client, "data": data, "storage": storage, "outside": outside, "tmp": tmp_path}
    client.__exit__(None, None, None)
    from pcloud.services import jobs
    if jobs.manager:
        jobs.manager.shutdown()
        jobs.manager = None
    db.close_thread_connection()


class Api:
    def __init__(self, client):
        self.c = client
        self.csrf = None

    def _h(self):
        return {"x-csrf-token": self.csrf} if self.csrf else {}

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, json=None, **kw):
        return self.c.post(url, json=json, headers=self._h() | kw.pop("headers", {}), **kw)

    def put(self, url, **kw):
        return self.c.put(url, headers=self._h() | kw.pop("headers", {}), **kw)

    def patch(self, url, json=None):
        return self.c.patch(url, json=json, headers=self._h())

    def delete(self, url):
        return self.c.delete(url, headers=self._h())


@pytest.fixture()
def admin(env):
    from pcloud.routers.auth_routes import ensure_setup_token
    api = Api(env["client"])
    token = ensure_setup_token()
    r = api.post("/api/setup/admin", {"setup_token": token, "username": "owner", "password": ADMIN_PW})
    assert r.status_code == 200, r.text
    api.csrf = r.json()["csrf"]
    r = api.post("/api/roots", {"name": "Cloud", "path": str(env["storage"]), "ai_read": True, "ai_write": True})
    assert r.status_code == 200, r.text
    api.root_id = r.json()["id"]
    return api


def wait_job(api, job_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        j = api.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("succeeded", "failed", "cancelled", "interrupted"):
            return j
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not finish")
