# Loki-Client - Windows Installer
# Run as Administrator: powershell -ExecutionPolicy Bypass -File install.ps1

$ErrorActionPreference = "Stop"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Loki-Client - Windows Setup" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# Check Python
try {
    $pyVer = python --version 2>&1
    Write-Host "[+] Found: $pyVer" -ForegroundColor Green
} catch {
    Write-Host "[!] Python not found. Installing via winget..." -ForegroundColor Yellow
    winget install Python.Python.3.12
    refreshenv
}

# Install Python dependencies
Write-Host "[+] Installing Python dependencies..." -ForegroundColor Cyan
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
pip install -r "$scriptDir\..\requirements.txt"

# Check usbip-win
$usbipPath = "C:\Program Files\usbip-win\usbip.exe"
if (-not (Test-Path $usbipPath)) {
    Write-Host ""
    Write-Host "[!] usbip-win NOT FOUND" -ForegroundColor Yellow
    Write-Host "    USB device attachment requires usbip-win." -ForegroundColor Yellow
    Write-Host "    Download from: https://github.com/cezanne/usbip-win/releases" -ForegroundColor Yellow
    Write-Host "    Install the driver and add to PATH." -ForegroundColor Yellow
    Write-Host ""
    $install = Read-Host "Open download page now? [Y/n]"
    if ($install -ne "n") {
        Start-Process "https://github.com/cezanne/usbip-win/releases"
    }
}

# Create Start Menu shortcut
$clientPath = "$scriptDir\..\loki_client.py"
$shortcutPath = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Loki-PrintServer.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "pythonw"
$shortcut.Arguments = "`"$clientPath`""
$shortcut.WorkingDirectory = "$scriptDir\.."
$shortcut.Description = "Loki-PrintServer USB Client"
$shortcut.Save()

Write-Host ""
Write-Host "[✓] Installation complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Run the client:" -ForegroundColor White
Write-Host "    python $clientPath" -ForegroundColor Gray
Write-Host ""
Write-Host "Or from Start Menu: Loki-PrintServer" -ForegroundColor White
