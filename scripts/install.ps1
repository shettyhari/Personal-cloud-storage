<#
.SYNOPSIS
  Installs Personal Cloud OS on this Windows PC.

.DESCRIPTION
  1. Finds (or offers to install) Python 3.11+ and Node.js 20+
  2. Creates the Python virtual environment and installs server dependencies
  3. Builds the web dashboard
  4. Creates .env from .env.example
  5. Optionally registers auto-start at Windows sign-in (no administrator rights needed)
  6. Starts the server and opens the setup wizard

  Run from the project folder:
      powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#>
param(
    [switch]$NoAutoStart,
    [switch]$NoBrowser,
    [switch]$SkipWebBuild
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Server = Join-Path $Root "server"
$Web = Join-Path $Root "web"
$Venv = Join-Path $Server ".venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"

function Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "    $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }
function Invoke-Py([string[]]$cmd, [string[]]$arguments) {
    $exe = $cmd[0]
    $pre = @()
    if ($cmd.Length -gt 1) { $pre = $cmd[1..($cmd.Length - 1)] }
    & $exe @pre @arguments
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($isAdmin) {
    Warn "You are running as Administrator. Personal Cloud OS is designed to run as a normal user (least privilege)."
    Warn "Close this window and run the installer from a normal PowerShell window unless you have a specific reason."
    $ans = Read-Host "Continue anyway? (y/N)"
    if ($ans -ne "y") { exit 1 }
}

# ------------------------------------------------------------------ Python
Step "Checking Python 3.11+"
$py = $null
$uv = Get-Command uv -ErrorAction SilentlyContinue
foreach ($cand in @("py -3.12", "py -3.11", "python3", "python")) {
    try {
        $parts = $cand.Split(" ")
        $ver = Invoke-Py $parts @("-c", "import sys; print('%d.%d' % sys.version_info[:2]); import venv, ensurepip") 2>$null
        if ($LASTEXITCODE -eq 0 -and [version]$ver -ge [version]"3.11") { $py = $parts; break }
    } catch { }
}
if (-not $py -and -not $uv) {
    Warn "Python 3.11+ was not found."
    $ans = Read-Host "Install Python 3.12 now with winget? (Y/n)"
    if ($ans -ne "n") {
        winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements
        Warn "Python installed. Close this window, open a new PowerShell window and run the installer again."
        exit 0
    }
    throw "Python 3.11 or newer is required: https://www.python.org/downloads/windows/"
}

Step "Creating virtual environment"
if (-not (Test-Path $VenvPy)) {
    if ($py) { Invoke-Py $py @("-m", "venv", $Venv) }
    else { & $uv.Source venv $Venv --python 3.11 }
}
Ok $Venv

Step "Installing server dependencies"
if ($uv -and -not $py) { & $uv.Source pip install --python $VenvPy -r (Join-Path $Server "requirements.txt") }
else {
    & $VenvPy -m pip --version *> $null
    if ($LASTEXITCODE -ne 0) { & $VenvPy -m ensurepip --upgrade | Out-Null }
    & $VenvPy -m pip install --upgrade pip --quiet
    & $VenvPy -m pip install -r (Join-Path $Server "requirements.txt") --quiet
}
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
Ok "Server dependencies installed"

# --------------------------------------------------------------- Web build
if (-not $SkipWebBuild) {
    Step "Building the web dashboard"
    $node = Get-Command node -ErrorAction SilentlyContinue
    if (-not $node) {
        if (Test-Path (Join-Path $Web "out\index.html")) {
            Warn "Node.js not found - using the existing dashboard build in web\out."
        } else {
            Warn "Node.js 20+ is required to build the dashboard."
            $ans = Read-Host "Install Node.js LTS now with winget? (Y/n)"
            if ($ans -ne "n") {
                winget install -e --id OpenJS.NodeJS.LTS --accept-package-agreements
                Warn "Node.js installed. Open a new PowerShell window and run the installer again."
                exit 0
            }
            throw "Node.js is required to build the dashboard"
        }
    } else {
        Push-Location $Web
        try {
            if (Test-Path "package-lock.json") { npm ci --no-audit --no-fund } else { npm install --no-audit --no-fund }
            if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
            npm run build
            if ($LASTEXITCODE -ne 0) { throw "Dashboard build failed" }
        } finally { Pop-Location }
        Ok "Dashboard built into web\out"
    }
}

# ---------------------------------------------------------------- .env
Step "Configuration"
$envFile = Join-Path $Root ".env"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $Root ".env.example") $envFile
    Ok "Created .env (defaults: listen on 127.0.0.1:8787, data in %LOCALAPPDATA%\PersonalCloudOS)"
} else { Ok ".env already exists - left unchanged" }

# ---------------------------------------------------------------- service
if (-not $NoAutoStart) {
    Step "Auto-start"
    $ans = Read-Host "Start Personal Cloud OS automatically when you sign in to Windows? (Y/n)"
    if ($ans -ne "n") { & (Join-Path $PSScriptRoot "register-service.ps1") }
}

# ---------------------------------------------------------------- start
Step "Starting the server"
& (Join-Path $PSScriptRoot "start.ps1") -Background
Start-Sleep -Seconds 4
$port = 8787
$envPort = Select-String -Path $envFile -Pattern '^\s*PCLOUD_PORT\s*=\s*(\d+)' -ErrorAction SilentlyContinue
if ($envPort) { $port = [int]$envPort.Matches[0].Groups[1].Value }
Push-Location $Server
try { $code = (& $VenvPy -m pcloud.cli setup-code 2>$null | Select-Object -Last 1) } finally { Pop-Location }
$url = "http://127.0.0.1:$port/setup"
if ($code -and $code -notmatch "already") { $url = "$url`?code=$code" }
Write-Host ""
Write-Host "Personal Cloud OS is running." -ForegroundColor Green
Write-Host "  Dashboard:  http://127.0.0.1:$port"
if ($code -and $code -notmatch "already") { Write-Host "  Setup code: $code" }
Write-Host "  Next: complete the setup wizard, then follow docs\REMOTE-ACCESS.md to reach it from your phone."
if (-not $NoBrowser) { Start-Process $url }
