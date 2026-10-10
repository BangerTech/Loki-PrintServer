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
set "LOCK=%TEMP%\loki-usbip-install.lock"

REM --- Single-instance guard: only one installer run at a time. ---
if exist "%LOCK%" exit /b 0
echo running> "%LOCK%"

REM --- Skip if a USBip Setup/Uninstall is already running (deadlock risk). ---
tasklist /FI "IMAGENAME eq USBip-Setup.exe" | find /I "USBip-Setup.exe" >nul
if not errorlevel 1 (
  del /q "%LOCK%" 2>nul
  exit /b 0
)

REM --- Skip entirely if USBip is already installed (avoids an uninstall/
REM     reinstall deadlock on the Inno Setup mutex). ---
if exist "%USBIP_EXE%" goto :already_installed
if exist "%USBIP_DIR%\unins000.exe" goto :already_installed
if exist "%ProgramFiles%\USBip\usbip.exe" goto :already_installed

if not exist "%SETUP%" (
  del /q "%LOCK%" 2>nul
  exit /b 1
)

:wait_loki
timeout /t 2 /nobreak >nul
tasklist /FI "IMAGENAME eq LokiClient-Setup.exe" | find /I "LokiClient-Setup.exe" >nul
if not errorlevel 1 goto wait_loki
REM Also wait if a previous USBip Setup is still around.
tasklist /FI "IMAGENAME eq USBip-Setup.exe" | find /I "USBip-Setup.exe" >nul
if not errorlevel 1 goto wait_loki
REM Let Inno release its Setup mutex before starting the nested installer.
timeout /t 5 /nobreak >nul

REM Final race check: another process may have installed USBip while we waited.
if exist "%USBIP_EXE%" goto :already_installed

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
