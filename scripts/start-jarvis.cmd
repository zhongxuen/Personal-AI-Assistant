@echo off
REM Double-clickable launcher for JARVIS. Runs scripts\start-jarvis.ps1 with an
REM execution-policy bypass so it works from Explorer without any PowerShell setup.
REM Any arguments are passed through, e.g.  start-jarvis.cmd -BackendOnly
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-jarvis.ps1" %*
if errorlevel 1 pause
