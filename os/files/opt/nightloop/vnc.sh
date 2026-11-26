#!/bin/bash
# VNC (port 5900) showing the NIGHTLOOP screen - connect with RealVNC Viewer to nightloop.local
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
for i in $(seq 1 120); do
  W=$(ls "$XDG_RUNTIME_DIR" 2>/dev/null | grep -E '^wayland-[0-9]+$' | head -1)
  [ -n "$W" ] && break; sleep 1
done
[ -z "$W" ] && exit 1
sleep 3
export WAYLAND_DISPLAY=$W
exec wayvnc 0.0.0.0 5900
