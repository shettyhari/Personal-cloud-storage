<#
.SYNOPSIS
  Local-only development mode: API server with auto-reload docs + Next.js dev server with hot reload.
  Dashboard: http://localhost:3000 (proxies /api and /dav to the agent on 127.0.0.1:8787)
  Data lives in .dev\data so your real installation is untouched.
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Server = Join-Path $Root "server"
$Py = Join-Path $Server ".venv\Scripts\python.exe"
$env:PCLOUD_DEV = "1"
$env:PCLOUD_DATA_DIR = Join-Path $Root ".dev\data"
$env:PCLOUD_HOST = "127.0.0.1"
New-Item -ItemType Directory -Force (Join-Path $Root ".dev\data") | Out-Null
$api = Start-Process -FilePath $Py -ArgumentList "-m", "pcloud.cli", "run" -WorkingDirectory $Server -PassThru -NoNewWindow
try {
    Push-Location (Join-Path $Root "web")
    if (-not (Test-Path node_modules)) { npm install --no-audit --no-fund }
    npm run dev
} finally {
    Pop-Location
    if ($api -and -not $api.HasExited) { Stop-Process -Id $api.Id -Force }
}
