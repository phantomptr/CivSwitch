#!/bin/bash
# Install CivSwitch on a Linux SBC with USB gadget support.
# Run this ON the board over SSH, serial, or ADB shell.
#
# Tested boards: Luckfox Pico Pro, Raspberry Pi Zero W, BeagleBone Black
#
# Prerequisites:
#   - Linux with USB gadget support (configfs + libcomposite + usb_f_hid)
#   - USB OTG/peripheral port
#   - Python 3
#   - payload.txt generated on the host (node compress.js in PicoSwitch/)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR=/opt/civswitch

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

cp "$SCRIPT_DIR/civswitch-gadget.service"  /etc/systemd/system/
cp "$SCRIPT_DIR/civswitch.service"         /etc/systemd/system/
systemctl daemon-reload

echo ""
echo "=== Installed to $INSTALL_DIR ==="
echo ""
echo "To activate CivSwitch mode (replaces ADB with HID):"
echo "  systemctl enable civswitch-gadget civswitch && reboot"
echo ""
echo "To run once without enabling at boot:"
echo "  /opt/civswitch/usb-gadget-hid.sh   # sets up HID gadget"
echo "  python3 /opt/civswitch/civswitch.py # runs the macro"
echo ""
echo "To restore ADB+RNDIS:"
echo "  /opt/civswitch/restore-default-gadget.sh"
