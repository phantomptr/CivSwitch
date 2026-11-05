#!/bin/bash
# Restore the board's default USB gadget after CivSwitch.
#
# On Luckfox: restores RNDIS+ADB via S50usbdevice.
# On other boards: tears down the CivSwitch gadget so the board returns
# to its default state on next boot.
set -euo pipefail

echo "Restoring default USB gadget..."

FROM_JOB=0
case "${1:-}" in
    "") ;;
    --from-job) FROM_JOB=1 ;;
    *) echo "Usage: $0 [--from-job]" >&2; exit 2 ;;
esac

echo off > /etc/civswitch-mode 2>/dev/null || true
if [ "$FROM_JOB" = 0 ] && [ -x /etc/init.d/S99zz-civswitch ]; then
    /etc/init.d/S99zz-civswitch stop || true
fi

# Disable CivSwitch services
systemctl disable civswitch-gadget.service 2>/dev/null || true
systemctl disable civswitch.service 2>/dev/null || true
systemctl stop civswitch.service 2>/dev/null || true

unbind_configfs_gadgets() {
    CONFIGFS=/sys/kernel/config/usb_gadget
    for gadget_dir in "$CONFIGFS"/*/; do
        [ -d "$gadget_dir" ] || continue
        if [ -f "$gadget_dir/UDC" ] && [ -n "$(cat "$gadget_dir/UDC" 2>/dev/null)" ]; then
            echo "" > "$gadget_dir/UDC" 2>/dev/null || true
        fi
    done
}

# CivSwitch stops udev after creating its HID nodes. Do not run S10udev start
# here: that script triggers every subsystem, queues a full device settlement,
# and can block for its 30-second timeout. A single fresh udevd receives the
# gadget events produced below without replaying the entire boot hotplug set.
if command -v killall >/dev/null 2>&1; then
    killall udevd 2>/dev/null || true
fi
if [ -x /sbin/udevd ]; then
    /sbin/udevd -d || true
fi

# CivSwitch may have suppressed the console log level while the Switch
# repeatedly reconfigured the gadget. Restore the saved value.
if [ -f /run/civswitch.printk ]; then
    saved_printk=$(cat /run/civswitch.printk 2>/dev/null || true)
    if [ -n "$saved_printk" ]; then
        echo "$saved_printk" > /proc/sys/kernel/printk 2>/dev/null || true
    fi
    rm -f /run/civswitch.printk
fi

# A UDC can own only one gadget. Release CivSwitch before asking the vendor
# init script to recreate its RNDIS+ADB gadget.
if [ -x /etc/init.d/S50usbdevice ]; then
    unbind_configfs_gadgets
    # S50's restart path writes to a nonexistent per-config UDC attribute and
    # leaves stale HID links in the verified Luckfox layout. Do the unbind and
    # link cleanup explicitly, then call its normal start path.
    for link in /sys/kernel/config/usb_gadget/*/configs/*/*; do
        [ -L "$link" ] && rm -f "$link"
    done
    for process in usbdevice adbd; do
        for pid in $(pidof "$process" 2>/dev/null || true); do
            kill "$pid" 2>/dev/null || true
        done
    done
    for functionfs in /dev/usb-ffs/adb /dev/usb-ffs/ntb; do
        if mountpoint -q "$functionfs" 2>/dev/null; then
            umount "$functionfs" 2>/dev/null || true
        fi
        rmdir "$functionfs" 2>/dev/null || true
    done
    # The stock init script expects this file to select functions. Preserve an
    # existing profile, but add the normal RNDIS+ADB profile if CivSwitch
    # created only its guard marker.
    touch /tmp/.usb_config
    if ! grep -Eq '^usb_[a-z0-9_]+_en$' /tmp/.usb_config; then
        printf 'usb_rndis_en\nusb_adb_en\n' >> /tmp/.usb_config
    fi
    # Keep the vendor udev helper from restarting S50 concurrently on the
    # DISCONNECTED uevents produced by the unbind/rebind below. Removing this
    # guard too early causes its helper to call S50 without USB_CONFIG_FILE,
    # erase the configuration string, and reset usb0 to the vendor address.
    if ! grep -qx 'USB_FORCE_CHANGED' /tmp/.usb_config; then
        echo USB_FORCE_CHANGED >> /tmp/.usb_config
    fi
    USB_CONFIG_FILE=/tmp/.usb_config /etc/init.d/S50usbdevice start
    sleep 1
    sed -i '/^USB_FORCE_CHANGED$/d' /tmp/.usb_config 2>/dev/null || true
    # Re-apply both static addresses after the vendor script recreates usb0.
    if [ -x /etc/init.d/S99zz-ipstatic ]; then
        /etc/init.d/S99zz-ipstatic start || true
    fi
    echo "Default gadget restored (Luckfox RNDIS+ADB)."
else
    unbind_configfs_gadgets
    echo "CivSwitch gadget disabled. Reboot to restore defaults."
fi
