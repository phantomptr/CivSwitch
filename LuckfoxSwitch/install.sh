#!/bin/bash
# Install CivSwitch on a Linux SBC with USB gadget support.
# Run this ON the board over SSH, serial, or ADB shell.
#
# Supports systemd and Luckfox Buildroot's BusyBox init.
#
# Prerequisites:
#   - Linux with USB gadget support (configfs + libcomposite + usb_f_hid)
#   - USB OTG/peripheral port
#   - Python 3
#   - payload.txt generated on the host (node compress.js in PicoSwitch/)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR=/opt/civswitch

command -v python3 >/dev/null
command -v bash >/dev/null
[ "$(id -u)" = 0 ] || { echo 'Run this installer as root.' >&2; exit 1; }

echo "=== CivSwitch installer ==="

# Some restored Luckfox images retain a desktop UID on /root. OpenSSH's
# StrictModes then rejects authorized_keys even when the key itself is correct.
chown root:root /root
chmod 700 /root
if [ -d /root/.ssh ]; then
    chown root:root /root/.ssh
    chmod 700 /root/.ssh
fi
if [ -f /root/.ssh/authorized_keys ]; then
    chown root:root /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys
fi

if [ ! -f "$SCRIPT_DIR/payload.txt" ]; then
    echo "ERROR: payload.txt not found in $SCRIPT_DIR"
    echo "Generate it first: cd PicoSwitch && npm install && node compress.js"
    exit 1
fi

mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_DIR/civswitch.py"              "$INSTALL_DIR/"
cp "$SCRIPT_DIR/civswitchctl"              "$INSTALL_DIR/"
cp "$SCRIPT_DIR/usb-gadget-hid.sh"         "$INSTALL_DIR/"
cp "$SCRIPT_DIR/set-static-ip.sh"          "$INSTALL_DIR/"
cp "$SCRIPT_DIR/setup-swap.sh"             "$INSTALL_DIR/"
cp "$SCRIPT_DIR/restore-default-gadget.sh"  "$INSTALL_DIR/"
cp "$SCRIPT_DIR/payload.txt"               "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/civswitch.py" "$INSTALL_DIR/civswitchctl" \
    "$INSTALL_DIR/usb-gadget-hid.sh" "$INSTALL_DIR/set-static-ip.sh" \
    "$INSTALL_DIR/setup-swap.sh" "$INSTALL_DIR/restore-default-gadget.sh"
cp "$SCRIPT_DIR/run-on-boot.sh" "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/run-on-boot.sh"

# Keep the requested Ethernet address stable, and make the control command
# available by name without creating any additional SSH aliases.
"$INSTALL_DIR/set-static-ip.sh" \
    "${CIVSWITCH_STATIC_IP:-192.168.86.198}" \
    "${CIVSWITCH_STATIC_NETMASK:-255.255.255.0}" \
    "${CIVSWITCH_STATIC_GATEWAY:-192.168.86.1}"
ln -sf "$INSTALL_DIR/civswitchctl" /usr/bin/civswitchctl

if [ -d /run/systemd/system ]; then
    cp "$SCRIPT_DIR/civswitch-gadget.service" /etc/systemd/system/
    cp "$SCRIPT_DIR/civswitch.service" /etc/systemd/system/
    # Manual operation is the default. Keep the unit files for explicit use.
    systemctl disable civswitch-gadget.service civswitch.service 2>/dev/null || true
    systemctl daemon-reload
else
    command -v start-stop-daemon >/dev/null
    cp "$SCRIPT_DIR/S99zz-civswitch" /etc/init.d/
    chmod +x "$INSTALL_DIR/run-on-boot.sh" /etc/init.d/S99zz-civswitch
    # Installing never arms payload typing. Explicitly select a mode later.
    echo off > /etc/civswitch-mode
    echo "Installed for BusyBox init; automatic typing is OFF."
    echo "Manual control: civswitchctl {start|start-line N|test|stop|status}"
    echo "Run sync, then unplug USB from the computer and move it to the Switch."
    echo "Do not reboot or poweroff while connected to the computer: this Pro may restart."
    exit 0
fi

echo ""
echo "=== Installed to $INSTALL_DIR ==="
echo ""
echo "To run manually (replaces ADB with HID):"
echo "  civswitchctl start              # full payload"
echo "  civswitchctl start-line N       # one payload line"
echo "  civswitchctl test               # R warm-up, then A/B/X/Y/L"
echo "  civswitchctl stop               # restore RNDIS+ADB"
echo "  civswitchctl status"
echo ""
echo "To restore ADB+RNDIS:"
echo "  civswitchctl restore"
