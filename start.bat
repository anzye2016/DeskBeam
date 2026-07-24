@echo off
cd /d "%~dp0"

fltmc >nul 2>&1 || (
    powershell -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

title DeskBeam

rem Kill old instance by port
powershell -NoProfile -Command "$ErrorActionPreference='SilentlyContinue'; Get-NetTCPConnection -LocalPort 8769 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"

if not exist .venv (
    python -m venv .venv
    .venv\Scripts\python -m pip install -r requirements.txt -q
)

if not exist cert.pem (
    echo Generating self-signed certificate...
    where /q openssl || ( echo openssl not found & exit /b 1 )
    openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 3650 -nodes -subj "/CN=localhost"
)

if not exist config.json copy config.example.json config.json >nul

echo.
echo  DeskBeam  https://localhost:8769
echo.

.venv\Scripts\python server.py
pause
