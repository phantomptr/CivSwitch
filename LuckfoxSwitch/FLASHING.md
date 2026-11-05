# Flash and set up a Luckfox Pico Pro without an SD card

This guide installs Buildroot onto the **Luckfox Pico Pro's onboard SPI NAND**, then installs CivSwitch with manual control over Ethernet SSH. No computer or SD card is required while using it on the Switch, although an optional card can provide swap. It is not a UF2 workflow: Raspberry Pi Pico1/Pico2 UF2 files cannot be flashed to this Linux board.

The procedure below was performed on a Pico Pro using macOS, `upgrade_tool` v2.44, and `Luckfox_Pico_Pro_Max_Flash_250607/update.img`. The final Switch 2 run completed **87/87**, brought up the Remote JS Loader, and installed the persistent `XSTRIG.Civ7Save` trigger. It made 357 gamepad and 23,437 keyboard state writes, kept USB configured, retained about 96 MB available, and did not need swap. Every key transition now waits for endpoint acceptance; unchanged reports are coalesced and Python memory is reclaimed at safe line boundaries. Logs remain on NAND across boots.

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

Extract the firmware payloads with the same `upgrade_tool`. This creates the `env.img`, `download.bin`, and partition images used below; the board does not need to be connected for extraction:

```bash
sudo "$upgrade_tool" EXF "$firmware_dir/update.img" "$firmware_dir/extracted"
```

Expect `Extract ok.` and a 262144-byte `extracted/env.img`.

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

Generate the prepared environment from the extracted vendor image. The helper verifies the source CRC, replaces the camera reservation with the tested value, preserves the fixed-sector size, and recalculates the CRC:

```bash
python3 LuckfoxSwitch/make_boot_env.py \
  "$firmware_dir/extracted/env.img" \
  "$firmware_dir/extracted/env-cma4k.img"
```

For the tested image, the result must be:

```text
08c4d8f7f5505ba19409a4d686e2d5ccae5366ef9776d9166d815b22fc4417d3  env-cma4k.img
```

The helper output includes the SHA-256. A newer official firmware image may produce a different digest; do not continue if the tool's CRC validation or the single-argument check fails.

Then flash the system and write the prepared environment sector:

```bash
sudo "$upgrade_tool" UF "$firmware_dir/update.img" -noreset
sudo "$upgrade_tool" WL 0 512 "$firmware_dir/extracted/env-cma4k.img"
sudo "$upgrade_tool" RL 0 512 /tmp/env-read.img
cmp "$firmware_dir/extracted/env-cma4k.img" /tmp/env-read.img
sudo "$upgrade_tool" RD
```

`-noreset` is intentional: it leaves the firmware's `download.bin` loader resident in RAM so the raw environment-sector write can run without `PL`. Do not use `PL` on the RV1106; it does not work on this board. Keep USB connected until the tool finishes with **`Upgrade firmware ok.`** A literal `0M` is not a valid camera value because the Rockchip driver treats it as unset and falls back to its large vendor default. If `cmp` fails, do not boot; re-enter Maskrom and repeat the environment write.

After `RD`, leave BOOT released and wait for Linux to boot, then run:

```bash
adb devices -l
adb shell 'cat /proc/device-tree/model; echo; cat /proc/cmdline; grep -E "MemTotal|CmaTotal" /proc/meminfo'
adb shell 'python3 --version; command -v bash; command -v start-stop-daemon; df -h /'
```

Expect the Luckfox model, `root=ubi0:rootfs`, `storagemedia=mtd`, and `rk_dma_heap_cma=4K` in the kernel command line, plus Python, Bash, and `start-stop-daemon`. `MemTotal` should be near 120 MB and `CmaTotal` should be approximately 4 KiB. If the command line still says `1M` or omits the override, do not continue. The tested image included Python 3.11.6 and about 137 MB free after installation.

