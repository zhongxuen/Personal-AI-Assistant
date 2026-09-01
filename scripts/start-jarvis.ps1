<#
.SYNOPSIS
    One command to run JARVIS locally: backend + frontend + browser.

.DESCRIPTION
    Local JARVIS is two processes, not one app:
      * the FastAPI backend  (backend/, uvicorn, http://127.0.0.1:8000)
      * the Vite dev server  (frontend/, http://127.0.0.1:5173, proxies /api -> :8000)

    This script launches each in its own console window, waits for both ports to come
    up, then opens the dashboard in your default browser. Nothing here needs an editor
    open -- double-click scripts\start-jarvis.cmd, or run this from any terminal.

    Already-running services are detected by their listening port and reused rather
    than started twice, so re-running this is safe.

    To stop JARVIS: close the two console windows it opened (or Ctrl+C in each).

.PARAMETER BackendOnly
    Start just the backend. Useful when you only want the API / Discord bot
    (the bot runs inside the backend process -- see scripts\start-discord-bot.ps1).

.PARAMETER NoBrowser
    Don't open the dashboard in a browser once things are up.

.PARAMETER Reload
    Run uvicorn with --reload (auto-restart on code edits). Off by default: the
    everyday "just use the assistant" path doesn't need a file watcher.

.EXAMPLE
    .\scripts\start-jarvis.ps1
    .\scripts\start-jarvis.ps1 -BackendOnly
    .\scripts\start-jarvis.ps1 -Reload
#>

[CmdletBinding()]
param(
    [switch]$BackendOnly,
    [switch]$NoBrowser,
    [switch]$Reload
)

$ErrorActionPreference = "Stop"

$RepoRoot     = Split-Path -Parent $PSScriptRoot
$VenvPython   = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$BackendDir   = Join-Path $RepoRoot "backend"
$FrontendDir  = Join-Path $RepoRoot "frontend"
$BackendPort  = 8000
$FrontendPort = 5173
$FrontendUrl  = "http://127.0.0.1:$FrontendPort"

Write-Host "== Starting JARVIS ==" -ForegroundColor Cyan

# --- prerequisites ----------------------------------------------------------
if (-not (Test-Path $VenvPython)) {
    Write-Error @"
No Python venv found at .venv\Scripts\python.exe.
Set one up first (from repo root):
    python -m venv .venv
    .venv\Scripts\activate
    pip install -r backend\requirements-dev.txt
"@
}

if (-not $BackendOnly -and -not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
    Write-Error @"
frontend\node_modules is missing. Install the frontend deps once:
    cd frontend
    npm install
"@
}

# --- helpers ----------------------------------------------------------------
function Test-PortListening([int]$Port) {
    [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

# pwsh 7 if it's installed, else Windows PowerShell -- both are fine here.
$Shell = if (Get-Command pwsh -ErrorAction SilentlyContinue) { "pwsh" } else { "powershell" }

# Each service gets its own window, launched with -NoExit so that if it dies on
# startup the error stays on screen instead of vanishing with the window.
function Start-ServiceWindow([string]$Title, [string]$WorkDir, [string]$Command) {
    $inner = "`$Host.UI.RawUI.WindowTitle = '$Title'; Set-Location '$WorkDir'; $Command"
    Start-Process -FilePath $Shell -ArgumentList '-NoExit', '-NoProfile', '-Command', $inner | Out-Null
}

function Wait-ForPort([int]$Port, [string]$Label, [int]$TimeoutSeconds = 60) {
    Write-Host "Waiting for $Label on port $Port..." -NoNewline -ForegroundColor DarkGray
    for ($i = 0; $i -lt ($TimeoutSeconds * 2); $i++) {
        if (Test-PortListening $Port) {
            Write-Host " up." -ForegroundColor Green
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    Write-Host ""
    Write-Warning "$Label did not come up within $TimeoutSeconds s -- check its console window."
    return $false
}

# --- backend ----------------------------------------------------------------
# Invoked as `python -m uvicorn` rather than the bare `uvicorn` command for the same
# reason as start-discord-bot.ps1: the venv's uvicorn.exe shim has been seen to exit 1
# silently in some terminal hosts, while going through python.exe always works.
if (Test-PortListening $BackendPort) {
    Write-Host "Backend already listening on $BackendPort -- reusing it." -ForegroundColor DarkGray
} else {
    $uvicornArgs = if ($Reload) { "-m uvicorn main:app --reload" } else { "-m uvicorn main:app" }
    Start-ServiceWindow -Title "JARVIS backend" -WorkDir $BackendDir -Command "& '$VenvPython' $uvicornArgs"
    Wait-ForPort -Port $BackendPort -Label "backend" | Out-Null
}

# --- frontend ---------------------------------------------------------------
if ($BackendOnly) {
    Write-Host "`nBackend only: API at http://127.0.0.1:$BackendPort (health: /api/health)." -ForegroundColor Cyan
    Write-Host "Close the 'JARVIS backend' window to stop it." -ForegroundColor DarkGray
    return
}

if (Test-PortListening $FrontendPort) {
    Write-Host "Frontend already listening on $FrontendPort -- reusing it." -ForegroundColor DarkGray
} else {
    Start-ServiceWindow -Title "JARVIS frontend" -WorkDir $FrontendDir -Command "npm run dev"
    Wait-ForPort -Port $FrontendPort -Label "frontend" | Out-Null
}

# --- open ---------------------------------------------------------------
Write-Host "`nJARVIS is up:" -ForegroundColor Green
Write-Host "  Dashboard : $FrontendUrl"
Write-Host "  API       : http://127.0.0.1:$BackendPort/api/health"
Write-Host "Stop it by closing the 'JARVIS backend' and 'JARVIS frontend' windows." -ForegroundColor DarkGray

if (-not $NoBrowser) {
    Start-Process $FrontendUrl | Out-Null
}
