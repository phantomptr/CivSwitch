# CivSwitch

XSS toolchain for the Nintendo Switch edition of Civilization 7.  
Only the **1.0.0** version of the game on physical cartridge is supported.

Compatible with Switch 1/2 firmware 20.x.x through current (23.0.1).  
Tested on Switch 1 fw 22.5.0, Switch 2 fw 20.4.0 and 23.0.1.

The **Luckfox Pico Pro** path is verified end-to-end on **Switch 1 and Switch 2**: 87/87 typed lines reached the Remote JS Loader on port 9026. Switch 1 also executed the hello-world payload successfully after the keyboard control-channel fix; the earlier Switch 2 run completed the persistent installer. Max shares the Pro/Max image but has not been tested separately. See the [Luckfox guide](LuckfoxSwitch/README.md) and [verified findings](LuckfoxSwitch/FINDINGS.md).

## Components

| Component | Description |
| --------- | ----------- |
| **PicoSwitch** | Raspberry Pi Pico firmware (RP2040 + RP2350). Types the chat-XSS payload into the Switch as a USB HID macro. Ships with `compress.js` for payload generation. |
| **LuckfoxSwitch** | Linux USB gadget port with Pico-compatible reports and macro sequence. Supports standalone Luckfox Pico Pro onboard-flash startup, plus other SBCs with compatible USB gadget support. |
| **installer-creator** | Produces the boot-persistent installer (`XSTRIG.Civ7Save` trigger) from any JavaScript payload, sent through the Remote JS Loader. |

`core.js` is the shared exploit payload. `send_js.py` sends any payload to a running loader.

## Quick Start

### 1. Get the device ready

**Option A — Raspberry Pi Pico (easiest)**  
Download `civ7-pico1.uf2` (RP2040) or `civ7-pico2.uf2` (RP2350) from [Releases](../../releases).  
Hold BOOTSEL, plug the Pico into your PC, drag the UF2 onto the drive. Done.

**Option B — Linux SBC (Luckfox Pico, Pi Zero, BeagleBone, etc.)**  
Download `LuckfoxSwitch.tar.gz` from [Releases](../../releases), copy it to the board, and run `bash install.sh` inside the extracted directory. For a Luckfox Pico Pro or Max, follow the [complete Luckfox Pico Pro / Max flashing guide](LuckfoxSwitch/FLASHING.md); it includes the required Buildroot image, `rk_dma_heap_cma=4K` environment, static networking, the single `yunpengs-luckfox` SSH alias, manual controller test, and the verified full-run procedure. The Pro/Max image is not interchangeable with the other Luckfox Pico families.

The verified Pro/Max family runs from onboard SPI NAND with no SD card or continuing computer connection. CivSwitch is manual by default: nothing types at boot until `civswitchctl start`, `start-line N`, or `test` is invoked.

### 2. Prepare the Switch

1. **System** menu → change system language to **English(US)**
2. **Controllers & Accessories** menu → change USB Keyboard to **English(US)**
3. Connect to the internet using [90DNS](https://gitlab.com/a/90dns) to block Nintendo servers

> Language settings are required so the Switch types English characters via USB keyboard.  
> Higher firmware versions may not need the system language change.

### 3. Run the payload

1. Launch Civ 7 → **MULTIPLAYER** → **WIRELESS** → **CREATE GAME** → **HOST LOBBY** → **CHAT**
2. With the normal controller, **select/focus the chat text field first** so the caret is in the message box. Opening the chat panel alone is not enough.
3. Put the Switch in **docked mode**, then plug in the Pico/Luckfox. CivSwitch sends its fixed `A × 5` preamble after enumeration; the chat task must already be active because it cannot navigate the game menu by itself.
4. For Luckfox, follow the [registered-controller workflow](LuckfoxSwitch/README.md#registered-controller-workflow-switch-1-and-switch-2), including any L+R/A prompts. Wait about 20–25 minutes for all 87 lines.
5. The Remote JS Loader comes up on the console at TCP port 9026

### 4. Install the persistent boot trigger

```
python installer-creator/install.py <console-ip>
```

This uploads a save file with the compressed payload. From then on: **main menu → Load Game** runs the payload automatically (and if it's the loader, port 9026 is up at boot and survives sleep/wake).

You can revert system language after the installer finishes.

The trigger is a normal Civ VII save. Nintendo Switch Online Save Data Cloud can back it up or download it to another console when cloud support is enabled for that game; an NSO membership is required for Nintendo cloud saves. Disable per-game automatic cloud backup/download if you want the trigger isolated to one console. 2K Cross-Save is separate and generally requires explicitly selecting cross-play saves.

## Sending payloads

```
python send_js.py <console-ip> [port] file.js
```

Sends a JS file to the Remote JS Loader (default port 9026) and prints the log output streamed back.

## Building from source

Most users should download pre-built files from [Releases](../../releases). Build from source only if you've modified `core.js` or the firmware.

### Pico firmware (Ubuntu 24.04)

```bash
sudo apt install -y git build-essential cmake ninja-build gcc-arm-none-eabi \
  libnewlib-arm-none-eabi libstdc++-arm-none-eabi-newlib python3 nodejs npm
git clone --branch 2.3.1 --depth 1 --recurse-submodules --shallow-submodules \
  https://github.com/raspberrypi/pico-sdk.git ~/pico-sdk
export PICO_SDK_PATH=$HOME/pico-sdk

cd PicoSwitch
npm install                    # terser for compress.js
node compress.js               # reads ../core.js -> payload.txt
cmake --preset pico2 && cmake --build --preset pico2    # RP2350
cmake --preset pico1 && cmake --build --preset pico1    # RP2040
```

### Linux SBC payload

```bash
cd PicoSwitch
npm install
node compress.js               # reads ../core.js -> payload.txt
cp payload.txt ../LuckfoxSwitch/

scp -r LuckfoxSwitch/ root@<board-ip>:/tmp/
ssh root@<board-ip> 'cd /tmp/LuckfoxSwitch && bash install.sh'
ssh root@<board-ip> 'civswitchctl status'
```

Luckfox manual operation is intentional. The installer leaves automatic typing off; `civswitchctl test`, `start-line N`, and `start` explicitly replace the stock RNDIS+ADB gadget while a job is active, and `civswitchctl stop` restores it. See the [Luckfox flashing guide](LuckfoxSwitch/FLASHING.md) for the exact Switch 2 configuration and host SSH helper.

## Credits

- The JS exploit core in `core.js` referenced [inject.js](https://github.com/NetflixNHack/Netflix-N-Hack/blob/main/inject.js) from [Netflix-N-Hack](https://github.com/NetflixNHack/Netflix-N-Hack) and its fork by [ufm42](https://github.com/ufm42/Netflix-N-Hack/blob/main/inject.js)
- The PicoSwitch firmware is based on [Pico-Switch-Controller](https://github.com/Element2424/Pico-Switch-Controller) by [Element2424](https://github.com/Element2424)
