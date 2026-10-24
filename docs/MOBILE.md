# Phones and tablets

## Quickest: the Connect Phone dashboard

Dashboard → **Connect Phone** shows a live checklist (server, Tailscale on the PC, HTTPS via `tailscale serve`,
saved address, phone on your tailnet, phone signed in, file app connected) with a fix button for each step.
When steps 1–5 are green, click **Show QR code** and scan it with the phone's camera: the phone opens the
dashboard already signed in. Codes are single-use, expire after 10 minutes, and only work from your own
Tailscale devices.

Your PC's storage is exposed over **WebDAV** at `https://<pc-name>.<tailnet>.ts.net/dav/` (see
[REMOTE-ACCESS.md](REMOTE-ACCESS.md)). Each approved storage location with "Mobile / WebDAV" enabled appears as a
top-level folder.

**Credentials:** your username + an **app password** (Dashboard → Mobile Access → *New app password*). Your real
account password is never accepted over WebDAV. Create one app password per device; revoke it if the device is
lost. Mark it *read-only* for devices that should only browse.

Supported operations: browse (PROPFIND), open/download (GET, with range requests for video seeking), upload (PUT),
create folder (MKCOL), rename/move (MOVE), copy (COPY), delete (DELETE → **recycle bin**), and LOCK/UNLOCK (used by
Office apps, macOS and Windows clients). Overwriting a file keeps the previous version (if versioning is on).

## Android

Android has no built-in WebDAV client, so use a file manager that supports it:

| App | WebDAV browse/edit | Shows in system file picker (SAF) | Notes |
|---|---|---|---|
| Solid Explorer | yes | yes (enable in settings) | polished, paid after trial |
| CX File Explorer | yes | partial | free |
| Total Commander + WebDAV plugin | yes | no | free |
| Material Files | yes | no | open source |
| Round Sync (rclone) | yes | yes | open source, power users |
| FolderSync | sync only | — | scheduled two-way sync of phone folders (e.g. Camera) |

Steps (Solid Explorer example):

1. Install Tailscale on the phone, sign in, turn it on.
2. Solid Explorer → **+** → *New cloud connection* → **WebDAV**.
3. Host: `<pc-name>.<tailnet>.ts.net`, check **Use HTTPS**, port 443, path `/dav/`.
4. Username + app password. Save.
5. Your locations appear as folders. Long-press for rename/move/delete; *Download* for offline copies.

To make the PC appear when other apps open the **system file picker** (attach to email, save from browser), use an
app that registers a document provider (Solid Explorer, Round Sync). Support depends on the app and Android version.

## iPhone & iPad

Apple's Files app natively connects to **SMB** servers only ("Connect to Server" does not speak WebDAV). Supported
options:

1. **WebDAV app (recommended)** — *Documents by Readdle*, *FE File Explorer*, *Owlfiles* or *WebDAV Navigator*.
   Add a WebDAV connection with URL `https://<pc-name>.<tailnet>.ts.net/dav/`, username and app password.
   Several of these apps expose their connections as a location inside the Files app. HTTPS is required (use
   `tailscale serve`).
2. **Native Files app over SMB** — share the folders with Windows file sharing (folder → Properties → Sharing),
   then in Files: *⋯ → Connect to Server* → `smb://<pc-name>.<tailnet>.ts.net`, sign in with your Windows account.
   This bypasses Personal Cloud OS permissions/recycle bin/audit (it is plain Windows sharing), so only share what
   you intend to.
3. **Web app** — open the HTTPS address in Safari → Share → *Add to Home Screen*. Upload from Photos/Files,
   preview images/PDF/video, download.

## Offline access

WebDAV apps download files on demand. Mark files/folders "available offline" in the app, or use a sync app
(FolderSync on Android, the Readdle Documents sync feature on iOS) for folders you need without connectivity.

## Windows / macOS laptops

- **Windows:** File Explorer → *This PC* → *Map network drive* → *Connect to a Web site…* →
  `https://<pc-name>.<tailnet>.ts.net/dav/`. (HTTPS required for Basic auth.) Or use RaiDrive / Cyberduck / rclone.
- **macOS:** Finder → *Go → Connect to Server* → `https://<pc-name>.<tailnet>.ts.net/dav/`.
