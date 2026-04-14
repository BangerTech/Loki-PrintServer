@echo off
REM Loki-Client — Windows .exe Builder
REM Run this on Windows from repo root: client\build\build_windows.bat

echo ==========================================
echo   Loki-Client Windows Build
echo ==========================================

cd /d "%~dp0\.."

echo [+] Installing dependencies...
pip install pyinstaller pillow pystray zeroconf websockets customtkinter packaging

echo [+] Building .exe with PyInstaller...
pyinstaller ^
    --name "LokiClient" ^
    --windowed ^
    --onefile ^
    --icon "assets\icon.ico" ^
    --add-data "core;core" ^
    --add-data "assets;assets" ^
    --add-data "onboarding.py;." ^
    --hidden-import "onboarding" ^
    --hidden-import "core.api_client" ^
    --hidden-import "core.config" ^
    --hidden-import "core.device_db" ^
    --hidden-import "core.discovery" ^
    --hidden-import "core.usbip_attach" ^
    --hidden-import "zeroconf._utils.ipaddress" ^
    --hidden-import "zeroconf._handlers.answers" ^
    --collect-all "pystray" ^
    --collect-all "customtkinter" ^
    --collect-all "zeroconf" ^
    tray_app.py

if not exist "dist\LokiClient.exe" (
    echo [!] ERROR: EXE not created
    exit /b 1
)
echo [+] EXE created: dist\LokiClient.exe

REM Build installer with Inno Setup if available
where /q ISCC.exe 2>nul
if %errorlevel% equ 0 (
    set ISCC=ISCC.exe
) else if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
    set ISCC=C:\Program Files (x86^)\Inno Setup 6\ISCC.exe
) else (
    set ISCC=
)

if defined ISCC (
    echo [+] Building installer with Inno Setup...
    "%ISCC%" build\installer.iss
    if %errorlevel% equ 0 (
        echo [+] Installer created: dist\LokiClient-Setup.exe
    ) else (
        echo [!] Inno Setup failed - shipping raw EXE only
    )
) else (
    echo [i] Inno Setup not found - skipping installer
)

echo.
echo [OK] Build complete!
echo     EXE:       dist\LokiClient.exe
if exist "dist\LokiClient-Setup.exe" echo     Installer: dist\LokiClient-Setup.exe
