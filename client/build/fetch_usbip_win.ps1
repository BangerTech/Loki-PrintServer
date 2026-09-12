# Download the official usbip-win2 installer (Microsoft-attestation-signed UDE driver).
# cezanne/usbip-win 0.3.5 does not load on current Windows 11 ("vhci driver is not loaded").
# GPLv3: https://github.com/vadimgrn/usbip-win2
$ErrorActionPreference = "Stop"

# Tag is "v.0.9.7.7" (dot after v). Skip 0.9.7.8 — upstream warns of BSOD.
$Version = "0.9.7.7"
$Url = "https://github.com/vadimgrn/usbip-win2/releases/download/v.$Version/USBip-$Version-x64.exe"
$DestDir = Join-Path $PSScriptRoot "..\windows\usbip-win"
$Dest = Join-Path $DestDir "USBip-Setup.exe"

Write-Host "[+] Fetching usbip-win2 $Version (signed UDE driver) ..."
New-Item -ItemType Directory -Force -Path $DestDir | Out-Null
Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing

if (-not (Test-Path $Dest) -or ((Get-Item $Dest).Length -lt 1MB)) {
    throw "usbip-win2 installer download failed or is too small: $Dest"
}

Write-Host "[+] usbip-win2 installer ready: $Dest"
