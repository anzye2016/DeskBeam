@echo off
cd /d "%~dp0"

fltmc >nul 2>&1 || (
    powershell -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

rem Kill by PID file (most recent instance)
if exist server.pid (
    set /p PID=<server.pid
    taskkill /PID %PID% /F >nul 2>&1
    del server.pid >nul 2>&1
)

rem Kill any process holding our port, excluding elevated_input
powershell -NoProfile -Command "$ErrorActionPreference='SilentlyContinue'; $elevatedPid=try{Get-Content elevated.pid -ErrorAction SilentlyContinue}catch{$null}; Get-NetTCPConnection -LocalPort 8769 -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -ne $elevatedPid } | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"

rem Kill compiled exe
taskkill /IM DeskBeamRemote.exe /F >nul 2>&1

echo Done.
