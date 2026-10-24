"""Authenticated WebDAV endpoint (/dav) for native file managers.

Layout seen by clients:
    /dav/                      -> one folder per approved storage location the user can access
    /dav/<location>/...        -> files inside that location

Auth: HTTP Basic with your username + a WebDAV *app password* (create one under Mobile Access).
Your real account password is never accepted here, so a lost phone only needs its app password revoked.

Safety layers (in front of WsgiDAV):
  * per-user / per-location read & write checks (including MOVE/COPY destinations)
  * DELETE goes to the recycle bin instead of erasing data
  * overwriting PUT keeps a previous version when versioning is enabled
  * every path resolves through paths.resolve(), so traversal and junction escapes are rejected
"""
from __future__ import annotations

import base64
import logging
import os
import threading
from urllib.parse import unquote, urlparse

from wsgidav.dav_error import HTTP_FORBIDDEN, DAVError
from wsgidav.dav_provider import DAVCollection
from wsgidav.fs_dav_provider import FilesystemProvider, FileResource, FolderResource
from wsgidav.wsgidav_app import WsgiDAVApp

from . import audit, auth
from .config import get_config
from .paths import NotFound, PathError, list_roots, resolve
from .security import limiter
from .services import fileops, index

log = logging.getLogger("pcloud.webdav")
WRITE_METHODS = {"PUT", "MKCOL", "DELETE", "MOVE", "COPY", "PROPPATCH", "LOCK", "UNLOCK"}
MOUNT = "/dav"


def _principal(environ) -> auth.Principal:
    return environ["pcloud.principal"]


def _split(path: str) -> tuple[str, str]:
    parts = path.strip("/").split("/", 1)
    return parts[0], (parts[1] if len(parts) > 1 else "")


def _root_for(environ, name: str):
    p = _principal(environ)
    for r in list_roots():
        if r.name.lower() == name.lower() and p.root_access(r):
            return r
    return None


class RootsCollection(DAVCollection):
    """Virtual top-level folder listing the user's storage locations."""

    def __init__(self, path, environ):
        super().__init__(path, environ)

    def get_member_names(self):
        p = _principal(self.environ)
        return [r.name for r in list_roots() if p.root_access(r) and os.path.isdir(r.path)]

    def get_member(self, name):
        return self.provider.get_resource_inst("/" + name, self.environ)

    def get_display_info(self):
        return {"type": "Directory"}

    def get_creation_date(self):
        return None

    def get_last_modified(self):
        return None


class PCloudProvider(FilesystemProvider):
    def __init__(self):
        super().__init__(str(get_config().tmp_dir), readonly=False, fs_opts={"follow_symlinks": False})

    def _loc_to_file_path(self, path: str, environ: dict = None):
        name, rest = _split(path)
        root = _root_for(environ, name) if name else None
        if root is None:
            raise DAVError(HTTP_FORBIDDEN, "Not an accessible storage location")
        try:
            fp, _ = resolve(root, unquote(rest) if "%" in rest else rest)
        except (PathError, NotFound) as e:
            raise DAVError(HTTP_FORBIDDEN, str(e))
        return fp

    def get_resource_inst(self, path: str, environ: dict):
        if path.strip("/") == "":
            return RootsCollection("/", environ)
        name, _ = _split(path)
        if _root_for(environ, name) is None:
            return None
        fp = self._loc_to_file_path(path, environ)
        if not os.path.exists(fp):
            return None
        if os.path.isdir(fp):
            return FolderResource(path, environ, fp)
        return FileResource(path, environ, fp)


def _build_dav() -> WsgiDAVApp:
    config = {
        "provider_mapping": {"/": PCloudProvider()},
        "mount_path": MOUNT,
        "simple_dc": {"user_mapping": {"*": True}},  # authentication is enforced by DavGate below
        "http_authenticator": {"accept_basic": True, "accept_digest": False, "default_to_digest": False},
        "lock_storage": True,
        "property_manager": True,
        "dir_browser": {"enable": False},
        "verbose": 1,
        "logging": {"enable": False},
        "add_header_MS_Author_Via": True,
    }
    return WsgiDAVApp(config)


