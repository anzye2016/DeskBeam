@echo off
cd /d "%~dp0"

fltmc >nul 2>&1 || (
    powershell -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

if exist server.pid (
    set /p PID=<server.pid
    taskkill /PID %PID% /F >nul 2>&1
    del server.pid >nul 2>&1
)

taskkill /IM DeskBeamRemote.exe /F >nul 2>&1

powershell -NoProfile -Command "$ErrorActionPreference='SilentlyContinue'; $dir='%~dp0'; $dir=$dir.TrimEnd('\'); $escaped=$dir -replace '\\','\\'; Get-CimInstance Win32_Process -Filter \"name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*'+$escaped+'*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"

echo Done.
