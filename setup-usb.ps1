# setup-usb.ps1  -  one-time USB-install setup for the ipa-signer
# Run in an ELEVATED PowerShell (Run as Administrator).
#
# Installs the Apple Mobile Device driver (so libimobiledevice can reach the
# iPhone) and makes sure its service is running. The libimobiledevice binaries
# themselves live in .\bin (ideviceinstaller.exe, idevice_id.exe + DLLs).

$ErrorActionPreference = 'Stop'

Write-Host "== Installing Apple Mobile Device Support (driver) ==" -ForegroundColor Cyan
winget install --id Apple.AppleMobileDeviceSupport -e `
    --accept-source-agreements --accept-package-agreements --disable-interactivity

Write-Host "`n== Starting Apple Mobile Device Service ==" -ForegroundColor Cyan
try {
    Start-Service 'Apple Mobile Device Service'
    Write-Host "Service state: $((Get-Service 'Apple Mobile Device Service').Status)"
} catch {
    Write-Warning "Could not start the service: $($_.Exception.Message)"
    Write-Warning "A reboot usually fixes this (the service is set to auto-start)."
}

Write-Host "`n== Detecting connected iPhone ==" -ForegroundColor Cyan
$bin = Join-Path $PSScriptRoot 'bin'
$udids = & (Join-Path $bin 'idevice_id.exe') -l 2>&1
if ($udids) {
    Write-Host "OK - device(s) detected:" -ForegroundColor Green
    $udids | ForEach-Object { Write-Host "   $_" }
    Write-Host "`nUSB install is ready. Run:  python app.py" -ForegroundColor Green
} else {
    Write-Warning "No device detected yet."
    Write-Host "  - Plug in the iPhone, unlock it, tap 'Trust This Computer'."
    Write-Host "  - If it still fails, reboot (the driver service needs it once)."
}
