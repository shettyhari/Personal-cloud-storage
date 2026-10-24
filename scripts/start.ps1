<#
.SYNOPSIS
  Starts the Personal Cloud OS server agent (production mode).
.PARAMETER Background
  Start hidden in the background with automatic restart on crash (supervised).
  Without it, the server runs in this window (Ctrl+C to stop).
#>
param([switch]$Background)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Server = Join-Path $Root "server"
$Py = Join-Path $Server ".venv\Scripts\python.exe"
$Pyw = Join-Path $Server ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $Py)) { throw "Not installed yet. Run scripts\install.ps1 first." }

$running = Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -match "pcloud\.cli" -and $_.CommandLine -match " run" }
if ($running) { Write-Host "Personal Cloud OS is already running (PID $($running[0].ProcessId))."; exit 0 }

if ($Background) {
    Start-Process -FilePath $Pyw -ArgumentList "-m", "pcloud.cli", "run", "--supervise" -WorkingDirectory $Server -WindowStyle Hidden
    Write-Host "Started in the background. Logs: %LOCALAPPDATA%\PersonalCloudOS\logs\server.log"
} else {
    Push-Location $Server
    try { & $Py -m pcloud.cli run } finally { Pop-Location }
}
