@echo off
cd /d "%~dp0"

fltmc >nul 2>&1 || (
    powershell -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

title DeskBeam

rem Kill old instance by port (read port from config.json, default 8769)
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "try{(Get-Content config.json -Raw | ConvertFrom-Json).port}catch{8769}"`) do set DB_PORT=%%P
if not defined DB_PORT set DB_PORT=8769
powershell -NoProfile -Command "$ErrorActionPreference='SilentlyContinue'; Get-NetTCPConnection -LocalPort %DB_PORT% -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"

if not exist .venv (
    python -m venv .venv
    .venv\Scripts\python -m pip install -r requirements.txt -q
)

if not exist cert.pem .venv\Scripts\python certgen.py

if not exist config.json copy config.example.json config.json >nul

echo.
echo  DeskBeam  https://localhost:%DB_PORT%
echo.

.venv\Scripts\python server.py
pause