class DavGate:
    """WSGI middleware: authentication, permissions, trash/versioning, indexing and auditing."""

    def __init__(self):
        self._app: WsgiDAVApp | None = None
        self._lock = threading.Lock()

    @property
    def app(self) -> WsgiDAVApp:
        if self._app is None:
            with self._lock:
                if self._app is None:
                    self._app = _build_dav()
        return self._app

    # ---------------------------------------------------------- helpers
    @staticmethod
    def _deny(start_response, status: str, msg: str, auth_challenge: bool = False):
        headers = [("Content-Type", "text/plain; charset=utf-8")]
        if auth_challenge:
            headers.append(("WWW-Authenticate", 'Basic realm="Personal Cloud OS", charset="UTF-8"'))
        start_response(status, headers)
        return [msg.encode()]

    @staticmethod
    def _authenticate(environ) -> auth.Principal | None:
        ip = environ.get("REMOTE_ADDR", "unknown")
        header = environ.get("HTTP_AUTHORIZATION", "")
        token = None
        if header.lower().startswith("basic "):
            try:
                user, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
            except Exception:
                return None
            token = pw
            p = auth.token_principal(token, ip, kinds={"webdav", "device"})
            if p and p.username.lower() != user.strip().lower():
                p = None
        elif header.lower().startswith("bearer "):
            p = auth.token_principal(header[7:].strip(), ip, kinds={"webdav", "device"})
        else:
            return None
        return p

    @staticmethod
    def _dav_path(environ) -> str:
        path = environ.get("PATH_INFO", "") or "/"
        if path.startswith(MOUNT):
            path = path[len(MOUNT):] or "/"
        return path

    @staticmethod
    def _dest_path(environ) -> str | None:
        dest = environ.get("HTTP_DESTINATION")
        if not dest:
            return None
        path = unquote(urlparse(dest).path)
        if path.startswith(MOUNT):
            path = path[len(MOUNT):]
        return path or "/"

    def _resolve(self, environ, dav_path: str):
        name, rest = _split(dav_path)
        root = _root_for(environ, name) if name else None
        if root is None:
            return None, None, None
        try:
            abs_path, norm = resolve(root, rest)
        except (PathError, NotFound):
            return root, None, None
        return root, abs_path, norm

    # ------------------------------------------------------------- WSGI
    def __call__(self, environ, start_response):
        ip = environ.get("REMOTE_ADDR", "unknown")
        method = environ["REQUEST_METHOD"].upper()
        if method == "OPTIONS" and not environ.get("HTTP_AUTHORIZATION"):
            # Some clients probe capabilities before authenticating.
            start_response("200 OK", [("DAV", "1, 2"), ("Allow", "OPTIONS, GET, HEAD, PUT, DELETE, PROPFIND, "
                                       "PROPPATCH, MKCOL, COPY, MOVE, LOCK, UNLOCK"), ("MS-Author-Via", "DAV"),
                                      ("Content-Length", "0")])
            return [b""]
        if not limiter.hit(f"dav-fail:{ip}", 30, 300) and not environ.get("HTTP_AUTHORIZATION"):
            return self._deny(start_response, "429 Too Many Requests", "Too many requests")
        p = self._authenticate(environ)
        if p is None:
            if environ.get("HTTP_AUTHORIZATION"):
                if not limiter.hit(f"dav-badauth:{ip}", 10, 300):
                    audit.record("device", "webdav", "dav.auth", None, "rate_limited", ip)
                    return self._deny(start_response, "429 Too Many Requests", "Too many failed sign-ins")
                audit.record("device", "webdav", "dav.auth", None, "failed", ip)
            return self._deny(start_response, "401 Unauthorized",
                              "Sign in with your username and a WebDAV app password", auth_challenge=True)
        environ["pcloud.principal"] = p

        dav_path = self._dav_path(environ)
        root, abs_path, norm = self._resolve(environ, dav_path) if dav_path.strip("/") else (None, None, None)
        if dav_path.strip("/") and root is None:
            return self._deny(start_response, "404 Not Found", "Not found")
        if dav_path.strip("/") and abs_path is None:
            return self._deny(start_response, "403 Forbidden", "Path not allowed")

        if method in WRITE_METHODS:
            if root is None:
                if method in ("PROPPATCH", "LOCK", "UNLOCK"):
                    return self.app(environ, start_response)
                return self._deny(start_response, "403 Forbidden", "The top level is read-only")
            needs_write_src = method != "COPY"
            if needs_write_src and p.root_access(root) != "rw":
                return self._deny(start_response, "403 Forbidden", "Read-only location")
            if method in ("MOVE", "COPY"):
                dest = self._dest_path(environ)
                d_root, d_abs, d_norm = self._resolve(environ, dest or "")
                if d_root is None or d_abs is None or p.root_access(d_root) != "rw" or not d_norm:
                    return self._deny(start_response, "403 Forbidden", "Destination not writable")
                # Behind `tailscale serve` the client sees https://<name>.ts.net while we receive plain http;
                # reduce Destination to its path so WsgiDAV's same-scheme/host check does not reject it.
                environ["HTTP_DESTINATION"] = urlparse(environ["HTTP_DESTINATION"]).path
                return self._run_tracked(environ, start_response, p, method, root, norm, d_root, d_abs, d_norm)
            if method == "DELETE":
                if not norm:
                    return self._deny(start_response, "403 Forbidden", "A storage location cannot be deleted")
                if not os.path.exists(abs_path):
                    return self._deny(start_response, "404 Not Found", "Not found")
                try:
                    fileops.delete(root, norm, actor=p.label)
                except Exception as e:
                    log.exception("dav delete failed")
                    return self._deny(start_response, "500 Internal Server Error", str(e))
                audit.from_principal(p, "dav.delete", f"{root.name}/{norm}")
                start_response("204 No Content", [("Content-Length", "0")])
                return [b""]
            if method == "PUT":
                if not norm:
                    return self._deny(start_response, "403 Forbidden", "Invalid target")
                try:
                    fileops.validate_name(os.path.basename(norm))
                except PathError as e:
                    return self._deny(start_response, "400 Bad Request", str(e))
                if os.path.isfile(abs_path) and os.path.getsize(abs_path) > 0:
                    try:
                        fileops.snapshot_version(root, norm, abs_path, "WebDAV overwrite")
                    except Exception:
                        log.exception("version snapshot failed")
            if method in ("PUT", "MKCOL"):
                return self._run_tracked(environ, start_response, p, method, root, norm)
        return self.app(environ, start_response)

    def _run_tracked(self, environ, start_response, p, method, root, norm, d_root=None, d_abs=None, d_norm=None):
        status_holder = {}

        def sr(status, headers, exc_info=None):
            status_holder["status"] = status
            return start_response(status, headers, exc_info) if exc_info else start_response(status, headers)

        result = self.app(environ, sr)
        try:
            yield from result
        finally:
            if hasattr(result, "close"):
                result.close()
            code = int((status_holder.get("status") or "500").split()[0])
            ok = 200 <= code < 300
            target = f"{root.name}/{norm}"
            if ok:
                try:
                    if method == "PUT":
                        index.upsert(root, resolve(root, norm)[0], norm)
                    elif method == "MKCOL":
                        index.upsert(root, resolve(root, norm)[0], norm)
                    elif method == "MOVE":
                        index.remove(root, norm)
                        index.upsert(d_root, d_abs, d_norm, recursive=True)
                    elif method == "COPY":
                        index.upsert(d_root, d_abs, d_norm, recursive=True)
                except Exception:
                    log.exception("index update after %s failed", method)
            details = {"to": f"{d_root.name}/{d_norm}"} if d_root else None
            audit.from_principal(p, f"dav.{method.lower()}", target, "ok" if ok else f"http {code}", details)


dav_gate = DavGate()
