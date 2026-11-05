# Flash and set up a Luckfox Pico Pro without an SD card

This guide installs Buildroot onto the **Luckfox Pico Pro's onboard SPI NAND**, then installs CivSwitch and enables standalone startup. No computer or SD card is needed while using it on the Switch. It is not a UF2 workflow: Raspberry Pi Pico1/Pico2 UF2 files cannot be flashed to this Linux board.

The procedure below was performed on a Pico Pro using macOS, `upgrade_tool` v2.44, and `Luckfox_Pico_Pro_Max_Flash_250607/update.img`. Onboard boot, installation persistence, and neutral reports through both HID endpoints were verified. A physical Switch run typed lines 1–9 correctly, then stalled with a repeating `f`. The source now fixes two reproducible transport bugs; a Switch retest, full payload completion, and loader startup remain unconfirmed. Use the current repository rather than an older release archive.

## 1. Download the tools and firmware

On the computer, install Git, Node.js/npm (to generate the payload), and Google's [Android SDK Platform Tools](https://developer.android.com/tools/releases/platform-tools) for `adb`.

Download and extract these files:

- The **Pro/Max Flash Buildroot image** from [Luckfox's official image downloads](https://wiki.luckfox.com/Luckfox-Pico-RV1106/Getting-Started/overview/). Select the Pro/Max SPI NAND/Flash image. Ubuntu and MicroSD/TF images are for a different boot setup.
- The matching host version of **Rockchip upgrade_tool**, linked in [Luckfox's flashing instructions](https://wiki.luckfox.com/Luckfox-Pico-RV1106/Getting-Started/Image-flashing/). The tested Mac version was v2.44; use Luckfox's OS compatibility table for your computer.

The extracted firmware directory should contain `update.img` and its matching `download.bin`. The tested image's SHA-256 was:

```text
1ebd461a574e6e644a42c3be211aa111df133310f9ebb2e918c1652a45fd4ff6  update.img
```

That hash identifies the tested image only; a newer official image will have a different hash. Do not use another board's firmware.

In a terminal, set these paths to the files you extracted (replace the example paths):

```bash
upgrade_tool="/absolute/path/to/upgrade_tool"
firmware_dir="/absolute/path/to/Luckfox_Pico_Pro_Max_Flash_250607"
chmod +x "$upgrade_tool"
```

The commands below use `sudo` as in Luckfox's documentation. On the tested Mac, the tool also worked without it.

## 2. Enter flashing mode

1. Unplug the Luckfox and remove its SD card.
2. Hold **BOOT**, connect its USB-C port to the computer with a data cable, then release BOOT.
3. Check detection:

```bash
sudo "$upgrade_tool" LD
```

Expect one Rockchip device, normally VID `2207`, PID `110c`, with `Mode=Maskrom`. If it is absent, check the data cable and repeat BOOT while connecting. Do not proceed until the correct board is detected.

If CivSwitch was already enabled, entering Maskrom prevents it from typing into the computer. It does not erase or disable the saved configuration.

## 3. Back up the existing NAND (optional)

**Flashing replaces the onboard system and its configuration.** If you want to preserve it, download the matching loader into RAM and read the flash geometry:

```bash
sudo "$upgrade_tool" DB "$firmware_dir/download.bin"
sudo "$upgrade_tool" RFI
```

On the tested Pro, `RFI` reported `Flash Size: 255MB,value=0x7fc00`. For that reported sector count, the backup command was:

```bash
sudo "$upgrade_tool" RL 0 0x7fc00 nand-lba.img
shasum -a 256 nand-lba.img > nand-lba.img.sha256
```

Use your board's reported sector count if it differs. Wait for reading to finish before flashing. Keep this backup private; it may contain credentials. This is a logical sector backup, not a raw NAND dump including out-of-band data, and restoration of this backup has not been tested.

## 4. Flash Buildroot and verify onboard boot

```bash
sudo "$upgrade_tool" UF "$firmware_dir/update.img"
```

Keep USB connected until the tool finishes with **`Upgrade firmware ok.`** It then resets the board. Leave BOOT released and wait for Linux to boot, then run:

```bash
adb devices -l
adb shell 'cat /proc/device-tree/model; echo; cat /proc/cmdline'
adb shell 'python3 --version; command -v bash; command -v start-stop-daemon; df -h /'
```

Expect the Luckfox model, `root=ubi0:rootfs` and `storagemedia=mtd` in the kernel command line, plus Python, Bash, and `start-stop-daemon`. These confirm the intended NAND system with the required userspace tools. The tested image included Python 3.11.6 and about 137 MB free after installation.

The stock ADB device may identify itself as `Nexus_4` / `mako`; that is the vendor daemon's placeholder identity. Confirm the actual board through `/proc/device-tree/model`. If several ADB devices are connected, select this board with `adb -s <serial>` for subsequent commands.

## 5. Generate and install CivSwitch

Use the current repository so that the BusyBox startup scripts and continuous HID reporting are included:

```bash
git clone https://github.com/phantomptr/CivSwitch.git
cd CivSwitch
npm --prefix PicoSwitch install
(cd PicoSwitch && node compress.js)
cp PicoSwitch/payload.txt LuckfoxSwitch/payload.txt

adb push LuckfoxSwitch /tmp/LuckfoxSwitch
adb shell 'cd /tmp/LuckfoxSwitch && bash install.sh'
```

If you already have the repository, use that checkout instead of cloning again. Installation should report **automatic typing is OFF**. Check the installed files before enabling it:

```bash
adb shell 'cat /etc/civswitch-mode; ls -l /etc/init.d/S99zz-civswitch /opt/civswitch/run-on-boot.sh'
adb shell 'sha256sum /opt/civswitch/payload.txt'
```

The mode should be `off`. You can compare the payload checksum with `shasum -a 256 LuckfoxSwitch/payload.txt` on the Mac. `/tmp` is temporary; the installer copies the runtime files into persistent `/opt/civswitch` and `/etc`.

## 6. Prepare the Switch, then enable startup

Use the physical Civilization VII cartridge running **version 1.0.0**, with the Switch language and USB keyboard layout set to **English (US)**. Open **MULTIPLAYER → WIRELESS → CREATE GAME → HOST LOBBY → CHAT**, and focus the chat input.

While the Luckfox is still connected to the computer, enable the full payload for the **next boot**:

```bash
adb shell 'echo payload > /etc/civswitch-mode; sync; cat /etc/civswitch-mode'
```

After it prints `payload`, **unplug USB from the computer and plug the Luckfox into the prepared Switch. Leave BOOT released and the SD card out.** The mode change does not start the macro in the current boot.

**Do not run `reboot` or `poweroff` on the computer after enabling it.** On the tested Pro, `poweroff` restarted the board while USB power remained connected, and it began typing into the computer. Every normal boot in payload mode types into whichever USB host is connected.

Allow Linux to boot and the payload to type, approximately 15–20 minutes. No continuing computer connection is required. Unplugging and reconnecting starts the payload again from the beginning.

## Controller diagnostic and recovery

For a diagnostic boot instead of the full payload, set this mode while ADB is available, then move USB in the same way:

```bash
adb shell 'echo controller-test > /etc/civswitch-mode; sync'
```

It sends five A presses, then neutral reports, without typing text. Use a Switch screen where A has a visible effect. See the [controller diagnostic instructions](README.md#check-controller-input-before-running-the-payload) if the game requires manual A presses after Enter.

When HID mode is active, USB ADB/networking are replaced. If Ethernet or serial access is available, disable startup and stop the macro on the board with:

```bash
echo off > /etc/civswitch-mode
/etc/init.d/S99zz-civswitch stop
sync
```

Rebooting with mode `off` restores stock USB startup. Logs are in `/var/log/civswitch.log`; the stock image may keep `/var/log` in temporary storage, so retrieve them before rebooting.

If you have only USB access, unplug the board and hold BOOT while reconnecting to the computer. This enters recovery without running the macro. You can repeat the firmware flash and installation above; **this replaces the NAND system again**. BOOT alone does not clear payload mode, so reconnecting normally before disabling or reflashing will type again.