The stock ADB device may identify itself as `Nexus_4` / `mako`; that is the vendor daemon's placeholder identity. Confirm the actual board through `/proc/device-tree/model`. If several ADB devices are connected, select this board with `adb -s <serial>` for subsequent commands.

## 5. Generate and install CivSwitch

Use the current repository so that the BusyBox startup scripts, manual controller, and ordered HID transport are included:

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
adb shell 'cat /etc/civswitch-mode; ls -l /usr/bin/civswitchctl /etc/init.d/S99zz-civswitch /etc/init.d/S99zz-ipstatic'
adb shell 'ls -ld /root /root/.ssh; ls -l /root/.ssh/authorized_keys'
adb shell 'ifconfig eth0; ifconfig usb0; cat /etc/init.d/S99zz-ipstatic'
adb shell 'sha256sum /opt/civswitch/payload.txt'
```

The mode should be `off`, `eth0` should show `192.168.86.198/24`, and `usb0` should show `192.168.77.1/24` when the stock RNDIS interface exists. `/root`, `/root/.ssh`, and `authorized_keys` must be owned by `root`; the installer normalizes their modes so OpenSSH `StrictModes` accepts the key. The installer creates the persistent `/etc/init.d/S99zz-ipstatic` script. You can compare the payload checksum with `shasum -a 256 LuckfoxSwitch/payload.txt` on the Mac. `/tmp` is temporary; the installer copies the runtime files into persistent `/opt/civswitch` and `/etc`.

## 6. Configure the Mac host alias

The board installer does not modify the Mac's `.ssh` directory. Install the repository helper and define one host entry:

```bash
install -d ~/bin
install -m 755 LuckfoxSwitch/luckfox-ssh-connect ~/bin/luckfox-ssh-connect
```

Add this to `~/.ssh/config`:

```sshconfig
Host yunpengs-luckfox
    User root
    HostKeyAlias yunpengs-luckfox
    ProxyCommand ~/bin/luckfox-ssh-connect auto
