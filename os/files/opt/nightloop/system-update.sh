#!/bin/bash
# Runs a one-time system update script delivered with a NIGHTLOOP update (Settings > Updates > Send update).
S=/opt/nightloop/app/os_update.sh
[ -f "$S" ] || exit 0
echo "=== $(date)" >> /var/log/nightloop-os-update.log
bash "$S" >> /var/log/nightloop-os-update.log 2>&1
rc=$?
mv "$S" "$S.done-$(date +%s)"
exit $rc
