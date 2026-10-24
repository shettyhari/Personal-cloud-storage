<#
.SYNOPSIS
  Registers Personal Cloud OS to start automatically with Windows.

.DESCRIPTION
  Default: a Scheduled Task that starts the supervised agent when YOU sign in, running with
  your normal (non-administrator) rights. No admin rights or stored password needed.
  The agent keeps running when the browser is closed; Task Scheduler restarts it if it exits
  unexpectedly, and the built-in supervisor restarts the server process after crashes.

  -AtStartup: start at boot, before anyone signs in (Windows asks for your account password once,
  and creating such a task requires an elevated PowerShell). Use this for a headless home server.
#>
param([switch]$AtStartup)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Server = Join-Path $Root "server"
$Pyw = Join-Path $Server ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $Pyw)) { throw "Run scripts\install.ps1 first." }
$TaskName = "PersonalCloudOS"
$user = "$env:USERDOMAIN\$env:USERNAME"

$action = New-ScheduledTaskAction -Execute $Pyw -Argument "-m pcloud.cli run --supervise" -WorkingDirectory $Server
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew

if ($AtStartup) {
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $cred = Get-Credential -UserName $user -Message "Your Windows password (stored by Task Scheduler, not by Personal Cloud OS)"
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel Limited -Force | Out-Null
} else {
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
}
Write-Host "Registered scheduled task '$TaskName' ($(if ($AtStartup) {'at startup'} else {'at sign-in'}), standard user rights)." -ForegroundColor Green
Write-Host "Tip: in Windows Settings > System > Power, set 'Sleep' to Never (when plugged in) so the server stays reachable."
