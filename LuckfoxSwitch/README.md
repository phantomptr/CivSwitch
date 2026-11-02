# LuckfoxSwitch (Linux USB Gadget)

CivSwitch port for **Linux SBCs with USB gadget support**. Replaces the RP2040 firmware with a Python script and a shell-based USB gadget setup. The Switch sees the exact same HID device — same VID/PID (Hori 0x0F0D:0x0092), same report descriptors, same timing.

Originally developed on the Luckfox Pico Pro. Works on any Linux board that can act as a USB peripheral.

## Compatible Boards

| Board | USB Port | Status |
| ----- | -------- | ------ |
| **Luckfox Pico family** (Pro, Max, Plus, Ultra, Mini, base) | USB-C / Micro-USB OTG | Tested on Pro. All models share the same Rockchip SDK and kernel. |
| **Raspberry Pi Zero / Zero W / Zero 2 W** | Micro-USB OTG | Requires `dtoverlay=dwc2` in `/boot/config.txt` ([setup below](#raspberry-pi-zero)) |
| **BeagleBone Black / Green** | Mini-USB client | Disable existing `g_multi` gadget first ([setup below](#beaglebone)) |
| **Orange Pi Zero** | Micro-USB OTG | Allwinner SoC, gadget support via `musb_hdrc` |
| **Any Linux board** | USB OTG / peripheral | Needs `CONFIG_USB_CONFIGFS_F_HID=y` or `=m` in kernel config |

> **Not compatible**: boards with host-only USB ports (full-size USB-A only). The board must act as a USB **device**, not just a host.

## Requirements

- Linux with USB gadget support (`configfs`, `libcomposite`, `usb_f_hid`)
- USB OTG or peripheral port
- Python 3
- Root access

## Quick Start

### 1. Download or generate the payload

**From Releases (easiest):**  
Download `LuckfoxSwitch.tar.gz` from the [Releases page](../../releases) — it includes `payload.txt` pre-built.

**From source:**
```bash
cd PicoSwitch
npm install
node compress.js          # reads ../core.js -> payload.txt
cp payload.txt ../LuckfoxSwitch/
```

### 2. Install on the board

```bash
# Via SSH
scp -r LuckfoxSwitch/ root@<board-ip>:/tmp/
ssh root@<board-ip> 'cd /tmp/LuckfoxSwitch && bash install.sh'
```

For Luckfox via ADB:
```bash
adb push LuckfoxSwitch/ /tmp/LuckfoxSwitch/
adb shell 'cd /tmp/LuckfoxSwitch && bash install.sh'
```

### 3. Activate

```bash
# Enable at boot (this replaces USB networking/ADB with HID):
ssh root@<board-ip> 'systemctl enable civswitch-gadget civswitch && reboot'
```

After reboot, plug the board into the Switch via USB. In Civ 7, navigate to **MULTIPLAYER → WIRELESS → CREATE GAME → HOST LOBBY → CHAT**. The macro starts typing automatically.

### One-time run (without enabling at boot)

```bash
ssh root@<board-ip>
/opt/civswitch/usb-gadget-hid.sh        # set up the HID gadget
python3 /opt/civswitch/civswitch.py      # run the macro
```

## How It Works

`usb-gadget-hid.sh` uses Linux USB configfs to create a composite HID device:

- **Interface 0** — Switch gamepad (8-byte reports)
- **Interface 1** — Boot-protocol keyboard (8-byte reports)

`civswitch.py` reads `payload.txt` and types each line into chat:

1. **Preamble** — press A × 5 to dismiss menus
2. **Per line** — Esc → wait → A → wait → type the line → Enter × 2
3. **Between lines** — A press as separator

The timing matches the PicoSwitch C firmware exactly (32 ms key hold, 200 ms enter gap, 750 ms menu waits).

## Board-Specific Setup

### Raspberry Pi Zero

Add to `/boot/config.txt`:
```
dtoverlay=dwc2
```

Add to `/etc/modules`:
```
dwc2
```

Reboot, then run the installer as above.

### BeagleBone

The default `g_multi` gadget may conflict. The setup script handles this automatically by unbinding the existing gadget, but if you have issues:

```bash
echo "" > /sys/kernel/config/usb_gadget/*/UDC 2>/dev/null
```

Then run the installer.

### Generic Board

Check that your kernel supports USB HID gadgets:

```bash
# Check kernel config:
zcat /proc/config.gz 2>/dev/null | grep USB_CONFIGFS_F_HID
# Expect: CONFIG_USB_CONFIGFS_F_HID=y (or =m)

# If =m, verify the module loads:
modprobe usb_f_hid

# Check for a USB Device Controller:
ls /sys/class/udc/
# Should list at least one controller
```

## Auto-Detection

The gadget setup script adapts to different boards automatically:

- **Existing gadgets** — if one exists (e.g. Luckfox's `rockchip`, BeagleBone's `g_multi`), it reconfigures it instead of creating a new one
- **Strings directory** — detects `0x409` vs `0x0409` naming
- **Kernel modules** — loads `configfs`, `libcomposite`, `usb_f_hid` if they aren't built-in
- **UDC** — finds the first available USB Device Controller

## Restoring Default USB Mode

```bash
/opt/civswitch/restore-default-gadget.sh
```

On Luckfox, this restarts RNDIS+ADB. On other boards, it disables CivSwitch services — reboot to restore defaults.

## Updating the Payload

```bash
scp payload.txt root@<board-ip>:/opt/civswitch/payload.txt
ssh root@<board-ip> reboot
```

## Disabling Auto-Run

```bash
ssh root@<board-ip> 'systemctl disable civswitch.service'
```

The HID gadget still sets up at boot, but the macro won't run. Trigger manually with `python3 /opt/civswitch/civswitch.py`.

## Troubleshooting

| Problem | Cause | Fix |
| ------- | ----- | --- |
| No `/dev/hidg0` after running gadget script | USB OTG not enabled | Check `ls /sys/class/udc/`. On Pi Zero: add `dtoverlay=dwc2`. Check `dmesg \| grep -i gadget`. |
| Lost SSH/ADB after enabling CivSwitch | HID gadget replaces USB networking | Access via Ethernet, Wi-Fi, or serial. Run `restore-default-gadget.sh`. |
| `BrokenPipeError` when testing without a Switch | No USB host polling the HID endpoints | Expected. The Switch polls the endpoints — `civswitch.py` uses blocking writes that wait for this. |
| Macro types but Switch ignores input | Wrong language settings | Set Switch system language and USB Keyboard to English(US). |
