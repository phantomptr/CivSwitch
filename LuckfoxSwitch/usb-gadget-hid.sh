#!/bin/bash
# Set up a USB HID composite gadget (Switch gamepad + keyboard) for CivSwitch.
#
# Works on any Linux board with USB gadget support (configfs + libcomposite):
#   - Luckfox Pico Pro (RV1106)
#   - Raspberry Pi Zero / Zero W / Zero 2 W
#   - BeagleBone Black / Green / AI
#   - Orange Pi Zero / i96
#   - Any board with a USB OTG/peripheral port
#
# On Luckfox, the script reconfigures the existing rockchip gadget because that
# is the enumeration path verified against the physical Switch. It uses the
# Hori Pro Controller identity (VID=0x0F0D, PID=0x0092) so the Switch
# recognizes it.
#
# WARNING: This replaces any existing USB gadget (ADB, RNDIS, ECM, etc.).
# You will lose USB networking and ADB while in CivSwitch mode.
# Access the board via Ethernet, Wi-Fi, or serial console.
set -euo pipefail

GADGET_NAME="${CIVSWITCH_GADGET_NAME:-civswitch}"

# --- Locate or create the gadget directory ---
CONFIGFS=/sys/kernel/config/usb_gadget

if ! mountpoint -q /sys/kernel/config 2>/dev/null; then
    mount -t configfs none /sys/kernel/config 2>/dev/null || true
fi

# Load modules if not built-in
modprobe configfs 2>/dev/null || true
modprobe libcomposite 2>/dev/null || true
modprobe usb_f_hid 2>/dev/null || true

UDC=$(ls /sys/class/udc/ 2>/dev/null | head -1)
if [ -z "$UDC" ]; then
    echo "ERROR: No UDC found. Is USB OTG/peripheral mode enabled?" >&2
    exit 1
fi

# The Switch reissues SET_CONFIGURATION while its controller view initializes.
# Rockchip's Android configfs uevent path logs each request. At 100+ events per
# second, serial console output consumed almost the entire CPU. Suppress only
# the console log level for the duration of CivSwitch; restore-default-gadget
# restores the saved value.
PRINTK_STATE=/run/civswitch.printk
if [ ! -f "$PRINTK_STATE" ]; then
    cat /proc/sys/kernel/printk > "$PRINTK_STATE"
fi
dmesg -n 1

# Luckfox's Android USB helper responds to every CONFIGURED uevent. On the
# physical Switch that handler ran every ~8 ms, repeatedly launching
# /usr/bin/usbdevice until Linux memory was exhausted. CivSwitch owns the
# controller directly, so stop the vendor agent before binding our gadget.
if [ "${CIVSWITCH_STOP_VENDOR_USB:-1}" = 1 ]; then
    # The stock S99usb0config loop and the android_usb udev helper both react
    # to USB_STATE=DISCONNECTED and can restart RNDIS+ADB after CivSwitch has
    # claimed the UDC. Keep the marker read by usbdevice, and stop the loop
    # before stopping the vendor gadget.
    if [ -f /tmp/.usb_config ]; then
        grep -qx 'USB_FORCE_CHANGED' /tmp/.usb_config 2>/dev/null || \
            echo USB_FORCE_CHANGED >> /tmp/.usb_config
    else
        echo USB_FORCE_CHANGED > /tmp/.usb_config
    fi
    for pid in $(ps w | awk '/[S]99usb0config/ {print $1}'); do
        kill "$pid" 2>/dev/null || true
    done
    if [ -x /etc/init.d/S50usbdevice ]; then
        /etc/init.d/S50usbdevice stop >/dev/null 2>&1 || true
    fi
    for process in usbdevice adbd; do
        for pid in $(pidof "$process" 2>/dev/null || true); do
            kill "$pid" 2>/dev/null || true
        done
    done
fi

# A UDC can be owned by only one gadget. Unbind any vendor gadget, but leave
# its configfs tree intact so the default RNDIS+ADB mode can be restored.
for gadget_udc in "$CONFIGFS"/*/UDC; do
    [ -f "$gadget_udc" ] || continue
    if [ "$(cat "$gadget_udc" 2>/dev/null || true)" = "$UDC" ]; then
        echo "" > "$gadget_udc"
    fi
done
sleep 0.3

if [ -d "$CONFIGFS/rockchip" ]; then
    GADGET="$CONFIGFS/rockchip"
    echo "CivSwitch: reconfiguring existing gadget 'rockchip'..."
else
    GADGET="$CONFIGFS/$GADGET_NAME"
    echo "CivSwitch: creating gadget '$GADGET_NAME'..."
fi
mkdir -p "$GADGET/configs/b.1"

# Remove every stale function link from the gadget we are about to bind.
for link in "$GADGET"/configs/*/*; do
    [ -L "$link" ] && rm -f "$link"
done

# f_hid assigns /dev/hidgN IDs in function-creation order. Release every stale
# HID function, including ones left by an older CivSwitch run or by the vendor
# gadget, so the gamepad and keyboard deterministically become hidg0/hidg1.
for link in "$CONFIGFS"/*/configs/*/*; do
    [ -L "$link" ] || continue
    case "$(readlink "$link" 2>/dev/null || true)" in
        */functions/hid.usb*) rm -f "$link" ;;
    esac
