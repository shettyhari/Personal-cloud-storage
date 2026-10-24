"""FastAPI application factory."""
from __future__ import annotations

import logging
import logging.handlers
import os
from contextlib import asynccontextmanager

from a2wsgi import WSGIMiddleware
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from . import __version__, db, settings_store
from .config import get_config
from .paths import NotFound, PathError
from .routers import (admin_routes, ai_routes, auth_routes, devices, files, jobs_routes, mobile, storage, sync_backup,
                      system, uploads)
from .security import ip_allowed, limiter
from .services import backup as _backup, remote as _remote, sync as _sync, tasks as _tasks  # noqa: F401 - register jobs
from .services.jobs import get_manager
from .services.monitor import monitor
from .services.scheduler import scheduler

log = logging.getLogger("pcloud")


def setup_logging() -> None:
    cfg = get_config()
    root = logging.getLogger()
    if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        return
    root.setLevel(logging.INFO)
    fh = logging.handlers.RotatingFileHandler(cfg.logs_dir / "server.log", maxBytes=5 * 1024 * 1024, backupCount=5,
                                              encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    for noisy in ("wsgidav", "httpx", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
]
APP_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
           "img-src 'self' data: blob:; media-src 'self' blob:; frame-src 'self' blob:; connect-src 'self'; "
           "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'")


class NetworkGate:
    """Outermost ASGI middleware: client IP allow-list, global rate limit, security headers."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        ip = (scope.get("client") or ("0.0.0.0",))[0]
        if not ip_allowed(ip, bool(settings_store.get("allow_tailscale")), bool(settings_store.get("allow_lan")),
                          settings_store.get("extra_allowed_cidrs") or []):
            await self._plain(send, 403, b"Access from this network is not allowed. Connect through Tailscale.")
            return
        if not limiter.hit(f"global:{ip}", 3000, 60):
            await self._plain(send, 429, b"Too many requests")
            return
        path = scope.get("path", "")

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                names = {h[0].lower() for h in headers}
                headers += [h for h in SECURITY_HEADERS if h[0] not in names]
                if not path.startswith(("/api", "/dav", "/mcp")) and b"content-security-policy" not in names:
                    headers.append((b"content-security-policy", APP_CSP.encode()))
                if get_config().secure_cookies:
                    headers.append((b"strict-transport-security", b"max-age=31536000"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)

    @staticmethod
    async def _plain(send, status, body):
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": body})


def create_app(start_background: bool = True) -> FastAPI:
    cfg = get_config()
    setup_logging()
    db.migrate()
    token = auth_routes.ensure_setup_token()
    if token:
        log.warning("First-time setup code: %s", token)
    from . import mcp_server
    from .webdav import dav_gate
    mcp_endpoint = mcp_server.MCPEndpoint()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_background:
            monitor.start()
            get_manager().recover()
            scheduler.start()
        async with mcp_server.mcp.session_manager.run():
            log.info("Personal Cloud OS %s started on %s:%s (data: %s)", __version__, cfg.host, cfg.port, cfg.data_dir)
            yield
        scheduler.stop()
        monitor.stop()
        get_manager().shutdown()

    app = FastAPI(title="Personal Cloud OS", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs" if cfg.dev_mode else None, redoc_url=None,
                  openapi_url="/api/openapi.json" if cfg.dev_mode else None)

    for r in (auth_routes, files, uploads, storage, devices, mobile, system, jobs_routes, ai_routes, sync_backup, admin_routes):
        app.include_router(r.router)

    @app.exception_handler(PathError)
    async def _path_error(request: Request, exc: PathError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, exc: NotFound):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        errs = exc.errors()
        msg = "; ".join(f"{'.'.join(str(x) for x in e.get('loc', [])[1:])}: {e.get('msg')}" for e in errs[:5])
        return JSONResponse({"detail": msg or "Invalid request", "errors": errs[:10]}, status_code=422)

    # MCP (streamable HTTP) - exact path, no redirect
    app.router.routes.append(Route("/mcp", endpoint=mcp_endpoint, methods=["GET", "POST", "DELETE"]))

    # WebDAV
    app.mount("/dav", WSGIMiddleware(dav_gate, workers=16))

    @app.api_route("/dav", methods=["GET", "PROPFIND", "OPTIONS", "HEAD"], include_in_schema=False)
    async def dav_root_redirect():
        return Response(status_code=301, headers={"Location": "/dav/"})

    # Static dashboard (Next.js static export)
    web_dir = cfg.web_dir

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith(("api/", "dav/", "mcp")):
            raise HTTPException(404)
        if not (web_dir / "index.html").exists():
            return PlainTextResponse("Personal Cloud OS server is running, but the dashboard has not been built.\n"
                                     "Run scripts\\build-web.ps1 (or `npm run build` in web/).", status_code=503)
        base = os.path.realpath(web_dir)
        candidates = [full_path, f"{full_path}.html", f"{full_path.rstrip('/')}/index.html"] if full_path else ["index.html"]
        for c in candidates:
            fp = os.path.realpath(os.path.join(base, c))
            if os.path.commonpath([fp, base]) == base and os.path.isfile(fp):
                cache = "public, max-age=31536000, immutable" if "/_next/static/" in fp.replace("\\", "/") else "no-cache"
                return FileResponse(fp, headers={"Cache-Control": cache})
        return FileResponse(os.path.join(base, "404.html") if os.path.exists(os.path.join(base, "404.html"))
                            else os.path.join(base, "index.html"), status_code=404)

    if cfg.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=cfg.cors_origins, allow_credentials=True,
                           allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                           allow_headers=["content-type", "x-csrf-token", "authorization"])
    app.add_middleware(NetworkGate)
    return app
