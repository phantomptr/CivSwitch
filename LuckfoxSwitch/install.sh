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

if [ ! -f "$SCRIPT_DIR/payload.txt" ]; then
    echo "ERROR: payload.txt not found in $SCRIPT_DIR"
    echo "Generate it first: cd PicoSwitch && npm install && node compress.js"
    exit 1
fi

mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_DIR/civswitch.py"              "$INSTALL_DIR/"
cp "$SCRIPT_DIR/usb-gadget-hid.sh"         "$INSTALL_DIR/"
cp "$SCRIPT_DIR/restore-default-gadget.sh"  "$INSTALL_DIR/"
cp "$SCRIPT_DIR/payload.txt"               "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/civswitch.py" "$INSTALL_DIR/usb-gadget-hid.sh" "$INSTALL_DIR/restore-default-gadget.sh"

if [ -d /run/systemd/system ]; then
    cp "$SCRIPT_DIR/civswitch-gadget.service" /etc/systemd/system/
    cp "$SCRIPT_DIR/civswitch.service" /etc/systemd/system/
    systemctl daemon-reload
else
    command -v start-stop-daemon >/dev/null
    cp "$SCRIPT_DIR/run-on-boot.sh" "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/S99zz-civswitch" /etc/init.d/
    chmod +x "$INSTALL_DIR/run-on-boot.sh" /etc/init.d/S99zz-civswitch
    # Installing never arms payload typing. Explicitly select a mode later.
    echo off > /etc/civswitch-mode
    echo "Installed for BusyBox init; automatic typing is OFF."
    echo "Controller-only boot: echo controller-test > /etc/civswitch-mode"
    echo "Full payload boot:    echo payload > /etc/civswitch-mode"
    echo "Run sync, then unplug USB from the computer and move it to the Switch."
    echo "Do not reboot or poweroff while connected to the computer: this Pro may restart."
    exit 0
fi

echo ""
echo "=== Installed to $INSTALL_DIR ==="
echo ""
echo "To activate CivSwitch mode (replaces ADB with HID):"
echo "  systemctl enable civswitch-gadget civswitch && sync"
echo "Then unplug USB from the computer and connect to the prepared Switch."
echo ""
echo "To run once without enabling at boot:"
echo "  /opt/civswitch/usb-gadget-hid.sh   # sets up HID gadget"
echo "  python3 /opt/civswitch/civswitch.py # runs the macro"
echo ""
echo "To restore ADB+RNDIS:"
echo "  /opt/civswitch/restore-default-gadget.sh"