done
for hid_function in "$CONFIGFS"/*/functions/hid.usb*; do
    [ -d "$hid_function" ] || continue
    rmdir "$hid_function"
done

# --- Reconfigure device descriptor (Hori Pro Controller) ---
echo 0x0200 > "$GADGET/bcdUSB"
echo 0x00   > "$GADGET/bDeviceClass"
echo 0x00   > "$GADGET/bDeviceSubClass"
echo 0x00   > "$GADGET/bDeviceProtocol"
echo 0x40   > "$GADGET/bMaxPacketSize0"
echo 0x0F0D > "$GADGET/idVendor"
echo 0x0092 > "$GADGET/idProduct"
echo 0x0100 > "$GADGET/bcdDevice"

# Clear Microsoft OS descriptor settings inherited from RNDIS/ADB mode.
if [ -f "$GADGET/os_desc/use" ]; then
    echo 0 > "$GADGET/os_desc/use"
fi
echo 0x80 > "$GADGET/configs/b.1/bmAttributes"
echo 500 > "$GADGET/configs/b.1/MaxPower"

# Strings — detect existing directory name (some boards use 0x409, others 0x0409)
if [ -d "$GADGET/strings/0x409" ]; then
    STRINGS="$GADGET/strings/0x409"
elif [ -d "$GADGET/strings/0x0409" ]; then
    STRINGS="$GADGET/strings/0x0409"
else
    STRINGS="$GADGET/strings/0x409"
    mkdir -p "$STRINGS"
fi
echo "PICO SWITCH PRO."  > "$STRINGS/manufacturer"
echo "J24 CONTROLLER"    > "$STRINGS/product"

# Config strings — same auto-detect
if [ -d "$GADGET/configs/b.1/strings/0x409" ]; then
    CFG_STRINGS="$GADGET/configs/b.1/strings/0x409"
elif [ -d "$GADGET/configs/b.1/strings/0x0409" ]; then
    CFG_STRINGS="$GADGET/configs/b.1/strings/0x0409"
else
    CFG_STRINGS="$GADGET/configs/b.1/strings/0x409"
    mkdir -p "$CFG_STRINGS"
fi

# --- Configure HID function 0: Switch gamepad ---
HID0="$GADGET/functions/hid.usb0"
mkdir -p "$HID0"
echo 0 > "$HID0/protocol"
echo 0 > "$HID0/subclass"
# f_hid uses report_length as the endpoint's wMaxPacketSize too. Pico's
# gamepad endpoints allow 64-byte packets, although normal input is 8 bytes.
# This also permits echoing host output reports without truncating them.
echo 64 > "$HID0/report_length"

# Gamepad report descriptor (86 bytes — matches switch_usb_report_descriptor)
python3 -c "
import sys
sys.stdout.buffer.write(bytes([
    0x05,0x01,0x09,0x05,0xA1,0x01,0x15,0x00,0x25,0x01,0x35,0x00,0x45,0x01,
    0x75,0x01,0x95,0x10,0x05,0x09,0x19,0x01,0x29,0x10,0x81,0x02,0x05,0x01,
    0x25,0x07,0x46,0x3B,0x01,0x75,0x04,0x95,0x01,0x65,0x14,0x09,0x39,0x81,
    0x42,0x65,0x00,0x95,0x01,0x81,0x01,0x26,0xFF,0x00,0x46,0xFF,0x00,0x09,
    0x30,0x09,0x31,0x09,0x32,0x09,0x35,0x75,0x08,0x95,0x04,0x81,0x02,0x06,
    0x00,0xFF,0x09,0x20,0x95,0x01,0x81,0x02,0x0A,0x21,0x26,0x95,0x08,0x91,
    0x02,0xC0
]))
" > "$HID0/report_desc"

# --- Configure HID function 1: Keyboard (boot protocol) ---
HID1="$GADGET/functions/hid.usb1"
mkdir -p "$HID1"
echo 1 > "$HID1/protocol"
echo 1 > "$HID1/subclass"
echo 8 > "$HID1/report_length"

# Keyboard report descriptor (63 bytes — matches keyboard_usb_report_descriptor)
python3 -c "
import sys
sys.stdout.buffer.write(bytes([
    0x05,0x01,0x09,0x06,0xA1,0x01,0x75,0x01,0x95,0x08,0x05,0x07,0x19,0xE0,
    0x29,0xE7,0x15,0x00,0x25,0x01,0x81,0x02,0x95,0x01,0x75,0x08,0x81,0x01,
    0x95,0x05,0x75,0x01,0x05,0x08,0x19,0x01,0x29,0x05,0x91,0x02,0x95,0x01,
    0x75,0x03,0x91,0x01,0x95,0x06,0x75,0x08,0x15,0x00,0x25,0x65,0x05,0x07,
    0x19,0x00,0x29,0x65,0x81,0x00,0xC0
]))
" > "$HID1/report_desc"

# --- Link functions to config (gamepad first = ITF 0, keyboard = ITF 1) ---
ln -sf "$HID0"  "$GADGET/configs/b.1/f1"
ln -sf "$HID1"  "$GADGET/configs/b.1/f2"

echo "CivSwitch HID" > "$CFG_STRINGS/configuration"

# --- Bind to UDC ---
echo "$UDC" > "$GADGET/UDC"

tries=0
while [ ! -c /dev/hidg0 ] || [ ! -c /dev/hidg1 ]; do
    tries=$((tries + 1))
    [ "$tries" -lt 20 ] || {
        echo "ERROR: expected /dev/hidg0 and /dev/hidg1 after binding" >&2
        ls -l /dev/hidg* 2>&1 || true
        exit 1
    }
    sleep 0.1
done

# HID character nodes are supplied by devtmpfs; udev is not required to create
# them. Stop the udev event queue only after both nodes exist so the same
# repeated uevents do not spawn vendor helpers or consume userspace CPU.
if [ -x /etc/init.d/S10udev ]; then
    /etc/init.d/S10udev stop >/dev/null 2>&1 || true
fi

SPEED=$(cat "/sys/class/udc/$UDC/current_speed" 2>/dev/null || echo unknown)
echo "CivSwitch: gadget ready — gamepad=/dev/hidg0 keyboard=/dev/hidg1 speed=$SPEED"
