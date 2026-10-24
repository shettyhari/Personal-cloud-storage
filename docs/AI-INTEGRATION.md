# Claude & AI integration (MCP)

Personal Cloud OS includes a [Model Context Protocol](https://modelcontextprotocol.io) server built with the
official `mcp` Python SDK. Claude has **no access by default**: you create an AI token, paste it into Claude, and
you can disable or revoke it any time.

## 1. Choose what AI may see

Dashboard → Drives & Storage → per location:

- **AI can read** — list, search, read, analyze files there.
- **AI can write** — additionally create folders and save *new* documents.

Locations without "AI can read" are invisible to the AI (not even listed).

## 2. Create a token

Dashboard → AI Workspace → **Connect Claude** → name it (e.g. "Claude Desktop – laptop") → copy the token
(shown once).

## 3. Connect

**Claude Desktop on another computer (over Tailscale).** Requires Node.js on that computer. *Settings → Developer →
Edit Config*, then add:

```json
{
  "mcpServers": {
    "personal-cloud": {
      "command": "npx",
      "args": ["-y", "mcp-remote@latest", "https://<pc-name>.<tailnet>.ts.net/mcp",
               "--header", "Authorization:${PCLOUD_AUTH}"],
      "env": { "PCLOUD_AUTH": "Bearer <your MCP token>" }
    }
  }
}
```

**Claude Desktop on the home PC itself (stdio, no network):**

```json
{
  "mcpServers": {
    "personal-cloud": {
      "command": "C:\\path\\to\\Home PC Server\\server\\.venv\\Scripts\\python.exe",
      "args": ["-m", "pcloud.cli", "mcp-stdio"],
      "env": { "PCLOUD_MCP_TOKEN": "<your MCP token>",
               "PYTHONPATH": "C:\\path\\to\\Home PC Server\\server" }
    }
  }
}
```

**Claude Code:**

```powershell
claude mcp add --transport http personal-cloud https://<pc-name>.<tailnet>.ts.net/mcp --header "Authorization: Bearer <token>"
```

The AI Workspace page shows these snippets pre-filled with your server address.

**Claude.ai web/mobile custom connectors** need a publicly reachable URL with OAuth. This project intentionally
keeps the server private, so that path is not supported out of the box.

## Tools exposed

| Tool | What it does | Approval |
|---|---|---|
| `list_locations` | Locations the AI can access and their permissions | – |
| `list_files` | Folder listing | – |
| `search_files` | Filename + full-text (PDF/DOCX/XLSX/text) search with extension, folder and date filters | – |
| `get_file_info` | Metadata, optional SHA-256 | – |
| `read_document` | Extracted text (paged with `offset`) | – |
| `analyze_spreadsheet` | Columns, types, sums/min/max/mean, sample rows | – |
| `find_duplicates` | Identical files by content hash | – |
| `propose_organization` | Suggested folder structure (by type or year/month) — no changes | – |
| `storage_overview` | Totals, file types, recent files | – |
| `create_folder`, `save_document` | Create folders / new files in "AI can write" locations | – (overwrite → approval) |
| `move_file`, `copy_file`, `rename_file`, `delete_file`, `organize_files` | Request changes | **always** |
| `get_approval_status` | Check what the owner decided | – |
| `run_task`, `get_job_status` | Approved background jobs (inventory, duplicates, checksums, compress, batch text extraction, convert, directory search) | – |
| `list_scripts`, `run_script` | Scripts the owner registered with "AI may request" | **always** |
| `local_model_summarize` | Summarize with your local model (Ollama) — content never leaves the PC | – |

Approvals appear in Dashboard → AI Workspace → *Pending approvals* (and as a badge in the sidebar). Approved
actions execute immediately and are logged with who approved them. Requests expire after 72 h (configurable).
Deletes go to the recycle bin; overwrites keep the previous version.

### Example

> "Find all marketing reports from October, compare their performance, create a summary, and save the report in
> my Marketing folder."

Claude calls `search_files(query="marketing report", modified_after="2026-10-01", modified_before="2026-10-31")`,
reads each hit with `read_document` / `analyze_spreadsheet`, writes the comparison, and calls
`save_document(location="Cloud", path="Marketing/October summary.md", content=...)`. Every step is in the AI
activity log.

## Safety guarantees

- Bearer token required on every request; tokens are stored hashed and are revocable.
- Settings → AI → *Enable MCP endpoint* off = all AI access blocked instantly.
- Paths are resolved through the same traversal-safe resolver as everything else.
- No tool runs shell commands; scripts must be pre-registered by an admin and each run needs approval.
- Per-request read cap (default 200k characters).

## Local AI (optional)

Install [Ollama](https://ollama.com), run `ollama pull llama3.2`, then Settings → AI → *Enable local AI*. The
dashboard's AI Workspace gets a "Summarize locally" panel, and the AI gets `local_model_summarize`. The provider URL
must be on localhost, so document contents are processed on this PC only.
