# Download attested usbip-win VHCI bits for the Loki Windows installer.
# Client-only files (no stub / test cert). GPLv3: https://github.com/cezanne/usbip-win
$ErrorActionPreference = "Stop"

$Version = "0.3.5"
$Url = "https://github.com/cezanne/usbip-win/releases/download/v$Version/usbip-win-$Version.zip"
$Dest = Join-Path $PSScriptRoot "..\windows\usbip-win"
$Zip = Join-Path $env:TEMP "usbip-win-$Version.zip"
$Extract = Join-Path $env:TEMP "usbip-win-$Version"

Write-Host "[+] Fetching usbip-win $Version ..."
Invoke-WebRequest -Uri $Url -OutFile $Zip -UseBasicParsing

if (Test-Path $Extract) { Remove-Item $Extract -Recurse -Force }
Expand-Archive -Path $Zip -DestinationPath $Extract -Force

if (Test-Path $Dest) { Remove-Item $Dest -Recurse -Force }
New-Item -ItemType Directory -Path $Dest | Out-Null

# Attach client + signed VHCI drivers only. Never ship the test PFX or stub kext.
$Keep = @(
    "usbip.exe",
    "attacher.exe",
    "usb.ids",
    "usbip_vhci_ude.inf",
    "usbip_vhci_ude.sys",
    "usbip_vhci_ude.cat",
    "usbip_vhci.inf",
    "usbip_vhci.sys",
    "usbip_vhci.cat",
    "usbip_root.inf"
)
foreach ($name in $Keep) {
    $src = Join-Path $Extract $name
    if (-not (Test-Path $src)) {
        throw "usbip-win zip missing required file: $name"
    }
    Copy-Item $src (Join-Path $Dest $name)
}

if (-not (Test-Path (Join-Path $Dest "usbip.exe"))) {
    throw "usbip.exe not found after extract"
}

Write-Host "[+] usbip-win ready: $Dest"
