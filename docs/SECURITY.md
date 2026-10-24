# Security model

## Network exposure
- Binds to `127.0.0.1` by default. Remote access is via Tailscale (WireGuard); nothing is exposed to the internet.
- Outermost middleware rejects any client IP that is not loopback, Tailscale (100.64.0.0/10, fd7a:115c:a1e0::/48),
  the LAN (only if enabled) or an explicitly added CIDR. Global per-IP rate limit (3000 req/min).
- Security headers on every response (CSP for the dashboard, nosniff, frame-ancestors, referrer policy; HSTS when
  `PCLOUD_SECURE_COOKIES=true`).
- No CORS by default (dashboard is same-origin).

## Authentication
- First admin can only be created with a one-time setup code printed on the PC (prevents a LAN/tailnet device from
  claiming the server).
- Passwords: scrypt (N=2^15, r=8, p=1), minimum strength rules; constant-time behavior for unknown users.
- Login rate limits per IP and per username; account lockout for 15 min after 8 failures.
- Optional TOTP two-factor authentication (RFC 6238); CLI recovery on the PC.
- Sessions: random 256-bit tokens, stored as SHA-256 hashes; HttpOnly + SameSite=Strict cookie; absolute expiry
  (12 h) and idle expiry (configurable); password change revokes other sessions; manual revoke per session.
- CSRF: every cookie-authenticated state-changing request needs the per-session `X-CSRF-Token` header.
- Device/WebDAV/AI tokens: 256-bit, stored hashed, per-device, optional expiry and read-only, revocable; WebDAV never
  accepts the account password; administrative endpoints refuse token auth.

## Authorization
- Roles: admin, user, read-only. Non-admin users only see storage locations granted to them (read or read/write).
- Per-location flags: read-only, WebDAV, AI read, AI write, versioning, content indexing.
- AI (MCP) tokens are limited to AI-enabled locations; destructive/bulk actions need human approval.

## File access safety
- Every path goes through `paths.resolve()`: rejects absolute paths, drive letters, `..`, NUL/control characters,
  NTFS alternate data streams (`:`), reserved device names (CON, NUL…), trailing dots/spaces, then resolves
  symlinks/junctions with `realpath` and verifies the result is inside the approved root.
- Whole drives, Windows/Program Files/ProgramData/AppData, the full user profile and the server's data folder
  cannot be approved; overlapping roots are refused.
- Archive extraction blocks zip-slip and uses tarfile's `data` filter.
- Inline previews of HTML/SVG/XML are served as text/plain with a sandbox CSP (no stored XSS via uploaded files).
- Upload size limit (`PCLOUD_MAX_UPLOAD_BYTES`), free-space check, optional SHA-256 verification.
- Deletes go to the recycle bin (permanent delete is admin-only); overwrites keep versions.

## Execution
- No shell, PowerShell, SSH or terminal is exposed. Background jobs are a fixed set of Python implementations.
- Scripts must live in `<data dir>\scripts`, be registered by an admin with fixed arguments, run without a shell
  (except `.bat/.cmd` via `cmd /c` with admin-fixed args), with a time limit and full output logging.
- The agent is designed to run as a standard user; `doctor` warns if elevated. The scheduled task uses
  `RunLevel Limited`.
- Secure shutdown/restart requires the admin password.

## Secrets
- No secrets in source or frontend. Backup encryption keys are protected with Windows DPAPI (user scope) under
  `<data dir>\secrets`. Recovery keys typed for restores are held in memory only.
- `.env` contains no secrets by design.

## Audit
- Activity log of logins (success/failure), file operations, WebDAV writes, token/user/settings changes, AI tool
  calls and approvals, jobs. Exportable to CSV; retained 1 year.

## Backups vs. sync
- Sync propagates deletions/corruption (archived copies are kept, but it is not a backup).
- Backups are immutable dated snapshots, AES-256-GCM (per-segment authenticated), content-deduplicated, with
  verify and restore-to-new-folder. Keep the recovery key offline.

## Known limitations
- Tailscale's own account security (SSO, 2FA, device approval) is part of your perimeter — enable device approval
  in the Tailscale admin console.
- SMB sharing for iOS Files (optional) is governed by Windows permissions, not by this app.
- The AI can read everything in AI-readable locations; only enable it where that is acceptable.
