# NIGHTLOOP OS

A ready-made Raspberry Pi 4 SD card image that boots straight into the **NIGHTLOOP AI ATC simulator**:
logo boot screen -> simulator full screen. No desktop, no set-up wizard, VNC already on.

## Use it
1. Download `nightloop-os.img.xz` from **Releases**.
2. Flash it with Raspberry Pi Imager (*Choose OS -> Use custom*, and say **No** to OS customisation) or balenaEtcher.
3. Take the card out and put it back in, then run `SETUP_NIGHTLOOP_OS_CARD.bat` (asks for your Wi-Fi). Or edit `nightloop-wifi.txt` on the card.
4. Put the card in the Pi 4 and switch it on. After ~2 minutes connect **RealVNC Viewer** to `nightloop.local`, or open `http://nightloop.local:8080`.

Login (SSH): user `nightloop`, password `nightloop` (or the one you set).

## Updates
- Simulator: from the NIGHTLOOP Windows app, Settings -> Updates -> Send update (over Wi-Fi).
- System packages: Settings -> Updates -> Update Raspberry Pi OS.
- System changes: an update package may carry `os_update.sh`, which the Pi runs once as root.

## Build
GitHub Actions (`.github/workflows/build.yml`) runs `os/build.sh`: newest Raspberry Pi OS Lite 64-bit + Chromium, cage (kiosk),
wayvnc, Plymouth boot logo, NIGHTLOOP services. Output is published as a Release.
