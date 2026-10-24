<#
.SYNOPSIS
  Personal Cloud OS command-line helper.
.EXAMPLE
  scripts\pcloud.ps1 doctor              # diagnostics
  scripts\pcloud.ps1 status              # is it running?
  scripts\pcloud.ps1 stop                # stop the server (and its supervisor)
  scripts\pcloud.ps1 restart
  scripts\pcloud.ps1 setup-code          # show first-run setup code
  scripts\pcloud.ps1 reset-password admin
  scripts\pcloud.ps1 disable-2fa admin
  scripts\pcloud.ps1 logs                # follow the server log
#>
param([Parameter(Position = 0)][string]$Command = "help", [Parameter(Position = 1, ValueFromRemainingArguments)]$Rest)
$Root = Split-Path -Parent $PSScriptRoot
$Server = Join-Path $Root "server"
$Py = Join-Path $Server ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { Write-Error "Not installed. Run scripts\install.ps1"; exit 1 }

function DataDir {
    $d = $env:PCLOUD_DATA_DIR
    $envFile = Join-Path $Root ".env"
    if (-not $d -and (Test-Path $envFile)) {
        $m = Select-String -Path $envFile -Pattern '^\s*PCLOUD_DATA_DIR\s*=\s*(.+)$'
        if ($m) { $d = $m.Matches[0].Groups[1].Value.Trim('"', "'", ' ') }
    }
    if (-not $d) { $d = Join-Path $env:LOCALAPPDATA "PersonalCloudOS" }
    return $d
}

Push-Location $Server
try {
    switch ($Command) {
        "status" {
            $p = Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -match "pcloud\.cli" -and $_.CommandLine -match " run" }
            if ($p) { Write-Host "Running: $($p.Count) process(es)"; try { (Invoke-RestMethod http://127.0.0.1:8787/api/health -TimeoutSec 3) | Format-List } catch { Write-Warning "Process found but health check failed" } }
            else { Write-Host "Not running" }
        }
        "restart" { & $Py -m pcloud.cli stop; Start-Sleep 2; & (Join-Path $PSScriptRoot "start.ps1") -Background }
        "logs" { Get-Content (Join-Path (DataDir) "logs\server.log") -Tail 50 -Wait }
        "help" { Get-Help $MyInvocation.MyCommand.Path -Detailed }
        default { & $Py -m pcloud.cli $Command @Rest; exit $LASTEXITCODE }
    }
} finally { Pop-Location }
