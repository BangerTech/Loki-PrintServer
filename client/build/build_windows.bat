@echo off
REM Loki-PrintServer — Windows .exe Builder
REM Run this on Windows: build\build_windows.bat

echo ==========================================
echo   Loki-PrintServer Windows Build
echo ==========================================

cd /d "%~dp0\.."

echo [+] Installing dependencies...
pip install pyinstaller pillow pystray zeroconf httpx websockets customtkinter

echo [+] Building .exe with PyInstaller...
pyinstaller ^
    --name "LokiClient" ^
    --windowed ^
    --onefile ^
    --add-data "core;core" ^
    --hidden-import "zeroconf._utils.ipaddress" ^
    --hidden-import "zeroconf._handlers.answers" ^
    --collect-all "pystray" ^
    --collect-all "customtkinter" ^
    tray_app.py

echo [+] EXE created: dist\LokiClient.exe

REM Optional: Create installer with Inno Setup if available
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
    echo [+] Building installer with Inno Setup...
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" build\installer.iss
    echo [+] Installer created: dist\LokiClient-Setup.exe
) else (
    echo [i] Inno Setup not found - skipping installer
    echo     Download: https://jrsoftware.org/isdl.php
)

echo.
echo [OK] Build complete!
echo     EXE: dist\LokiClient.exe
pause
