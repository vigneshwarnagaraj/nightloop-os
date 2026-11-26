@echo off
REM Puts your Wi-Fi details on a freshly flashed NIGHTLOOP OS SD card.
cd /d "%~dp0"
set "S=%~dp0windows\setup_card.ps1"
if not exist "%S%" set "S=%~dp0nightloop-os\windows\setup_card.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%S%"
echo.
pause
