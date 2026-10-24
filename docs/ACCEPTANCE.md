# Acceptance criteria — status

"Verified" = covered by the automated suite (`server/tests`, 37 tests) and/or exercised against the running server.
"Needs your setup" = implemented, but depends on something only you can do on your devices/accounts.

| # | Criterion | Status | How it is met / how to check |
|---|---|---|---|
| 1 | Open the app on the home PC and access authorized local files | Verified | `http://127.0.0.1:8787` → My Files. Tested end-to-end in a browser. |
| 2 | Access the same files remotely through a secured connection | Needs your setup | Install Tailscale + `tailscale serve` ([REMOTE-ACCESS.md](REMOTE-ACCESS.md)). IP allow-list for Tailscale ranges is tested. |
| 3 | Upload, download, rename, move, delete per permissions | Verified | `test_files.py`, `test_user_permissions`. |
| 4 | Android file manager via WebDAV | Verified (protocol) / Needs your phone | Full WebDAV cycle tested (PROPFIND/GET/PUT/MKCOL/COPY/MOVE/DELETE/LOCK) incl. 20 MB file on the live server. Connect an app per [MOBILE.md](MOBILE.md). |
| 5 | iPhone/iPad via a supported method | Needs your phone | WebDAV apps over HTTPS, SMB, or the web app ([MOBILE.md](MOBILE.md)). |
| 6 | Windows service runs independently of the browser | Implemented | Supervised background process + Scheduled Task (`register-service.ps1`), restart on failure. |
| 7 | Monitor CPU, RAM, disk, availability | Verified | Overview + Remote Computer pages (live psutil data), health endpoint, offline banner. |
| 8 | Claude can search and read authorized documents | Verified (protocol) / Needs your Claude | MCP over HTTP tested on the live server (initialize, search, analyze); connect Claude per [AI-INTEGRATION.md](AI-INTEGRATION.md). |
| 9 | Destructive AI actions require authorization | Verified | `test_mcp_tools_and_approvals` (delete/rename/overwrite pending until approved; reject path). |
| 10 | Files outside approved directories inaccessible | Verified | Traversal, ADS, device names, junction escape, WebDAV traversal, MCP traversal, forbidden roots tests. |
| 11 | Graceful recovery after restart/network interruption | Verified (partly) | Resumable uploads + job recovery tested; supervisor/Task Scheduler restart. |
| 12 | Transfers handle errors and resume large uploads | Verified | Chunk offset mismatch, resume, checksum mismatch tests; client auto-retry with backoff. |
| 13 | Backup and restoration can be tested | Verified | Encrypted backup → verify → ransomware simulation → restore older snapshot with recovery key; tamper detection. |
| 14 | Installation and troubleshooting docs | Done | README, docs/*.md, `install.ps1`, `pcloud.ps1 doctor`. |

## Explicitly out of scope / limitations
- No custom remote-desktop protocol: integrates RustDesk / RDP (status detection, `.rdp` file, `rustdesk://` link).
- No native iOS/Android app; native file managers via WebDAV + responsive web app.
- Claude.ai web connectors require a public endpoint + OAuth; not enabled by design (private-only server).
- CPU temperatures are not exposed by Windows without vendor tools; GPU stats require NVIDIA `nvidia-smi`.
- Format conversion covers xlsx↔csv, tsv→csv, json→csv, docx/pdf→txt (no image/video transcoding).
- Device sync uses WebDAV sync apps (e.g. FolderSync); server-side sync covers PC folder ↔ folder/drive/NAS.
