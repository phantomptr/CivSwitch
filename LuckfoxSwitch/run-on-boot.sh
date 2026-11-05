#!/bin/bash
set -euo pipefail
exec >> /var/log/civswitch.log 2>&1
echo "CivSwitch: starting $(date), mode=${1:-off}"
case "${1:-off}" in
    payload) args=(/opt/civswitch/payload.txt) ;;
    controller-test) args=(--test-controller) ;;
    *) exit 1 ;;
esac
/opt/civswitch/usb-gadget-hid.sh
exec python3 -u /opt/civswitch/civswitch.py "${args[@]}"
