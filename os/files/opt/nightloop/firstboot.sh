#!/bin/bash
# Runs at every start (as root): grows the SD card partition once and applies nightloop-wifi.txt from the boot partition.
LOG=/var/log/nightloop-firstboot.log
exec >> "$LOG" 2>&1
echo "=== $(date)"
if [ ! -f /opt/nightloop/.resized ]; then
  ROOT=$(findmnt -no SOURCE /)
  DISK="/dev/$(lsblk -no pkname "$ROOT")"
  PART=$(echo "$ROOT" | grep -o '[0-9]*$')
  growpart "$DISK" "$PART" && resize2fs "$ROOT"
  touch /opt/nightloop/.resized
fi
F=/boot/firmware/nightloop-wifi.txt
[ -f "$F" ] || exit 0
val() { grep -E "^$1=" "$F" | head -1 | cut -d= -f2- | tr -d '\r'; }
NAME=$(val WIFI_NAME); PASS=$(val WIFI_PASSWORD); CC=$(val WIFI_COUNTRY); PW=$(val PI_PASSWORD)
[ -n "$CC" ] && (raspi-config nonint do_wifi_country "$CC" || iw reg set "$CC")
rfkill unblock wifi || true
if [ -n "$NAME" ]; then
  nmcli connection delete nightloop-wifi 2>/dev/null
  if [ -n "$PASS" ]; then
    nmcli connection add type wifi ifname wlan0 con-name nightloop-wifi ssid "$NAME" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PASS" connection.autoconnect yes
  else
    nmcli connection add type wifi ifname wlan0 con-name nightloop-wifi ssid "$NAME" connection.autoconnect yes
  fi
  nmcli connection up nightloop-wifi || true
fi
[ -n "$PW" ] && echo "nightloop:$PW" | chpasswd
# remove the password from the card again
printf 'Applied on %s. To change Wi-Fi again, run SETUP_NIGHTLOOP_OS_CARD.bat on your PC.\n' "$(date)" > /boot/firmware/nightloop-wifi-applied.txt
rm -f "$F"
