@echo off
cd /d "%~dp0"
echo === DeskBeam remote-only build ===
echo.

:: Create temp build venv
set BUILD=.build_venv
if exist "%BUILD%" rmdir /s /q "%BUILD%"
python -m venv "%BUILD%"
if errorlevel 1 ( echo ERROR: python not found & exit /b 1 )

call "%BUILD%\Scripts\activate.bat"
python -m pip install --upgrade pip -q
pip install -r requirements-remote.txt pyinstaller -q
if errorlevel 1 ( echo ERROR: pip install failed & exit /b 1 )

echo.
echo Building DeskBeamRemote.exe ...
pyinstaller --onefile --noconsole --uac-admin ^
    --name DeskBeamRemote ^
    --icon icon.ico ^
    --add-data "web;deskbeam_web" ^
    --hidden-import websockets.asyncio.server ^
    --hidden-import websockets.http11 ^
    --hidden-import websockets.datastructures ^
    --hidden-import keyboard._winkeyboard ^
    server_remote.py

if errorlevel 1 ( echo ERROR: build failed & exit /b 1 )

:: Copy output
if exist DeskBeamRemote.exe del DeskBeamRemote.exe
copy "dist\DeskBeamRemote.exe" DeskBeamRemote.exe >nul
if errorlevel 1 ( echo ERROR: copy failed & exit /b 1 )

echo.
echo === Build complete ===
echo Output: DeskBeamRemote.exe
echo.
echo To deploy, copy these files to the target machine:
echo   DeskBeamRemote.exe
echo   config.json
echo   cert.pem  (or generate one)
echo   key.pem   (or generate one)
echo.
echo For config.json, start from config.example.json.
echo To generate TLS cert:
echo   openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 3650 -nodes -subj "/CN=localhost"
echo.

:: Cleanup build venv
call deactivate >nul 2>&1
rmdir /s /q "%BUILD%" >nul 2>&1
rmdir /s /q dist >nul 2>&1
rmdir /s /q build >nul 2>&1
del DeskBeamRemote.spec >nul 2>&1

pause
