import hashlib
import io
import os
import zipfile

from conftest import wait_job


def _upload(api, root_id, name, data, dir_="", chunk=3):
    r = api.post("/api/uploads", {"root_id": root_id, "dir": dir_, "filename": name, "size": len(data),
                                  "sha256": hashlib.sha256(data).hexdigest()})
    assert r.status_code == 200, r.text
    uid = r.json()["id"]
    off = r.json()["received"]
    while off < len(data):
        r = api.put(f"/api/uploads/{uid}?offset={off}", content=data[off:off + chunk])
        assert r.status_code == 200, r.text
        off = r.json()["received"]
    r = api.post(f"/api/uploads/{uid}/complete")
    assert r.status_code == 200, r.text
    return r.json()


def test_upload_download_roundtrip(admin, env):
    data = os.urandom(10_000)
    e = _upload(admin, admin.root_id, "blob.bin", data, chunk=4096)
    assert e["sha256"] == hashlib.sha256(data).hexdigest()
    r = admin.get("/api/files/download", params={"root_id": admin.root_id, "path": "blob.bin"})
    assert r.content == data
    r = admin.get("/api/files/download", params={"root_id": admin.root_id, "path": "blob.bin"},
                  headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == data[10:20]


def test_resumable_upload(admin, env):
    data = b"0123456789" * 1000
    r = admin.post("/api/uploads", {"root_id": admin.root_id, "filename": "resume.txt", "size": len(data)})
    uid = r.json()["id"]
    admin.put(f"/api/uploads/{uid}?offset=0", content=data[:4000])
    # client "reconnects": creating the same upload returns the resume point
    r = admin.post("/api/uploads", {"root_id": admin.root_id, "filename": "resume.txt", "size": len(data)})
    assert r.json()["id"] == uid and r.json()["received"] == 4000 and r.json()["resumed"]
    # wrong offset is rejected with the real resume point
    r = admin.put(f"/api/uploads/{uid}?offset=10", content=b"x")
    assert r.status_code == 409
    admin.put(f"/api/uploads/{uid}?offset=4000", content=data[4000:])
    assert admin.post(f"/api/uploads/{uid}/complete").status_code == 200
    assert (env["storage"] / "resume.txt").read_bytes() == data


def test_upload_checksum_mismatch(admin, env):
    r = admin.post("/api/uploads", {"root_id": admin.root_id, "filename": "bad.txt", "size": 3, "sha256": "0" * 64})
    uid = r.json()["id"]
    admin.put(f"/api/uploads/{uid}?offset=0", content=b"abc")
    assert admin.post(f"/api/uploads/{uid}/complete").status_code == 422
    assert not (env["storage"] / "bad.txt").exists()


def test_folder_ops_trash_and_versions(admin, env):
    rid = admin.root_id
    assert admin.post("/api/files/mkdir", {"root_id": rid, "path": "", "name": "Docs"}).status_code == 200
    assert admin.post("/api/files/mkdir", {"root_id": rid, "path": "", "name": "Docs"}).status_code == 409
    _upload(admin, rid, "a.txt", b"version one", "Docs")
    r = admin.post("/api/uploads", {"root_id": rid, "dir": "Docs", "filename": "a.txt", "size": 11, "overwrite": True})
    uid = r.json()["id"]
    admin.put(f"/api/uploads/{uid}?offset=0", content=b"version two")
    admin.post(f"/api/uploads/{uid}/complete")
    assert (env["storage"] / "Docs" / "a.txt").read_text() == "version two"
    vers = admin.get("/api/files/versions", params={"root_id": rid, "path": "Docs/a.txt"}).json()
    assert len(vers) == 1
    admin.post(f"/api/files/versions/{vers[0]['id']}/restore", {"root_id": rid})
    assert (env["storage"] / "Docs" / "a.txt").read_text() == "version one"

    r = admin.post("/api/files/rename", {"root_id": rid, "path": "Docs/a.txt", "new_name": "b.txt"})
    assert r.json()["path"] == "Docs/b.txt"
    admin.post("/api/files/mkdir", {"root_id": rid, "path": "", "name": "Archive"})
    r = admin.post("/api/files/transfer", {"items": [{"root_id": rid, "path": "Docs/b.txt"}], "dest_root_id": rid,
                                           "dest_dir": "Archive", "as_copy": True})
    assert r.json()["results"][0]["ok"]
    r = admin.post("/api/files/transfer", {"items": [{"root_id": rid, "path": "Docs"}], "dest_root_id": rid,
                                           "dest_dir": "Docs"})
    assert not r.json()["results"][0]["ok"]  # into itself

    r = admin.post("/api/files/delete", {"items": [{"root_id": rid, "path": "Archive/b.txt"}]})
    assert r.json()["results"][0]["ok"]
    assert not (env["storage"] / "Archive" / "b.txt").exists()
    trash = admin.get("/api/trash").json()
    assert trash[0]["rel_path"] == "Archive/b.txt"
    admin.post(f"/api/trash/{trash[0]['id']}/restore")
    assert (env["storage"] / "Archive" / "b.txt").exists()


def test_invalid_names(admin):
    for bad in ["a/b", "x:y", "CON", "trailing.", "", ".."]:
        r = admin.post("/api/files/mkdir", {"root_id": admin.root_id, "path": "", "name": bad})
        assert r.status_code in (400, 422), bad


def test_search_and_index(admin, env):
    (env["storage"] / "Marketing").mkdir()
    (env["storage"] / "Marketing" / "october-report.txt").write_text("Campaign revenue grew 20 percent in October")
    (env["storage"] / "notes.md").write_text("grocery list: apples")
    from pcloud.services import index
    from pcloud.paths import get_root
    index.scan_root(get_root(admin.root_id))
    r = admin.get("/api/search", params={"q": "october"}).json()
    assert any(x["path"] == "Marketing/october-report.txt" for x in r)
    r = admin.get("/api/search", params={"q": "revenue"}).json()
    assert r and r[0]["match"] == "content" and "revenue" in r[0]["snippet"].lower()
    r = admin.get("/api/search", params={"q": "", "ext": "md"}).json()
    assert [x["name"] for x in r] == ["notes.md"]
    st = admin.get("/api/storage/stats").json()
    assert st["files"] == 2 and st["total"] > 0


def test_zip_download_and_preview(admin, env):
    (env["storage"] / "f1.txt").write_text("hello")
    (env["storage"] / "page.html").write_text("<script>alert(1)</script>")
    r = admin.post("/api/files/zip", {"items": [{"root_id": admin.root_id, "path": "f1.txt"}]})
    z = admin.get(f"/api/files/zip/{r.json()['ticket']}")
    assert zipfile.ZipFile(io.BytesIO(z.content)).read("f1.txt") == b"hello"
    r = admin.get("/api/files/download", params={"root_id": admin.root_id, "path": "page.html", "inline": True})
    assert r.headers["content-type"].startswith("text/plain")
    assert "sandbox" in r.headers["content-security-policy"]
    assert admin.get("/api/files/text", params={"root_id": admin.root_id, "path": "f1.txt"}).text == "hello"
    r = admin.get("/api/files/checksum", params={"root_id": admin.root_id, "path": "f1.txt"})
    assert r.json()["checksum"] == hashlib.sha256(b"hello").hexdigest()


def test_jobs_compress_extract_inventory(admin, env):
    (env["storage"] / "src").mkdir()
    (env["storage"] / "src" / "one.txt").write_text("1")
    (env["storage"] / "src" / "two.txt").write_text("2")
    rid = admin.root_id
    j = admin.post("/api/jobs", {"type": "compress", "params": {"root_id": rid, "paths": ["src"], "archive_name": "src.zip"}})
    res = wait_job(admin, j.json()["id"])
    assert res["status"] == "succeeded", res
    j = admin.post("/api/jobs", {"type": "extract", "params": {"root_id": rid, "path": "src.zip"}})
    res = wait_job(admin, j.json()["id"])
    assert res["status"] == "succeeded", res
    assert (env["storage"] / res["result"]["output"] / "src" / "two.txt").read_text() == "2"
    j = admin.post("/api/jobs", {"type": "inventory", "params": {"root_id": rid, "path": "src"}})
    res = wait_job(admin, j.json()["id"])
    assert res["status"] == "succeeded" and res["result"]["files"] == 2


def test_zip_slip_rejected(admin, env):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../../evil.txt", "pwned")
    (env["storage"] / "evil.zip").write_bytes(buf.getvalue())
    j = admin.post("/api/jobs", {"type": "extract", "params": {"root_id": admin.root_id, "path": "evil.zip"}})
    res = wait_job(admin, j.json()["id"])
    assert res["status"] == "failed"
    assert not (env["tmp"] / "evil.txt").exists()


def test_job_types_restricted(admin):
    r = admin.post("/api/jobs", {"type": "backup", "params": {"plan_id": 1}})
    assert r.status_code == 400
