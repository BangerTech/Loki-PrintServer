@echo off
REM Wait until Loki Setup has exited (Inno mutex), then install USBip silently.
setlocal
set "SETUP=%~dp0USBip-Setup.exe"
set "USBIP_EXE=%ProgramFiles%\USBip\usbip.exe"
set "LOCK=%TEMP%\loki-usbip-install.lock"

REM --- Single-instance guard: only one installer run at a time. ---
if exist "%LOCK%" exit /b 0
echo running> "%LOCK%"

REM --- Skip entirely if USBip is already installed (avoids an uninstall/
REM     reinstall deadlock on the Inno Setup mutex). ---
if exist "%USBIP_EXE%" (
  call :cleanup_shortcuts
  del /q "%LOCK%" 2>nul
  exit /b 0
)

if not exist "%SETUP%" (
  del /q "%LOCK%" 2>nul
  exit /b 1
)

:wait_loki
timeout /t 2 /nobreak >nul
tasklist /FI "IMAGENAME eq LokiClient-Setup.exe" | find /I "LokiClient-Setup.exe" >nul
if not errorlevel 1 goto wait_loki
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

:cleanup_shortcuts
del /q "%PUBLIC%\Desktop\USBip.lnk" 2>nul
del /q "%USERPROFILE%\Desktop\USBip.lnk" 2>nul
del /q "%PUBLIC%\Desktop\USB\IP.lnk" 2>nul
del /q "%USERPROFILE%\Desktop\USB\IP.lnk" 2>nul
del /q "%PUBLIC%\Desktop\usbip.lnk" 2>nul
del /q "%USERPROFILE%\Desktop\usbip.lnk" 2>nul
goto :eof
