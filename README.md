# Personal Cloud OS

Turn your Windows PC into a private cloud drive, remote server and AI-accessible workspace — reachable from
your phone, laptop and Claude, without exposing the PC to the public internet.

- **Private cloud storage** – browse, upload (chunked, resumable, checksummed), download, preview, rename,
  move, copy, delete (recycle bin), version history, full-text search, storage analytics.
- **Native mobile file managers** – authenticated WebDAV at `/dav/` for Android file managers and iOS WebDAV apps.
- **Claude integration** – an MCP server at `/mcp` (and stdio) lets Claude search, read, analyze and create
  documents in folders you approve. Moves/renames/deletes/overwrites wait for your approval in the dashboard.
- **Remote computer** – live CPU/RAM/disk/network/GPU monitoring, background tasks (compress, extract,
  inventories, duplicate finder, batch text extraction, format conversion), admin-registered scripts and apps,
  RustDesk/RDP integration.
- **Sync & backup** – folder sync (mirror / two-way with conflict copies) and encrypted, deduplicated,
  versioned snapshot backups with verify & restore.
- **Security first** – only approved folders are reachable, scrypt passwords, optional TOTP 2FA, revocable
  per-device app passwords, CSRF protection, rate limiting, IP allow-list (this PC + Tailscale), audit log,
  DPAPI-protected secrets, runs as a standard (non-admin) user.

Your data never leaves your PC unless you choose to (e.g. by connecting Claude, which then reads only what it is
allowed to). **When the PC is off or asleep, the cloud is offline** — the dashboard says so instead of pretending.

## Quick start (Windows 10/11)

```powershell
cd "C:\path\to\Home PC Server"
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

The installer checks/installs Python 3.11+ and Node.js, installs dependencies, builds the dashboard, asks about
auto-start, starts the server and opens the setup wizard at `http://127.0.0.1:8787/setup`. The wizard walks you
through:

1. Administrator account (requires the one-time **setup code** shown by the installer / in `setup-token.txt`)
2. Approved storage folders (e.g. `D:\PersonalCloud`, `E:\Media`)
3. Network access (Tailscale + HTTPS)
4. AI integration (MCP token for Claude)
5. Mobile access (WebDAV app password)
6. Backups (second drive, encrypted)

Then: [connect from anywhere](docs/REMOTE-ACCESS.md) · [set up your phone](docs/MOBILE.md) ·
[connect Claude](docs/AI-INTEGRATION.md).

## Everyday commands

```powershell
scripts\pcloud.ps1 status            # running?
scripts\pcloud.ps1 doctor            # diagnostics with fixes
scripts\pcloud.ps1 logs              # follow server log
scripts\pcloud.ps1 restart
scripts\pcloud.ps1 stop
scripts\start.ps1                    # run in this window (Ctrl+C stops)
scripts\start.ps1 -Background        # run hidden, auto-restart on crash
scripts\register-service.ps1         # auto-start at sign-in   (-AtStartup: at boot, before sign-in)
scripts\unregister-service.ps1
scripts\pcloud.ps1 reset-password <user>
scripts\pcloud.ps1 disable-2fa <user>
scripts\uninstall.ps1                # never touches your storage folders
```

## Architecture

```
 Phone (WebDAV app / browser) ─┐                       ┌─ Approved folders (NTFS): D:\PersonalCloud, E:\Media …
 Laptop (browser / Claude)  ───┼─ Tailscale (WireGuard)─┤
 Claude Desktop / Code (MCP) ──┘   + `tailscale serve`  │   Home PC agent (Python, FastAPI, one process, port 8787)
                                   HTTPS, no open ports ├─ /            Next.js dashboard (static export)
                                                        ├─ /api/…       REST API (sessions + CSRF, or Bearer tokens)
                                                        ├─ /dav/…       WebDAV (WsgiDAV behind an auth/permission gate)
                                                        ├─ /mcp         MCP Streamable HTTP (Bearer AI token)
                                                        └─ background:  job queue, indexer (SQLite FTS5), scheduler
                                                           (sync, backups, trash retention), system monitor
                                                        Data dir %LOCALAPPDATA%\PersonalCloudOS: SQLite DB, logs,
                                                        recycle bin, versions, upload parts, DPAPI secrets, scripts
```

| Path | Contents |
|---|---|
| `server/pcloud/` | Agent: `main.py` (app), `auth.py`, `paths.py` (traversal-safe resolution), `webdav.py`, `mcp_server.py`, `routers/`, `services/` (fileops, index, jobs, tasks, sync, backup, approvals, monitor, remote, scheduler, local_ai) |
| `server/tests/` | 37 automated tests (security, files, uploads, WebDAV, MCP, sync, backup/restore) |
| `web/` | Next.js 16 + React 19 + TypeScript + Tailwind 4 + Radix (shadcn-style) + TanStack Query |
| `scripts/` | Install, start, service, dev, build, diagnostics, uninstall |
| `docs/` | Remote access, mobile, AI, security, troubleshooting, acceptance checklist |

**Tech choices.** SQLite (WAL) for users/metadata/jobs/index — no database server to run. WsgiDAV is a mature
WebDAV implementation (class 2, locking) so mobile clients behave; it runs behind our own gate that enforces
auth, permissions, recycle-bin deletes and versioning. The dashboard is a static export served by the agent, so
the whole system is one process, one port, same-origin (no CORS). Remote access uses Tailscale rather than
port-forwarding: nothing listens on the public internet.

## Development

```powershell
scripts\dev.ps1                       # agent on :8787 + Next dev server on :3000 (data in .dev\data)
cd server; .venv\Scripts\python -m pytest     # backend tests
cd web; npm run typecheck; npm run build      # frontend checks
```

PostgreSQL is not required; SQLite handles a personal server comfortably (the schema is plain SQL if you outgrow it).

## Documentation

- [Remote access, HTTPS, firewall & networking](docs/REMOTE-ACCESS.md)
- [Android & iPhone setup](docs/MOBILE.md)
- [Claude / MCP integration & local AI](docs/AI-INTEGRATION.md)
- [Security model](docs/SECURITY.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Acceptance criteria status](docs/ACCEPTANCE.md)
