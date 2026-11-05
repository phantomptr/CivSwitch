# CivSwitch

XSS toolchain for the Nintendo Switch edition of Civilization 7.  
Only the **1.0.0** version of the game on physical cartridge is supported.

Compatible with Switch 1/2 firmware 20.x.x through current (23.0.1).  
Tested on Switch 1 fw 22.5.0, Switch 2 fw 20.4.0 and 23.0.1.

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
Download `LuckfoxSwitch.tar.gz` from [Releases](../../releases).  
Copy to the board and install — see [LuckfoxSwitch/README.md](LuckfoxSwitch/README.md) for details.

The **Luckfox Pico Pro can run entirely from its onboard flash**, without an SD card or a connected computer. See the [onboard-flash setup](LuckfoxSwitch/README.md#luckfox-pico-pro-onboard-flash-without-an-sd-card), including the observed restart behavior when using `poweroff` and how to move the board to the Switch after enabling automatic typing.

### 2. Prepare the Switch

1. **System** menu → change system language to **English(US)**
2. **Controllers & Accessories** menu → change USB Keyboard to **English(US)**
3. Connect to the internet using [90DNS](https://gitlab.com/a/90dns) to block Nintendo servers

> Language settings are required so the Switch types English characters via USB keyboard.  
> Higher firmware versions may not need the system language change.

### 3. Run the payload

1. Launch Civ 7 → **MULTIPLAYER** → **WIRELESS** → **CREATE GAME** → **HOST LOBBY** → **CHAT**
2. Plug the device into the Switch (docked mode: focus the chat box first)
3. Wait ~15 minutes while it types all ~87 lines into chat
4. The Remote JS Loader comes up on the console at TCP port 9026

### 4. Install the persistent boot trigger

```
python installer-creator/install.py <console-ip>
```

This uploads a save file with the compressed payload. From then on: **main menu → Load Game** runs the payload automatically (and if it's the loader, port 9026 is up at boot and survives sleep/wake).

You can revert system language after the installer finishes.

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
ssh root@<board-ip> 'systemctl enable civswitch-gadget civswitch && reboot'
```

## Credits

- The JS exploit core in `core.js` referenced [inject.js](https://github.com/NetflixNHack/Netflix-N-Hack/blob/main/inject.js) from [Netflix-N-Hack](https://github.com/NetflixNHack/Netflix-N-Hack) and its fork by [ufm42](https://github.com/ufm42/Netflix-N-Hack/blob/main/inject.js)
- The PicoSwitch firmware is based on [Pico-Switch-Controller](https://github.com/Element2424/Pico-Switch-Controller) by [Element2424](https://github.com/Element2424)
