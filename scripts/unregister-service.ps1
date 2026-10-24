# Removes the auto-start scheduled task (does not stop a running server; use pcloud.ps1 stop).
$ErrorActionPreference = "Stop"
if (Get-ScheduledTask -TaskName "PersonalCloudOS" -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName "PersonalCloudOS" -Confirm:$false
    Write-Host "Auto-start removed."
} else { Write-Host "Auto-start was not registered." }
