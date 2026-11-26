# NIGHTLOOP OS - puts your Wi-Fi details on a freshly flashed NIGHTLOOP OS card (run from SETUP_NIGHTLOOP_OS_CARD.bat)
$ErrorActionPreference = "Stop"
Write-Host "NIGHTLOOP OS - SD card Wi-Fi setup" -ForegroundColor Magenta
$boot = $null
foreach ($d in Get-PSDrive -PSProvider FileSystem) {
  $r = $d.Root
  if ((Test-Path (Join-Path $r "config.txt")) -and (Test-Path (Join-Path $r "cmdline.txt"))) { $boot = $r; break }
}
if (-not $boot) { Write-Host "No NIGHTLOOP OS card found. Flash nightloop-os.img.xz first, then take the card out and put it back in." -ForegroundColor Red; exit 1 }
Write-Host "Found the card at $boot" -ForegroundColor Green
$name = Read-Host "Wi-Fi name (SSID)"
$sec = Read-Host "Wi-Fi password" -AsSecureString
$pass = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
$cc = Read-Host "Country code (e.g. US, IN) [US]"; if (-not $cc) { $cc = "US" }
$pw = Read-Host "New password for the Pi (leave empty to keep 'nightloop')"
$txt = "WIFI_NAME=$name`nWIFI_PASSWORD=$pass`nWIFI_COUNTRY=$($cc.ToUpper())`nPI_PASSWORD=$pw`n"
[IO.File]::WriteAllText((Join-Path $boot "nightloop-wifi.txt"), $txt)
Write-Host ""
Write-Host "Done. Eject the card, put it in the Pi and switch it on." -ForegroundColor Green
Write-Host "After about 2 minutes connect RealVNC Viewer to  nightloop.local  (or open http://nightloop.local:8080 in a browser)."
Write-Host "(The Wi-Fi password is removed from the card after the Pi has used it.)"
