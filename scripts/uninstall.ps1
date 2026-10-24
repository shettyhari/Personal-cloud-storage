<#
.SYNOPSIS
  Safely uninstalls Personal Cloud OS.

.DESCRIPTION
  - Stops the server and removes auto-start.
  - NEVER touches your storage folders or backup destinations - your files stay exactly where they are.
  - Optionally deletes the server's own data folder (database, logs, recycle bin, version history,
    stored backup keys). You are asked before anything is deleted, and items still in the recycle bin
    or version history are listed first.
  - Remove Tailscale's "serve" config yourself if you set it up:  tailscale serve reset
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root "server\.venv\Scripts\python.exe"

Write-Host "Stopping the server..."
if (Test-Path $Py) { Push-Location (Join-Path $Root "server"); & $Py -m pcloud.cli stop; Pop-Location }
& (Join-Path $PSScriptRoot "unregister-service.ps1")

$data = $env:PCLOUD_DATA_DIR
if (-not $data) { $data = Join-Path $env:LOCALAPPDATA "PersonalCloudOS" }
if (Test-Path $data) {
    $trash = Get-ChildItem (Join-Path $data "trash") -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum
    $vers = Get-ChildItem (Join-Path $data "versions") -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum
    Write-Host ""
    Write-Host "Server data folder: $data"
    Write-Host ("  Recycle bin:     {0} files, {1:N1} MB" -f $trash.Count, ($trash.Sum / 1MB))
    Write-Host ("  Version history: {0} files, {1:N1} MB" -f $vers.Count, ($vers.Sum / 1MB))
    Write-Host "  Also contains: accounts, settings, activity log, and the keys for encrypted backups." -ForegroundColor Yellow
    Write-Host "  Make sure you saved your backup recovery keys before deleting it." -ForegroundColor Yellow
    $ans = Read-Host "Delete the server data folder? Your storage folders are NOT affected. Type DELETE to confirm"
    if ($ans -eq "DELETE") { Remove-Item -Recurse -Force $data; Write-Host "Data folder deleted." }
    else { Write-Host "Kept $data" }
}
Write-Host ""
Write-Host "Uninstalled. You can now delete the project folder: $Root"
