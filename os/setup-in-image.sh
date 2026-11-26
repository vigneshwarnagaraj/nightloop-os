#!/bin/bash
# Runs inside the Raspberry Pi OS Lite image (chroot) while building NIGHTLOOP OS.
set -eux
export DEBIAN_FRONTEND=noninteractive
printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d; chmod +x /usr/sbin/policy-rc.d
apt-get update
apt-get install -y --no-install-recommends chromium cage wayvnc wlr-randr seatd plymouth plymouth-themes \
  avahi-daemon network-manager python3 curl cloud-guest-utils rfkill iw fonts-dejavu-core ca-certificates
# offline voice commands (speech engine + microphone access)
apt-get install -y --no-install-recommends python3-pip libportaudio2 alsa-utils
pip3 install --break-system-packages vosk sounddevice || echo "offline voice engine could not be installed"
# user 'nightloop' (password 'nightloop', changeable with SETUP_NIGHTLOOP_OS_CARD.bat)
if ! id nightloop >/dev/null 2>&1; then
  if getent passwd 1000 >/dev/null; then usermod -l nightloop -d /home/nightloop -m "$(getent passwd 1000 | cut -d: -f1)"; else useradd -m -u 1000 -s /bin/bash nightloop; fi
fi
for g in sudo video render input audio netdev plugdev _seatd seat; do getent group $g >/dev/null && usermod -aG $g nightloop; done
echo 'nightloop:nightloop' | chpasswd
# no Raspberry Pi set-up wizard, no cloud-init: NIGHTLOOP configures itself
systemctl disable userconfig.service 2>/dev/null || true
systemctl mask userconfig.service 2>/dev/null || true
rm -f /etc/ssh/sshd_config.d/rename_user.conf
[ -d /etc/cloud ] && touch /etc/cloud/cloud-init.disabled
echo nightloop > /etc/hostname
sed -i 's/raspberrypi/nightloop/g' /etc/hosts
chown -R nightloop:nightloop /opt/nightloop
chmod +x /opt/nightloop/*.sh
chown root:root /opt/nightloop/system-update.sh /opt/nightloop/firstboot.sh
cat > /etc/sudoers.d/nightloop <<SUDO
nightloop ALL=(root) NOPASSWD: /usr/sbin/shutdown, /usr/sbin/reboot, /usr/bin/nmcli, /usr/bin/apt-get, /usr/bin/systemctl restart nightloop-kiosk, /opt/nightloop/system-update.sh
SUDO
chmod 440 /etc/sudoers.d/nightloop
touch /opt/nightloop/.nightloop-os
# services
systemctl enable ssh avahi-daemon NetworkManager seatd nightloop nightloop-firstboot nightloop-kiosk nightloop-vnc
systemctl disable getty@tty1.service || true
systemctl set-default graphical.target
# boot screen with the logo
plymouth-set-default-theme nightloop
update-initramfs -u -k all || true
rm -f /usr/sbin/policy-rc.d
apt-get clean
rm -rf /var/lib/apt/lists/*
