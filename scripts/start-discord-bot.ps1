<#
.SYNOPSIS
    Boots the JARVIS backend so the Discord bot comes online.

.DESCRIPTION
    The Discord bot isn't its own process — it runs as a background task inside the
    FastAPI backend (see backend/main.py's lifespan + backend/app/platforms/discord.py),
    started automatically whenever DISCORD_BOT_TOKEN is set in the repo-root .env file.
    So "getting the bot online" just means "run the backend". This script is a single
    command for that: it verifies the venv and DISCORD_BOT_TOKEN are set up, then
    launches uvicorn from backend/.

.PARAMETER Reload
    Pass to run uvicorn with --reload (auto-restarts on code changes, like the README's
    normal dev flow). Off by default here because a reload mid-dev-edit drops and
    reconnects the Discord client — fine for API work, mildly annoying if you're
    actively testing the bot in a Discord channel.

.EXAMPLE
    .\scripts\start-discord-bot.ps1
    .\scripts\start-discord-bot.ps1 -Reload
#>

[CmdletBinding()]
param(
    [switch]$Reload
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvActivate = Join-Path $RepoRoot ".venv\Scripts\Activate.ps1"
$EnvFile = Join-Path $RepoRoot ".env"
$BackendDir = Join-Path $RepoRoot "backend"

Write-Host "== JARVIS Discord bot bootstrap ==" -ForegroundColor Cyan

# --- venv check -------------------------------------------------------------
if (-not (Test-Path $VenvActivate)) {
    Write-Error @"
No venv found at .venv\Scripts\Activate.ps1.
Set one up first (from repo root):
    python -m venv .venv
    .venv\Scripts\activate
    pip install -r backend\requirements-dev.txt
"@
}

Write-Host "Activating venv..." -ForegroundColor DarkGray
. $VenvActivate

# --- DISCORD_BOT_TOKEN check -------------------------------------------------
# Matches settings.py's own loading rule: a repo-root .env, regardless of cwd.
$hasToken = $false
if (Test-Path $EnvFile) {
    $tokenLine = Select-String -Path $EnvFile -Pattern '^\s*DISCORD_BOT_TOKEN\s*=\s*(.+)$' -ErrorAction SilentlyContinue
    if ($tokenLine) {
        $value = $tokenLine.Matches[0].Groups[1].Value.Trim()
        if ($value -and $value -ne '""' -and $value -ne "''") {
            $hasToken = $true
        }
    }
}

if (-not $hasToken) {
    Write-Warning @"
DISCORD_BOT_TOKEN is missing or blank in .env — the backend will still start, but
run_discord_bot() will log "DISCORD_BOT_TOKEN not set -- Discord bot disabled." and the
bot will NOT come online. Set it in .env (see .env.example) and re-run this script.
"@
} else {
    Write-Host "DISCORD_BOT_TOKEN is set." -ForegroundColor Green
}

# --- launch -------------------------------------------------------------
Write-Host "Starting backend from backend/ (this also starts the Discord bot)..." -ForegroundColor Cyan
Write-Host "Watch for 'Discord bot connected as <name>' below once it's online." -ForegroundColor DarkGray
Write-Host "Ctrl+C to stop.`n" -ForegroundColor DarkGray

# Invoked as `python -m uvicorn`, not the bare `uvicorn` command: the venv's
# uvicorn.exe console-script shim has been observed to fail silently (exit 1, zero
# stdout/stderr -- even `uvicorn --version` reproduces it) in some shells/terminal
# hosts, while `python -m uvicorn` against the same venv always works. Going through
# python.exe directly sidesteps the shim entirely.
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

Push-Location $BackendDir
try {
    if ($Reload) {
        & $VenvPython -m uvicorn main:app --reload
    } else {
        & $VenvPython -m uvicorn main:app
    }
} finally {
    Pop-Location
}
