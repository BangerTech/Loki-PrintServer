@echo off
REM Wait until Loki Setup has exited (Inno mutex), then install USBip silently.
setlocal
set "SETUP=%~dp0USBip-Setup.exe"
if not exist "%SETUP%" exit /b 1

:wait_loki
timeout /t 2 /nobreak >nul
tasklist /FI "IMAGENAME eq LokiClient-Setup.exe" | find /I "LokiClient-Setup.exe" >nul
if not errorlevel 1 goto wait_loki
REM Let Inno release its Setup mutex before starting the nested installer.
timeout /t 5 /nobreak >nul

REM /TASKS="" skips USBip's optional desktop shortcut (we only want Loki on the desktop).
"%SETUP%" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /TASKS="" /MERGETASKS="!desktopicon"
set RC=%ERRORLEVEL%

del /q "%PUBLIC%\Desktop\USBip.lnk" 2>nul
del /q "%USERPROFILE%\Desktop\USBip.lnk" 2>nul
del /q "%PUBLIC%\Desktop\USB\IP.lnk" 2>nul
del /q "%USERPROFILE%\Desktop\USB\IP.lnk" 2>nul
del /q "%PUBLIC%\Desktop\usbip.lnk" 2>nul
del /q "%USERPROFILE%\Desktop\usbip.lnk" 2>nul

powershell -NoProfile -WindowStyle Hidden -Command ^
  "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('USB-Treiber (USBip) ist installiert. Bitte Windows jetzt neu starten, damit der Plotter erkannt wird.','Loki-Client', 'OK', 'Information')"

exit /b %RC%
