# Rebuilds the dashboard into web\out (served by the agent). Restart not required; reload the page.
$ErrorActionPreference = "Stop"
Push-Location (Join-Path (Split-Path -Parent $PSScriptRoot) "web")
try {
    if (-not (Test-Path node_modules)) { npm install --no-audit --no-fund }
    npm run build
} finally { Pop-Location }
