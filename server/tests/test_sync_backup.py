import os
import time

from conftest import wait_job


def test_mirror_sync(admin, env):
    (env["storage"] / "Docs").mkdir()
    (env["storage"] / "Docs" / "a.txt").write_text("A")
    dest = env["tmp"] / "Mirror"
    r = admin.post("/api/sync", {"name": "Docs mirror", "root_id": admin.root_id, "rel_path": "Docs",
                                 "dest_path": str(dest), "mode": "mirror"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    res = wait_job(admin, admin.post(f"/api/sync/{pid}/run").json()["job_id"])
    assert res["status"] == "succeeded", res
    assert (dest / "a.txt").read_text() == "A"
    os.remove(env["storage"] / "Docs" / "a.txt")
    res = wait_job(admin, admin.post(f"/api/sync/{pid}/run").json()["job_id"])
    assert res["result"]["deleted_dest"] == 1
    assert not (dest / "a.txt").exists()
    # deleted file is archived, not lost
    archived = list((env["data"] / "sync-archive").rglob("a.txt"))
    assert archived


def test_sync_dest_validation(admin, env):
    r = admin.post("/api/sync", {"name": "bad", "root_id": admin.root_id, "rel_path": "",
                                 "dest_path": str(env["storage"] / "inside"), "mode": "mirror"})
    assert r.status_code == 400


def test_two_way_sync_and_conflict(admin, env):
    src = env["storage"] / "Shared"
    src.mkdir()
    (src / "doc.txt").write_text("v1")
    dest = env["tmp"] / "Laptop"
    pid = admin.post("/api/sync", {"name": "2way", "root_id": admin.root_id, "rel_path": "Shared",
                                   "dest_path": str(dest), "mode": "two_way"}).json()["id"]
    wait_job(admin, admin.post(f"/api/sync/{pid}/run").json()["job_id"])
    (dest / "new-on-laptop.txt").write_text("hi")
    wait_job(admin, admin.post(f"/api/sync/{pid}/run").json()["job_id"])
    assert (src / "new-on-laptop.txt").read_text() == "hi"
    # both sides edit -> conflict copy, nothing lost
    time.sleep(0.05)
    (src / "doc.txt").write_text("pc edit")
    (dest / "doc.txt").write_text("laptop edit!!")
    t = time.time() + 10
    os.utime(dest / "doc.txt", (t, t))
    res = wait_job(admin, admin.post(f"/api/sync/{pid}/run").json()["job_id"])
    assert res["result"]["conflicts"] == 1, res
    assert (dest / "doc.txt").read_text() == "pc edit"
    conflict_files = [p for p in dest.iterdir() if "conflict" in p.name]
    assert conflict_files and conflict_files[0].read_text() == "laptop edit!!"
    assert admin.get(f"/api/sync/{pid}/conflicts").json()


def test_encrypted_backup_verify_restore(admin, env):
    (env["storage"] / "Photos").mkdir()
    (env["storage"] / "Photos" / "img.raw").write_bytes(os.urandom(5 * 1024 * 1024 + 7))  # spans 2 segments
    (env["storage"] / "notes.txt").write_text("original notes")
    dest = env["tmp"] / "BackupDrive"
    r = admin.post("/api/backups", {"name": "Nightly", "root_ids": [admin.root_id], "destination": str(dest),
                                    "encrypted": True, "keep_snapshots": 5})
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["recovery_key"]
    res = wait_job(admin, admin.post(f"/api/backups/{plan['id']}/run").json()["job_id"])
    assert res["status"] == "succeeded", res
    assert res["result"]["files"] == 2
    # contents are encrypted at rest
    blobs = list((dest).rglob("blobs/*/*"))
    assert blobs and all(b"original notes" not in b.read_bytes() for b in blobs)
    res = wait_job(admin, admin.post(f"/api/backups/{plan['id']}/verify").json()["job_id"])
    assert res["status"] == "succeeded" and res["result"]["ok"], res

    # simulate ransomware: file overwritten, then a later backup runs
    (env["storage"] / "notes.txt").write_text("ENCRYPTED BY RANSOMWARE")
    time.sleep(1.1)
    second = wait_job(admin, admin.post(f"/api/backups/{plan['id']}/run").json()["job_id"])
    assert second["result"]["new_blobs"] == 1  # dedup: only the changed file stored
    snaps = admin.get(f"/api/backups/{plan['id']}/snapshots").json()
    assert len(snaps) == 2
    oldest = snaps[-1]["id"]
    # restore the earlier snapshot using the recovery key (as if on a new PC)
    r = admin.post(f"/api/backups/{plan['id']}/restore", {"snapshot": oldest, "target_root_id": admin.root_id,
                                                          "recovery_key": plan["recovery_key"]})
    res = wait_job(admin, r.json()["job_id"])
    assert res["status"] == "succeeded", res
    out = env["storage"] / res["result"]["output"] / "Cloud"
    assert (out / "notes.txt").read_text() == "original notes"
    assert (out / "Photos" / "img.raw").read_bytes() == (env["storage"] / "Photos" / "img.raw").read_bytes()
    # wrong key is refused
    r = admin.post(f"/api/backups/{plan['id']}/restore", {"snapshot": oldest, "target_root_id": admin.root_id,
                                                          "recovery_key": "A" * 43})
    res = wait_job(admin, r.json()["job_id"])
    assert res["status"] == "failed"


def test_tampered_backup_detected(admin, env):
    (env["storage"] / "f.txt").write_text("data")
    dest = env["tmp"] / "B2"
    pid = admin.post("/api/backups", {"name": "B", "root_ids": [admin.root_id], "destination": str(dest)}).json()["id"]
    wait_job(admin, admin.post(f"/api/backups/{pid}/run").json()["job_id"])
    blob = next(p for p in dest.rglob("blobs/*/*"))
    raw = bytearray(blob.read_bytes())
    raw[-1] ^= 0xFF
    blob.write_bytes(bytes(raw))
    res = wait_job(admin, admin.post(f"/api/backups/{pid}/verify").json()["job_id"])
    assert res["status"] == "failed"


def test_job_recovery_after_restart(admin, env):
    from pcloud import db
    from pcloud.services.jobs import get_manager
    db.execute("INSERT INTO jobs(type, params, status, source, created_by, created_at) VALUES('compress','{}','running','user','owner',?)",
               (db.now(),))
    get_manager().recover()
    row = db.one("SELECT status FROM jobs ORDER BY id DESC LIMIT 1")
    assert row["status"] == "interrupted"


def test_overview_and_metrics(admin):
    o = admin.get("/api/system/overview").json()
    assert o["online"] and "cpu" in o
    m = admin.get("/api/system/metrics").json()
    assert m["memory"]["total"] > 0 and m["disks"]
    d = admin.get("/api/system/diagnostics").json()
    assert d["checks"]


def test_static_dashboard_and_head(env):
    web = env["tmp"] / "web"
    web.mkdir()
    (web / "index.html").write_text("<html>home</html>")
    (web / "files.html").write_text("<html>files</html>")
    from pcloud.config import get_config
    get_config().web_dir = web
    from pcloud.main import create_app
    from fastapi.testclient import TestClient
    c = TestClient(create_app(start_background=False), client=("127.0.0.1", 1))
    assert c.get("/files").text == "<html>files</html>"
    assert c.head("/files").status_code == 200
    assert "content-security-policy" in c.get("/").headers
    assert c.get("/../../etc/passwd").status_code in (403, 404)
