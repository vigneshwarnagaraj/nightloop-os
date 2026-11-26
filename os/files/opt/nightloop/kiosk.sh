#!/bin/bash
# NIGHTLOOP OS screen: the simulator full screen (no desktop). Works with a monitor (HDMI) or without one (virtual screen for VNC).
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
for i in $(seq 1 120); do curl -s -o /dev/null http://localhost:8080/api/config && break; sleep 1; done
HDMI=0
for s in /sys/class/drm/card*-HDMI-A-*/status; do [ "$(cat "$s" 2>/dev/null)" = connected ] && HDMI=1; done
if [ "$HDMI" = 0 ]; then
  export WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1
  ( sleep 6
    W=$(ls "$XDG_RUNTIME_DIR" | grep -E '^wayland-[0-9]+$' | head -1)
    WAYLAND_DISPLAY=$W wlr-randr --output HEADLESS-1 --custom-mode 1920x1080 ) &
fi
export WLR_NO_HARDWARE_CURSORS=1
exec cage -s -- chromium --kiosk --app="http://localhost:8080/?pi=1" --ozone-platform=wayland \
  --user-data-dir="$HOME/.config/nightloop-kiosk" --noerrdialogs --disable-infobars --no-first-run --password-store=basic \
  --check-for-update-interval=31536000 --disable-features=Translate --autoplay-policy=no-user-gesture-required
