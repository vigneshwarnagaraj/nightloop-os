#!/bin/bash
# Builds out/nightloop-os.img.xz from the newest Raspberry Pi OS Lite (64-bit). Run as root on Linux (GitHub Actions does this).
set -euxo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK=/tmp/nlos; OUT="$REPO/out"
rm -rf "$WORK"; mkdir -p "$WORK" "$OUT"; cd "$WORK"
apt-get update
apt-get install -y qemu-user-static binfmt-support xz-utils parted e2fsprogs curl
curl -fL --retry 3 -o base.img.xz https://downloads.raspberrypi.com/raspios_lite_arm64_latest
xz -d -T0 base.img.xz
IMG=nightloop-os.img
mv base.img $IMG
truncate -s +3G $IMG
parted -s $IMG resizepart 2 100%
LOOP=$(losetup -fP --show $IMG)
e2fsck -fy ${LOOP}p2 || true
resize2fs ${LOOP}p2
M=$WORK/mnt; mkdir -p $M
mount ${LOOP}p2 $M
mount ${LOOP}p1 $M/boot/firmware
mount --bind /dev $M/dev; mount --bind /dev/pts $M/dev/pts; mount -t proc proc $M/proc; mount -t sysfs sys $M/sys
cp /usr/bin/qemu-aarch64-static $M/usr/bin/ || true
mv $M/etc/resolv.conf $M/etc/resolv.conf.nl || true
cp /etc/resolv.conf $M/etc/resolv.conf
# NIGHTLOOP files
cp -r "$REPO/os/files/." $M/
mkdir -p $M/opt/nightloop/app/data
cp "$REPO/app/server.py" $M/opt/nightloop/app/
cp -r "$REPO/app/static" $M/opt/nightloop/app/
cp "$REPO/app/config.json" $M/opt/nightloop/app/
# offline voice model (40 MB)
curl -fL --retry 3 -o vosk.zip https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip && \
  (apt-get install -y unzip && unzip -q vosk.zip && mv vosk-model-small-en-us-0.15 $M/opt/nightloop/app/data/vosk-model) || echo "voice model skipped"
cp "$REPO/os/setup-in-image.sh" $M/tmp/
chroot $M /bin/bash /tmp/setup-in-image.sh
rm -f $M/tmp/setup-in-image.sh $M/usr/bin/qemu-aarch64-static
rm -f $M/etc/resolv.conf; mv $M/etc/resolv.conf.nl $M/etc/resolv.conf || true
# quiet boot with the logo; boot screen hides the text console
CMD=$M/boot/firmware/cmdline.txt
sed -i 's/console=tty1/console=tty3/' $CMD
sed -i 's/$/ quiet splash plymouth.ignore-serial-consoles logo.nologo vt.global_cursor_default=0 loglevel=3 consoleblank=0/' $CMD
grep -q '^disable_splash' $M/boot/firmware/config.txt || echo 'disable_splash=1' >> $M/boot/firmware/config.txt
cat > $M/boot/firmware/nightloop-wifi.txt <<TXT
# NIGHTLOOP OS - edit this file on your PC before the first start (or use SETUP_NIGHTLOOP_OS_CARD.bat)
WIFI_NAME=
WIFI_PASSWORD=
WIFI_COUNTRY=US
PI_PASSWORD=
TXT
sync
umount $M/dev/pts $M/dev $M/proc $M/sys $M/boot/firmware $M
e2fsck -fy ${LOOP}p2 || true
losetup -d $LOOP
xz -T0 -6 -c $IMG > "$OUT/nightloop-os.img.xz"
(cd "$OUT" && sha256sum nightloop-os.img.xz > SHA256SUMS.txt)
ls -la "$OUT"
