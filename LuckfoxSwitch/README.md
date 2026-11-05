# LuckfoxSwitch (Linux USB Gadget)

CivSwitch port for **Linux SBCs with USB gadget support**. Replaces the RP2040 firmware with a Python script and a shell-based USB gadget setup. It uses the Pico firmware's VID/PID (Hori 0x0F0D:0x0092), HID report descriptors, macro sequence, and nominal hold/wait times.

Originally developed on the Luckfox Pico Pro. On 2026-11-04, the onboard-flash installation passed USB endpoint and reboot-persistence checks, and a user reported that it appeared to work on a physical Switch. Completion of the full payload and loader startup have not yet been confirmed. Linux gadget USB descriptors and control requests differ from TinyUSB; this is not a byte-for-byte replacement for Pico USB firmware.

## Compatible Boards

| Board | USB Port | Status |
| ----- | -------- | ------ |
| **Luckfox Pico Pro** | USB-C OTG | Onboard Buildroot installation verified; initial Switch operation reported. |
| **Other Luckfox Pico models** | Model dependent OTG port | Require compatible firmware and HID gadget support; not verified here. |
| **Raspberry Pi Zero / Zero W / Zero 2 W** | Micro-USB OTG | Requires `dtoverlay=dwc2` in `/boot/config.txt` ([setup below](#raspberry-pi-zero)) |
| **BeagleBone Black / Green** | Mini-USB client | Disable existing `g_multi` gadget first ([setup below](#beaglebone)) |
| **Orange Pi Zero** | Micro-USB OTG | Allwinner SoC, gadget support via `musb_hdrc` |
| **Any Linux board** | USB OTG / peripheral | Needs `CONFIG_USB_CONFIGFS_F_HID=y` or `=m` in kernel config |

> **Not compatible**: boards with host-only USB ports (full-size USB-A only). The board must act as a USB **device**, not just a host.

## Requirements

- Linux with USB gadget support (`configfs`, `libcomposite`, `usb_f_hid`)
- USB OTG or peripheral port
- Python 3
- Bash
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

Prepare the Switch **before** powering the board: the repo requires the physical Civ VII cartridge running game version **1.0.0**, with system language and USB keyboard layout set to **English (US)**. Navigate to **MULTIPLAYER → WIRELESS → CREATE GAME → HOST LOBBY → CHAT** and focus the chat input.

Enabling auto-run makes the board type on **any USB host**, including your Mac. Keep Ethernet/serial access available when testing; USB networking and ADB disappear in HID mode. The installer itself does not enable auto-run.

For the **Luckfox Pico Pro's onboard SPI NAND**, use the official Pro/Max **Flash Buildroot** image, not the Ubuntu SD-card image. Its 256 MB flash is sufficient; an SD card is not required. The installer supports BusyBox init as well as systemd. On Buildroot, select **one** boot mode:

```bash
# Controller diagnostic only (five A presses, no text):
echo controller-test > /etc/civswitch-mode
# Or, for the complete payload:
echo payload > /etc/civswitch-mode
sync
```

After `sync` returns, unplug the board from the computer and connect it to the prepared Switch. **Do not use `poweroff` to move this board:** the tested Pro restarted while USB power remained connected and began typing into the computer. Do not reboot it on the computer after enabling payload mode either.

The late `/etc/init.d/S99zz-civswitch` startup runs after the stock USB setup. Logs are in `/var/log/civswitch.log`. Set `/etc/civswitch-mode` to `off` to disable startup; reboot restores the stock USB gadget. Installing on Buildroot resets this mode to `off`; it does not stop an already running macro.

```bash
# On systemd distributions, enable at boot:
ssh root@<ethernet-board-ip> 'systemctl enable civswitch-gadget civswitch && sync'
```

Then unplug and move the board's USB OTG cable to the Switch. Linux must finish booting; after USB configuration, the macro starts following the Pico's one-second start delay. Allow approximately 15–20 minutes for the payload. Power-cycling runs it again from the beginning.

### Check controller input before running the payload

If typing works but you have to press A manually after Enter, the controller path has not been validated. From Ethernet SSH or serial, keep auto-run disabled, connect the OTG port to the Switch, then run:

```bash
# systemd:
systemctl disable --now civswitch.service
# Buildroot instead:
echo off > /etc/civswitch-mode
/etc/init.d/S99zz-civswitch stop

/opt/civswitch/usb-gadget-hid.sh
python3 -u /opt/civswitch/civswitch.py --test-controller
```

This sends only the Pico's five-A preamble, then keeps both interfaces neutral. No payload text is sent. Observe whether the Switch responds to A on a screen where A has a visible effect; stop with Ctrl+C. Do not run a full payload until controller input works. If you cannot maintain Ethernet/serial access, prepare and test a diagnostic-only startup before enabling the full payload.

### One-time run (without enabling at boot)

```bash
ssh root@<ethernet-board-ip>
/opt/civswitch/usb-gadget-hid.sh        # set up the HID gadget
python3 /opt/civswitch/civswitch.py      # run the macro
```

## How It Works

`usb-gadget-hid.sh` uses Linux USB configfs to create a composite HID device:

- **Interface 0** — Switch gamepad (8-byte input reports, 64-byte endpoint maximum packet size)
- **Interface 1** — Boot-protocol keyboard (8-byte reports)

`civswitch.py` reads `payload.txt` and types each line into chat:

1. **Preamble** — press A × 5 to dismiss menus
2. **Per line** — Esc → wait → A → wait → type the line → Enter × 2
3. **Between lines** — A press as separator

Two independent nonblocking workers continuously transmit the current gamepad and keyboard states, including neutral reports during pauses and after the macro finishes. Gamepad output received from the host is echoed, matching the Pico SET_REPORT callback's interrupt-output behavior; keyboard LED output is consumed. Errors reach the macro instead of allowing silent progress after a disconnected endpoint.

The macro uses the Pico's nominal timing (32 ms key hold, 200 ms enter gap, 750 ms menu waits). Python scheduling and USB queueing add latency, so wall-clock timing is not guaranteed to be identical.

### What still differs from Pico USB firmware

Stock Linux `f_hid` assigns endpoint addresses and generates interface/HID descriptors. In upstream Linux 5.10, it advertises HID 1.01 rather than the Pico's 1.11, uses a 10 ms full-speed polling interval rather than 1 ms, and stalls control-endpoint SET_REPORT requests. Reading `/dev/hidg0` handles interrupt OUT packets, not those stalled control requests. The board vendor's kernel may differ; inspect its source or capture USB traffic before attributing a console failure to any particular difference.

Strict USB-level equivalence would require a custom gadget driver or a Raw Gadget backend and kernel support, plus matching endpoints supported by the board's USB controller. Continuous reports and matching macro traces alone do not establish Switch compatibility. References: [Linux 5.10 HID gadget driver](https://github.com/torvalds/linux/blob/v5.10/drivers/usb/gadget/function/f_hid.c), [Pico USB implementation](../PicoSwitch/src/switchController/SwitchUsb.cpp).

### Host-side verification

```bash
python3 -B -m unittest discover -s LuckfoxSwitch -p 'test_*.py' -v
```

Run from the repository root. Tests use actual local socket I/O to verify continuous reports, A states while keyboard input changes, gamepad echoes, LED output, shutdown, and disconnect errors. They compare report descriptors and endpoint packet sizes against Pico source, and compare the Python state/duration trace against the actual Pico header generator for all printable keys and the generated payload. Generate `PicoSwitch/payload.txt` first to include full-payload parity. These are host-side checks, not a console test.

## Board-Specific Setup

### Luckfox Pico Pro: onboard flash without an SD card

Use Luckfox's [official image downloads](https://wiki.luckfox.com/Luckfox-Pico-RV1106/Getting-Started/overview) and [flashing instructions](https://wiki.luckfox.com/Luckfox-Pico-RV1106/Getting-Started/Image-flashing/). Flashing replaces the existing NAND system. The setup tested here used `Luckfox_Pico_Pro_Max_Flash_250607/update.img` and macOS `upgrade_tool` v2.44. Its firmware SHA-256 was `1ebd461a574e6e644a42c3be211aa111df133310f9ebb2e918c1652a45fd4ff6`.

1. Remove the SD card. Hold **BOOT** while connecting USB to the computer, then release it. `upgrade_tool LD` should show Maskrom.
2. If preserving the old installation, load the matching `download.bin` into RAM with `upgrade_tool DB download.bin`, read flash geometry with `upgrade_tool RFI`, and back up the reported sectors with `upgrade_tool RL 0 <sector-count> nand-lba.img`. The tested unit reported `0x7fc00` sectors. Keep the backup private; it can contain device configuration and credentials.
3. Flash the matching official image: `upgrade_tool UF update.img` (Luckfox documents running the tool with `sudo` if needed). Wait for `Upgrade firmware ok.` and for ADB to return after boot.
4. Install using the ADB commands above. The tested stock image included Python 3.11.6, Bash, BusyBox init, and HID configfs support. `/proc/cmdline` contained `root=ubi0:rootfs`, confirming NAND boot.
5. Prepare Switch chat, select a boot mode, run `sync`, and move USB as described above. **BOOT is only for flashing; leave it released for normal use.**

No computer connection is required while running on the Switch. If already armed, reconnecting USB to a computer starts typing again. To disable it, use Ethernet/serial access to set the mode to `off` and stop the service; USB networking and ADB are unavailable while the HID gadget is active. Holding BOOT during a computer connection enters recovery without running the macro, but does not itself clear the saved boot mode.

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
| `USB host is not accepting HID reports` or endpoint shutdown | Host is disconnected, not polling, or USB mode changed | Check the cable/OTG port and gadget state. The macro stops rather than silently skipping inputs. |
| Macro types but Switch ignores input | Wrong language settings | Set Switch system language and USB Keyboard to English(US). |
| Text works, but A must be pressed manually | Gamepad recognition or input path needs investigation | Use `--test-controller` before replaying the payload; do not assume keyboard recognition proves controller recognition. |
