@echo off
REM Wait until Loki Setup has exited (Inno mutex), then install USBip silently.
setlocal
set "SETUP=%~dp0USBip-Setup.exe"
REM Prefer native 64-bit Program Files (WOW64 shells redirect %ProgramFiles%).
if defined ProgramW6432 (
  set "USBIP_DIR=%ProgramW6432%\USBip"
) else (
  set "USBIP_DIR=%ProgramFiles%\USBip"
)
set "USBIP_EXE=%USBIP_DIR%\usbip.exe"
set "USBIP_RES=%USBIP_DIR%\resources.dll"
set "LOCK=%TEMP%\loki-usbip-install.lock"

REM --- Stale lock: ignore if no USBip-Setup is actually running. ---
if exist "%LOCK%" (
  tasklist /FI "IMAGENAME eq USBip-Setup.exe" | find /I "USBip-Setup.exe" >nul
  if errorlevel 1 del /q "%LOCK%" 2>nul
)
if exist "%LOCK%" exit /b 0
echo running> "%LOCK%"

REM --- Skip only a COMPLETE install (exe + resources.dll). ---
REM A leftover usbip.exe without resources.dll is a broken half-uninstall
REM (e.g. after a killed Setup) and must be repaired.
if exist "%USBIP_EXE%" if exist "%USBIP_RES%" goto :already_installed

if not exist "%SETUP%" (
  del /q "%LOCK%" 2>nul
  exit /b 1
)

:wait_loki
timeout /t 2 /nobreak >nul
tasklist /FI "IMAGENAME eq LokiClient-Setup.exe" | find /I "LokiClient-Setup.exe" >nul
if not errorlevel 1 goto wait_loki

REM Broken leftover: uninstall quietly first so USBip-Setup does not
REM start Uninstall + Install at the same time (Inno mutex deadlock).
if exist "%USBIP_DIR%\unins000.exe" (
  start /wait "" "%USBIP_DIR%\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
)
:wait_unins
timeout /t 1 /nobreak >nul
tasklist /FI "IMAGENAME eq unins000.exe" | find /I "unins000.exe" >nul
if not errorlevel 1 goto wait_unins
tasklist /FI "IMAGENAME eq _unins.tmp" | find /I "_unins.tmp" >nul
if not errorlevel 1 goto wait_unins
timeout /t 2 /nobreak >nul

if exist "%USBIP_EXE%" if exist "%USBIP_RES%" goto :already_installed

REM Let Inno release its Setup mutex before starting the nested installer.
timeout /t 5 /nobreak >nul

REM /TASKS="" skips USBip's optional desktop shortcut (we only want Loki on the desktop).
"%SETUP%" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /TASKS="" /MERGETASKS="!desktopicon"
set RC=%ERRORLEVEL%

call :cleanup_shortcuts

powershell -NoProfile -WindowStyle Hidden -Command ^
  "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('USB-Treiber (USBip) ist installiert. Bitte Windows jetzt neu starten, damit der Plotter erkannt wird.','Loki-Client', 'OK', 'Information')"

del /q "%LOCK%" 2>nul
exit /b %RC%

:already_installed
call :cleanup_shortcuts
del /q "%LOCK%" 2>nul
exit /b 0

:cleanup_shortcuts
del /q "%PUBLIC%\Desktop\USBip.lnk" 2>nul
del /q "%USERPROFILE%\Desktop\USBip.lnk" 2>nul
del /q "%PUBLIC%\Desktop\USB\IP.lnk" 2>nul
del /q "%USERPROFILE%\Desktop\USB\IP.lnk" 2>nul
del /q "%PUBLIC%\Desktop\usbip.lnk" 2>nul
del /q "%USERPROFILE%\Desktop\usbip.lnk" 2>nul
goto :eof
