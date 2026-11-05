# LuckfoxSwitch (Linux USB Gadget)

CivSwitch port for **Linux SBCs with USB gadget support**. Replaces the RP2040 firmware with a Python script and a shell-based USB gadget setup. It uses the Pico firmware's VID/PID (Hori 0x0F0D:0x0092), HID report descriptors, macro sequence, and nominal hold/wait times.

Verified end-to-end on **Switch 2** on 2026-11-05 using a Luckfox Pico Pro on its onboard Buildroot flash. The complete 87/87 payload reached the Remote JS Loader, and the persistent `XSTRIG.Civ7Save` installer completed. The final run made 357 gamepad and 23,437 keyboard state writes, kept the USB controller configured, left roughly 96 MB available, and did not need swap. The former line-10 stall came from unordered or repeated HID transfers; current code waits for endpoint acceptance, coalesces unchanged reports, and performs safe-boundary garbage collection. The payload remains the exact 87-line artifact with SHA-256 `3b0f97d015fd465fcf60bbd302aebd56bc2268730d03188f543cf791f9fb240f`.

`civswitchctl start` leaves the job process in a neutral HID wait after visible output so the Switch can be inspected. `civswitchctl status` therefore still reports `running` after a successful payload; run `civswitchctl stop` when finished. Luckfox verification is on Switch 2; Switch 1 validation is separate, while the Pico1/Pico2 builds remain available in each release.

## Compatible Boards

### Luckfox hardware coverage

The table below was checked against Luckfox's current product pages on 2026-11-05. "Verified" means a physical Switch 2 run; "same path" means the same official firmware and I/O layout but no separate CivSwitch hardware run; "candidate" means the board advertises a USB device-capable port, but its own Buildroot image and required userspace packages still need validation.

| Luckfox family | Models checked | USB and memory | CivSwitch status |
| --- | --- | --- | --- |
| **Pico Pro / Max** | Pro (RV1106G2), Max (RV1106G3) | USB 2.0 Host/Device; 128 MB or 256 MB; 256 MB SPI NAND | **Pro: Switch 2 verified.** **Max: same path.** Both use the official `Luckfox_Pico_Pro_Max_Flash_250607` image and identical board layout. |
| **RV1103 Pico family** | Pico, Mini A, Mini B, Plus, WebBee | USB 2.0 Host/Device; 64 MB RAM with about 35 MB usable under the stock 24 MB camera reservation | **Candidate, not verified.** Each model needs its own RV1103 Buildroot image plus Python 3, Bash, `start-stop-daemon`, configfs, and `usb_f_hid`. Pico and Mini A require microSD; Mini B, Plus, and WebBee can use SPI NAND. Do not flash the Pro/Max image. |
| **Pico Ultra family** | Ultra, Ultra W, Ultra B, Ultra BW | USB 2.0 Host/Device; 128 MB or 256 MB; 8 GB eMMC | **Candidate, not verified.** Use the model-specific eMMC Buildroot image and select USB Device mode when required. Camera CMA reduction and userspace checks still apply. |
| **Pico Pi family** | Pi A, Pi A W, Pi B, Pi B W | Dedicated USB Device port; 128 MB or 256 MB; 8 GB eMMC | **Candidate, not verified.** Hardware and Buildroot support are present; the DIP switch must be in USB Device mode. |
| **Pico Zero** | Zero | USB 2.0 Host/Device; 256 MB; 8 GB eMMC | **Candidate, not verified.** Use the Zero eMMC Buildroot image and confirm device mode, Python, and HID gadget support. |
| **Pico 86-Panel** | 0208, 0408, 1208, 1408 | USB Device; 128 MB or 256 MB; 8 GB eMMC | **Candidate, not verified.** Use the model-specific eMMC Buildroot image and confirm the userspace requirements. |

All current Luckfox Pico families advertise USB 2.0 device capability, so the CivSwitch design is portable to them. That is not the same as a tested image: the checked-in flashing guide, `env-cma4k.img`, static-address defaults, and stock USB restore path are specifically for Pro/Max. A candidate board must satisfy the generic requirements below and needs its own physical Switch test before it should be called supported.

