# Troubleshooting

Start with:

```powershell
scripts\pcloud.ps1 doctor     # checks database, folders, dashboard build, Tailscale, HTTPS, privileges, port
scripts\pcloud.ps1 status
scripts\pcloud.ps1 logs       # %LOCALAPPDATA%\PersonalCloudOS\logs\server.log
```

Task-specific logs: Dashboard → Background Tasks → *Details*. Security-relevant failures: Activity Logs (filter
status = failed / denied) and Remote Computer → *Recent errors*.

| Symptom | Cause / fix |
|---|---|
| Dashboard says "Your home PC is not reachable" | PC off/asleep, agent not running (`scripts\start.ps1 -Background`), or Tailscale disconnected on either device. |
| "dashboard has not been built" | `scripts\build-web.ps1` (needs Node.js). |
| Where is the setup code? | `scripts\pcloud.ps1 setup-code`, or `%LOCALAPPDATA%\PersonalCloudOS\setup-token.txt`. |
| 403 "Access from this network is not allowed" | Client IP is not loopback/Tailscale. Use the Tailscale address, or enable LAN / add a CIDR in Settings → Network. |
| Port 8787 in use | Set `PCLOUD_PORT` in `.env`, re-run `tailscale serve --bg http://127.0.0.1:<port>`. |
| Forgot password | `scripts\pcloud.ps1 reset-password <user>` on the PC. |
| Lost authenticator | `scripts\pcloud.ps1 disable-2fa <user>`. |
| Account locked | Wait 15 minutes, or reset the password via CLI (clears the lock). |
| WebDAV app: 401 | Use an **app password**, not your account password; username must match the token owner; token not revoked/expired. |
| iPhone/Windows WebDAV refuses to sign in | They need HTTPS — set up `tailscale serve` and use the `https://…ts.net/dav/` URL. |
| WebDAV: storage location missing | Location has "Mobile / WebDAV" off, the user lacks permission, or the drive is disconnected. |
| WebDAV 403 on upload | Location or app password is read-only. |
| Storage location "unavailable" | External drive disconnected or letter changed. Reconnect, or remove and re-add the location. |
| Windows denied access | The agent runs as your user; grant your account access to that folder (folder → Properties → Security). |
| Search misses a new file | Index refreshes every 30 min (Settings). Drives & Storage → *Re-index*. Files uploaded via the app/WebDAV are indexed immediately. Content search covers text, PDF, DOCX, XLSX ≤ 10 MB (configurable); scanned PDFs have no text. |
| Upload stuck/failed | Uploads retry automatically and resume from the last chunk, even after reloading the page (choose the same file again). Check free space. |
| Claude: "unauthorized" | Token wrong/revoked, or MCP disabled in Settings → AI. |
| Claude can't see a folder | Enable "AI can read" on that location. |
| Claude action "pending" | Approve it in AI Workspace → Pending approvals. |
| `npx mcp-remote` fails | Install Node.js on the computer running Claude Desktop; check that computer is on Tailscale. |
| Backup failed: destination | Backup drive not connected — the plan shows "drive not connected". |
| Restore on a new PC | Install, re-create the plan pointing at the same destination (encrypted: the new PC needs your recovery key — enter it in the restore dialog). |
| Backup verify failed | Possible disk corruption at the destination. Run a new backup to a healthy drive; check drive health (Drives & Storage). |
| Sync conflicts | Both sides changed: both copies are kept (`… (conflict …)`); resolve in Sync & Backup. Deleted/overwritten files from sync are in `<data dir>\sync-archive`. |
| Server keeps restarting | `scripts\pcloud.ps1 logs`. Run `scripts\start.ps1` (foreground) to see the error. |
| Stop auto-start | `scripts\unregister-service.ps1`. |

## Recovery after restart / network interruption

- The agent is restarted by its supervisor (crash) and by Task Scheduler (exit); it starts at sign-in (or boot).
- Interrupted uploads resume from the last received byte; partial uploads are kept 7 days.
- Jobs running at shutdown are marked *interrupted*; index scans and syncs resume automatically.
- Sync records per-file state as it goes, so an interrupted sync continues where it stopped.
- The SQLite database uses WAL mode and is crash-safe.

## Reset everything (keeps your files)

```powershell
scripts\pcloud.ps1 stop
Remove-Item -Recurse "$env:LOCALAPPDATA\PersonalCloudOS"   # accounts, settings, index, trash, versions, backup keys!
scripts\start.ps1 -Background
```
