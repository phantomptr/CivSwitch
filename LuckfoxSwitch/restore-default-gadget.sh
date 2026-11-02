#!/bin/bash
# Restore the board's default USB gadget after CivSwitch.
#
# On Luckfox: restores RNDIS+ADB via S50usbdevice.
# On other boards: tears down the CivSwitch gadget so the board returns
# to its default state on next boot.
set -euo pipefail

echo "Restoring default USB gadget..."

# Disable CivSwitch services
systemctl disable civswitch-gadget.service 2>/dev/null || true
systemctl disable civswitch.service 2>/dev/null || true

# Luckfox-specific: restart the rockchip default gadget
if [ -x /etc/init.d/S50usbdevice ]; then
    /etc/init.d/S50usbdevice restart
    echo "Default gadget restored (Luckfox RNDIS+ADB)."
else
    # Generic: unbind and remove the CivSwitch gadget
    CONFIGFS=/sys/kernel/config/usb_gadget
    for gadget_dir in "$CONFIGFS"/*/; do
        [ -d "$gadget_dir" ] || continue
        if [ -f "$gadget_dir/UDC" ] && [ -n "$(cat "$gadget_dir/UDC" 2>/dev/null)" ]; then
            echo "" > "$gadget_dir/UDC" 2>/dev/null || true
        fi
    done
    echo "CivSwitch gadget disabled. Reboot to restore defaults."
fi