Official references: [Pro/Max](https://wiki.luckfox.com/Luckfox-Pico-Pro-Max/), [RV1103 family](https://wiki.luckfox.com/Luckfox-Pico/Luckfox-Pico-RV1103/), [Ultra](https://wiki.luckfox.com/Luckfox-Pico-Ultra/), [Pico Pi](https://wiki.luckfox.com/Luckfox-Pico-Pi/), [Zero](https://wiki.luckfox.com/Luckfox-Pico-Zero/), [86-Panel](https://wiki.luckfox.com/Luckfox-Pico-86-Panel/), [WebBee](https://www.luckfox.com/Luckfox-WebBee), and [memory reservations](https://wiki.luckfox.com/Luckfox-Pico-RV1106/FAQ/Memory-Storage/).

### Other Linux SBCs

| Board | USB Port | Status |
| ----- | -------- | ------ |
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

**Starting with a Luckfox Pico Pro or Max that needs flashing?** Follow the [complete onboard-flash guide](FLASHING.md) first. It covers firmware extraction, the required `rk_dma_heap_cma=4K` environment, BOOT/Maskrom flashing, ADB checks, installation, host SSH setup, and moving USB to the Switch without an SD card.

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

### 3. Configure the Mac-side SSH alias

The board installer configures the Linux addresses and normalizes the root SSH permissions. Copy the repository's single helper into the Mac path used by SSH:

```bash
install -d ~/bin
install -m 755 LuckfoxSwitch/luckfox-ssh-connect ~/bin/luckfox-ssh-connect
```

Add one host entry to `~/.ssh/config`:

```sshconfig
Host yunpengs-luckfox
    User root
    HostKeyAlias yunpengs-luckfox
    ProxyCommand ~/bin/luckfox-ssh-connect auto
```

The helper tries Ethernet `192.168.86.198`, USB RNDIS `192.168.77.1`, then the Luckfox ADB device. Those are internal transports for the same board; do not create separate `-eth`, `-usb`, or `-adb` aliases.

### 4. Control manually over Ethernet

Prepare the Switch **before** powering the board: the repo requires the physical Civ VII cartridge running game version **1.0.0**, with system language and USB keyboard layout set to **English (US)**. Navigate to **MULTIPLAYER → WIRELESS → CREATE GAME → HOST LOBBY → CHAT** and focus the chat input.

On the verified Pro/Max path, the installer configures `eth0` as `192.168.86.198/24` with gateway `192.168.86.1` and, when the stock RNDIS interface is present, configures `usb0` as `192.168.77.1/24`. Models without Ethernet skip that address instead of failing installation. The installer leaves the stock RNDIS+ADB gadget in place and does **not** arm CivSwitch for the next boot. Test the single Mac SSH alias:

```bash
ssh yunpengs-luckfox
```

The Mac's RNDIS service can use `192.168.77.2/24` with router `192.168.77.1` when Ethernet is unavailable. The helper still reaches the same `yunpengs-luckfox` host through any transport that works.

Nothing types until you run a control command:

```bash
civswitchctl test          # uncounted R, then A/B/X/Y/L; HID stays active
civswitchctl status        # job, memory, swap, UDC state, recent log
civswitchctl stop          # release HID and restore RNDIS+ADB

civswitchctl start-line 1  # type one line, then restore stock USB
civswitchctl start         # full payload; HID stays active after completion
```

While a job is active, USB SSH/RNDIS and ADB are replaced by the two HID interfaces. Ethernet SSH remains available, so run `civswitchctl stop` from that session when finished. A command-line `stop` is a safe boundary: the job runner kills the macro, releases HID, and asks `S50usbdevice` to restore RNDIS+ADB. If Ethernet is unavailable, use serial or keep the existing ADB connection before starting a job.

The late `/etc/init.d/S99zz-civswitch` startup exists only for an explicit opt-in. It reads `/etc/civswitch-mode`; the installer sets it to `off`, and `civswitchctl` also writes `off` so a manual run never arms the next boot. Logs are persisted under `/opt/civswitch/logs/`: `current.log` is the latest run and `previous.log` is retained when the next run starts. Each line boundary records the macro line, total line count, endpoint generations, queued report, write/output counts, and USB controller state; a failed run also records the current character, phase, and traceback.

For the **Luckfox Pico Pro / Max onboard SPI NAND**, use the official Pro/Max **Flash Buildroot** image, not the Ubuntu SD-card image. Its 256 MB flash is sufficient; an SD card is optional and is used only for swap. The installer supports BusyBox init as well as systemd. On a systemd board the unit files remain available but are disabled by default; use `civswitchctl` for manual operation.

### Verified Switch 2 configuration

| Item | Tested value |
| --- | --- |
| Board | Luckfox Pico Pro, reported as `Luckfox Pico Pro Max` |
| Firmware | Official `Luckfox_Pico_Pro_Max_Flash_250607` Buildroot image on SPI NAND |
| Camera memory | `rk_dma_heap_cma=4K`, generated from the image's `env.img` with `make_boot_env.py` |
| Linux memory | About 120 MiB `MemTotal`; about 96 MiB `MemAvailable` after the full payload |
| Ethernet | `192.168.86.198/24`, gateway `192.168.86.1` |
| USB fallback | RNDIS `192.168.77.1/24`, ADB forwarded to port 22 by the host helper |
| Host access | One `yunpengs-luckfox` SSH entry using `luckfox-ssh-connect auto` |
| Control | Manual `civswitchctl test`, `start-line`, `start`, `status`, and `stop` |
| Storage | Onboard NAND boot; optional 1 GiB `CIVSWAP` partition, unused during the verified run |
| Result | 87/87 payload, Remote JS Loader reachable at port 9026, persistent installer complete |

### Check controller input before running the payload

If typing works but you have to press A manually after Enter, the controller path has not been validated. Connect the OTG port to the prepared Switch and run over Ethernet SSH:

```bash
civswitchctl test
```

The test waits four seconds after Linux sees USB configured, sends an uncounted `R` warm-up for 350 ms, waits two seconds, then sends `A`, `B`, `X`, `Y`, and `L`. Each counted button is held for 200 ms with a one-second gap, and both HID interfaces then remain neutral. No payload text is sent. On the Switch input-test screen, use `R` to confirm the controller pipeline woke, then verify `A → B → X → Y → L` in order. The production preamble remains five A presses because that is the Pico behavior. Run `civswitchctl stop` when finished.

### One line at a time

```bash
civswitchctl start-line 10
civswitchctl status
```

`start-line` omits the five-A preamble and assumes the Switch is already at chat. It runs one indexed line, releases both HID states, and restores the stock USB gadget automatically. It remains useful when checking a specific chat state or future payload revision; the formerly failing line 10 has passed on the verified Switch 2 run.

## How It Works

`usb-gadget-hid.sh` uses Linux USB configfs to create a composite HID device:

- **Interface 0** — Switch gamepad (8-byte input reports, 64-byte maximum report size)
- **Interface 1** — Boot-protocol keyboard (8-byte reports)

`civswitch.py` reads `payload.txt` and types each line into chat:

1. **Preamble** — press A × 5 to dismiss menus
2. **Per line** — Esc → wait → A → wait → type the line → Enter × 2
3. **Between lines** — A press as separator

Two independent nonblocking workers keep the gamepad and keyboard states synchronized. USB HID input reports are stateful, so each worker transmits a report only when its state changes and then sends a 30-second keepalive instead of repeating every millisecond. This preserves key-down/key-up ordering while avoiding the Luckfox `f_hid`/`dwc2` path's roughly 1 KiB kernel-memory loss per completed write, which previously exhausted RAM on payload line 10. The macro waits for every state-changing write before advancing. The verified Switch path uses the stock `rockchip` gadget at high speed with a normal `f_hid` interrupt-OUT endpoint; host output is read from `/dev/hidg0` and echoed back through its input endpoint. Keyboard LED output is consumed. Errors reach the macro instead of allowing silent progress after a disconnected endpoint.

Empty host output packets are ignored rather than interpreted as stream EOF: [Luckfox's HID gadget driver](https://github.com/LuckfoxTECH/luckfox-pico/blob/main/sysdrv/source/kernel/drivers/usb/gadget/function/f_hid.c) can return zero bytes for a completed zero-length USB OUT transfer. Optional LED sysfs failures cannot stop HID reporting. These cases have regression tests, including an empty packet arriving while `f` is held and subsequent key/release delivery.

The macro uses the Pico's nominal timing (32 ms key hold, 200 ms enter gap, 750 ms menu waits). Python scheduling and USB queueing add latency, so wall-clock timing is not guaranteed to be identical. A temporary lack of host polling no longer pauses the macro. If neither HID endpoint completes a USB transfer for 60 seconds, the worker records a transport error and the next macro state update reports it. Before starting each line, the macro checks `/proc/meminfo` and stops safely below 6 MiB available instead of risking an OOM kill while a key is held. After each line's keyboard state is released, it runs a Python garbage collection at that safe boundary. Kernel HID allocations are addressed separately by coalescing unchanged reports and by the reduced camera CMA reservation in the flashing guide.

The first nine displayed messages are decoder-armor chunks. Message 10 starts filling the next chunk index; a full-looking chat pane at that point by itself does not mean the decoder has run or that the next keystroke should fail. The retained diagnostic log distinguishes those cases:

- `line-complete` with the next `line-start`: the program kept progressing.
- `failure` with `typing_position`: the macro stopped while a key was active; compare `requested` and `last_queued`.
- `line-start` with no later event: the process or board stopped before Python could record an exception.

### What still differs from Pico USB firmware

| Behavior | Pico1 / Pico2 | Current Luckfox port |
| --- | --- | --- |
| Macro report bytes and nominal holds/waits | Shared source and generated macro | Compared against the Pico generator by host tests |
| USB transfer completion | TinyUSB endpoint readiness | Linux write completes when the host accepts the transfer; the macro waits for each state change |
| Endpoint addresses | Fixed in `SwitchConsts.h` | Assigned by the Linux gadget framework |
| HID version | 1.11 | Stock HID driver advertises 1.01 |
| USB speed | Full-speed Pico device | High-speed on the verified Luckfox `rockchip` gadget |
| Initial delay | One second from Pico boot | Four seconds after Linux sees USB configured |
| Gamepad control GET_REPORT | Callback returns 8 bytes | Linux returns zeros up to configured report length / host request length |
| Control SET_REPORT | TinyUSB callback handles it | The verified path sends host output through interrupt OUT instead |
| Gamepad endpoints | Interrupt IN and OUT | Interrupt IN and OUT on the stock `f_hid` function |

Matching the macro trace does not establish identical USB behavior. A custom gadget implementation would be needed to control the remaining descriptor and request differences; even then, Linux scheduling, the USB controller, and actual Switch acceptance require measurement. No USB traffic capture comparison against a working physical Pico1/Pico2 has been performed.

Stock Linux `f_hid` assigns endpoint addresses and generates interface/HID descriptors. In upstream Linux 5.10, it advertises HID 1.01 rather than the Pico's 1.11, and the generated descriptor and endpoint behavior are not byte-identical to TinyUSB. Physical testing is authoritative: reusing Luckfox's `rockchip` gadget with its negotiated high-speed link and normal interrupt OUT is the only configuration that stayed configured and delivered the accepted `A, B, X, Y, L` sequence. Dedicated-gadget, forced full-speed, and control-pipe-only variants were rejected during this bring-up.

Strict USB-level equivalence would require a custom gadget driver or a Raw Gadget backend and kernel support, plus matching endpoints supported by the board's USB controller. Matching macro traces alone do not establish Switch compatibility. References: [Linux 5.10 HID gadget driver](https://github.com/torvalds/linux/blob/v5.10/drivers/usb/gadget/function/f_hid.c), [Pico USB implementation](../PicoSwitch/src/switchController/SwitchUsb.cpp).

### Host-side verification

```bash
python3 -B -m unittest discover -s LuckfoxSwitch -p 'test_*.py' -v
```

Run from the repository root. Tests use actual local socket I/O to verify state-change reports, key ordering while keyboard input changes, gamepad echoes, LED output, shutdown, and disconnect errors. They compare report descriptors and endpoint packet sizes against Pico source, and compare the Python state/duration trace against the actual Pico header generator for all printable keys and the generated payload. Generate `PicoSwitch/payload.txt` first to include full-payload parity. These are host-side checks, not a console test.

## Board-Specific Setup

### Luckfox Pico Pro / Max: onboard flash without an SD card

Follow the [step-by-step flashing and installation guide](FLASHING.md). It includes the tested firmware version and checksum, the `rk_dma_heap_cma=4K` boot-environment override, optional NAND backup commands, boot verification, and recovery. The physical run used Pro; Max shares the same board layout and Pro/Max firmware. **BOOT is only for flashing; leave it released for normal use.** No computer connection is required while running on the Switch.

The installer writes persistent Ethernet and USB-network addresses. The repository includes the Mac `luckfox-ssh-connect` helper and the single `yunpengs-luckfox` host stanza shown above. The installer also enables an SD-card swap partition when one labeled `CIVSWAP` is present; swap is optional and NAND remains the boot device.

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

The gadget setup script:

- **Reuses Luckfox's `rockchip` gadget** — the verified enumeration path; a dedicated `civswitch` gadget is only a fallback on other boards
- **Unbinds the current UDC owner** — the vendor USB helper is stopped while CivSwitch is active and is restarted by `civswitchctl stop`
- **Keeps the negotiated high-speed link** — forcing full speed during bring-up did not fix Switch reconfiguration
- **Strings directory** — detects `0x409` vs `0x0409` naming
- **Kernel modules** — loads `configfs`, `libcomposite`, `usb_f_hid` if they aren't built-in
- **UDC** — finds the first available USB Device Controller

## Restoring Default USB Mode

```bash
civswitchctl stop
# or, to force the restore path:
civswitchctl restore
```

On Luckfox, this restarts RNDIS+ADB. On other boards, it disables CivSwitch services — reboot to restore defaults.

## Updating the Payload

```bash
scp payload.txt yunpengs-luckfox:/opt/civswitch/payload.txt
ssh yunpengs-luckfox 'sync'
```

## Manual Operation

```bash
ssh yunpengs-luckfox 'civswitchctl status'
```

Stock USB/ADB remains active at boot. The HID gadget is bound only by `civswitchctl start`, `start-line`, or `test`. Explicit auto-run remains available for diagnostic use by writing `/etc/civswitch-mode`, but it is not enabled by the installer.

## Troubleshooting

| Problem | Cause | Fix |
| ------- | ----- | --- |
| No `/dev/hidg0` after running gadget script | USB OTG not enabled | Check `ls /sys/class/udc/`. On Pi Zero: add `dtoverlay=dwc2`. Check `dmesg \| grep -i gadget`. |
| Lost USB SSH/ADB after starting CivSwitch | HID gadget replaces USB networking | Ethernet SSH remains available; run `civswitchctl stop`. |
| `USB host is not accepting HID reports` or endpoint shutdown | Host is disconnected, not polling, or USB mode changed | Check `/opt/civswitch/logs/current.log` and the cable/OTG port. The macro stops rather than silently skipping inputs. |
| Macro types but Switch ignores input | Wrong language settings | Set Switch system language and USB Keyboard to English(US). |
| Text works, but A must be pressed manually | Gamepad recognition or input path needs investigation | Run `civswitchctl test` before replaying the payload; do not assume keyboard recognition proves controller recognition. |