```

The helper's fallback order is Ethernet `192.168.86.198` → USB `192.168.77.1` → the board's ADB device. All three reach the same system under the single host name. Keep one entry; separate transport aliases make failures harder to diagnose. Verify it before disconnecting ADB:

```bash
ssh yunpengs-luckfox 'hostname; cat /proc/device-tree/model; echo; cat /proc/cmdline'
```

If USB RNDIS remains available, configure the Mac interface for that fallback as `192.168.77.2/24` with router `192.168.77.1`. Ethernet SSH remains available while CivSwitch owns the OTG port.

## 7. Prepare the Switch, then control manually

Use the physical Civilization VII cartridge running **version 1.0.0**, with the Switch language and USB keyboard layout set to **English (US)**. Open **MULTIPLAYER → WIRELESS → CREATE GAME → HOST LOBBY → CHAT**, and focus the chat input.

Connect the board's Ethernet port to the same LAN as the Mac, leave the OTG USB cable disconnected from the computer, and connect it to the prepared Switch only after Linux has booted. From the Mac, the single supported SSH command is:

```bash
ssh yunpengs-luckfox
```

Open the Switch's controller input-test screen, connect the Luckfox OTG cable, then run over Ethernet SSH:

```bash
civswitchctl test
civswitchctl status
civswitchctl stop
```

The test waits four seconds after USB is configured and sends an uncounted `R` warm-up for 350 ms. After another two seconds it sends `A`, `B`, `X`, `Y`, and `L`, with a 200 ms hold and one-second gap for each counted button. Confirm the warm-up wakes the controller pipeline and all five distinct counted buttons appear in order, then stop the test and move to chat.

To inspect one exact payload line without the five-A preamble:

```bash
civswitchctl start-line 10
```

The verified path is the full run:

```bash
civswitchctl start
civswitchctl status
```

The full payload takes approximately 15–20 minutes. Follow progress with:

```bash
tail -f /opt/civswitch/logs/current.log
```

At `87/87`, the log prints `CivSwitch: macro complete.` and the Remote JS Loader becomes available on port 9026. `start` and `test` keep HID active after visible output, so `civswitchctl status` still reports `running` during the neutral wait; run `civswitchctl stop` to release HID and restore stock RNDIS+ADB. `start-line N` exits and restores stock USB automatically after that line. Do not use `reboot` or `poweroff` while USB remains connected to the computer: the tested Pro restarted and could type into the Mac.

## Optional SD-card swap

NAND remains the boot device. A card is used only for emergency swap space; the verified 87/87 run did not consume swap. The optional layout is a DOS MBR with one Linux-swap partition (`0x82`) starting at LBA 2048, labeled `CIVSWAP`, containing a 1 GiB swap image. `civswitchctl` runs `setup-swap.sh` before every job, so the card can be inserted before boot or before a manual run.

Use `CIVSWAP-assets.tar.gz` from the release, or generate the same files from source:

```bash
python3 LuckfoxSwitch/make_sd_swap_assets.py --output-dir civswap-assets
```

The generator creates only regular files and never selects a physical disk. It uses the host `mkswap`; on macOS it can fall back to Docker. The expected checksums are:

```text
6f76faaa6efd99aeb77c5fa893b1e12009bedff7c2ff0bbff88301a81cf4e623  mbr.bin
590fbd98d73fbdde3fdeda5738358bda32055f958f4b1bcd74e905549eec7119  swap.img
```

On macOS, identify the removable card without guessing. A built-in SD reader is
not always listed under `external physical`, so inspect the complete device list
and verify the candidate's media name, capacity, and removable status:

```bash
diskutil list
diskutil info /dev/diskN
```

Only after confirming that `/dev/diskN` is the intended SD card and not the
internal disk or a disk image:

```bash
diskutil unmountDisk /dev/diskN
sudo dd if=civswap-assets/mbr.bin of=/dev/rdiskN bs=512 count=1
sudo dd if=civswap-assets/swap.img of=/dev/rdiskN bs=1m seek=1
sudo dd if=/dev/rdiskN bs=1m skip=1 count=1024 | cmp - civswap-assets/swap.img
sync
diskutil eject /dev/diskN
```

Do not use `/dev/disk13`, `/dev/disk14`, or another saved identifier from an earlier session. Never run the write unless `diskutil list` and `diskutil info` identify the intended physical card by media name and capacity. After inserting it into the Luckfox, `civswitchctl status` should show a `/dev/...` entry under `Swap:`.

## Persistent trigger and Nintendo cloud saves

The CivSwitch macro gets the Remote JS Loader running. The separate `installer-creator` then stores the persistent trigger as the normal Civ VII save `save:/saves/Single/XSTRIG.Civ7Save` while preserving other save files.

That trigger follows the console's save-data behavior. If Nintendo Switch Online Save Data Cloud backup or automatic download is enabled for Civ VII, the trigger can be copied to another console signed into the same account. Isolate the save on one console by disabling per-game cloud backup/download if that behavior is not wanted. Nintendo cloud saves require an NSO membership. 2K Cross-Save is a separate system and generally requires explicitly choosing cross-play saves.

## Recovery

When HID mode is active, USB ADB/networking are replaced but Ethernet SSH remains available. Stop the job and restore stock USB with:

```bash
ssh yunpengs-luckfox 'civswitchctl stop'
```

If Ethernet access is unavailable, enter BOOT/Maskrom mode as described in step 2, or use a serial console. Logs are retained on NAND at `/opt/civswitch/logs/current.log`, with the preceding run at `/opt/civswitch/logs/previous.log`. Retrieve `current.log` after a failed Switch run; it records the last completed line and, when Python catches the failure, the character and HID queue state where it stopped.

If you have only USB access, unplug the board and hold BOOT while reconnecting to the computer. This enters recovery without running the macro. You can repeat the firmware flash and installation above; **this replaces the NAND system again**. BOOT alone does not clear the mode file, so reconnecting normally before stopping or reflashing could type again.
