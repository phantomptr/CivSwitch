# Luckfox validation and USB findings

## Physical results

| Console | Hardware / transport | Confirmed result |
| --- | --- | --- |
| Switch 1, 2026-11-06 | Luckfox Pico Pro, Buildroot Linux 5.10.160, stock `rockchip` gadget at high speed, Civ VII chat | Exact slow alphabet with no flicker; L+R then A registration; all 87 payload lines; console displayed its TCP loader address; remote hello-world returned `Hello from remote JS!` and `Executed successfully`. |
| Switch 2, earlier 2026-11-05 run | Luckfox Pico Pro, onboard Buildroot flash, stock high-speed gadget | 87/87, Remote JS Loader, and successful persistent `XSTRIG.Civ7Save` installer. |

The Switch 1 run sent line 1 separately, registered the controller, resent line 1 successfully, then sent lines 2–87 on the same USB connection. Its continuation completed 86 consecutive lines with no endpoint errors: 343 gamepad writes and 23,168 keyboard writes. Available memory remained about 94 MiB. An optional 1 GiB SD swap partition was present; the pre-run available memory was already sufficient.

The release's `prepare`, `press`, and `resume` commands package that sequence without temporary scripts. Their command plumbing has host tests; the recorded physical run used the equivalent bounded Python calls before those commands were added. Switch 2's physical run predates the new keyboard output setting. A new Switch 2 regression run with the final v1.2 archive has not been performed. Max and other Luckfox models have not been separately tested. Switch 1 persistent-save installation was not performed in this run.

## Keyboard flicker and missing letters

With keyboard interrupt-OUT enabled, the Switch 1 alphabet test produced `cdghklopstwx`. Adding explicit 250 ms key holds and 500 ms release gaps still produced only `acegijlnpqsuwz`. Forcing full-speed USB also failed to cure flicker. Slower typing and USB speed were insufficient fixes.

Luckfox's Linux `f_hid` handles output reports in one of two ways. With interrupt-OUT enabled, it **stalls control-channel HID SET_REPORT requests**. With `no_out_endpoint=1`, it accepts those requests through endpoint zero. The Pico firmware's keyboard callback accepts output reports.

Setting **only the keyboard** to `no_out_endpoint=1` eliminated the observed configuration-event storm and passed the exact slow alphabet test without flicker. The subsequent real payload succeeded at normal 40 ms typing timing. The gamepad keeps its interrupt-OUT endpoint and host-output echo behavior.

## CONFIGURED messages do not prove resets

The vendor configfs setup hook checks `bRequest == USB_REQ_SET_CONFIGURATION` without checking `bRequestType` before scheduling its Android state notification. Both standard SET_CONFIGURATION and class-specific HID SET_REPORT have numeric request value `0x09`. It can therefore print `USB_STATE=CONFIGURED` for keyboard output traffic without resetting the interfaces.

Earlier conclusions that every such message proved a repeated configuration reset were too strong. No composite-kernel patch or kernel flash was needed for this fix. A future USB investigation must capture request type as well as request number before asserting repeated reconfiguration.

Sources: [Luckfox configfs setup hook](https://github.com/LuckfoxTECH/luckfox-pico/blob/main/sysdrv/source/kernel/drivers/usb/gadget/configfs.c), [Luckfox HID request handling](https://github.com/LuckfoxTECH/luckfox-pico/blob/main/sysdrv/source/kernel/drivers/usb/gadget/function/f_hid.c), and the repository's [Pico keyboard callback](../PicoSwitch/src/switchController/SwitchUsb.cpp).

## Registration and recovery

A registered controller and a working keyboard are independent requirements. Use `prepare`, satisfy the actual L+R and A prompts using `press`, return to focused chat, then `resume 1`. Keep the same USB connection throughout. Re-running gadget setup can reopen registration. Never infer console acceptance solely from successful Linux writes.

`resume N` preserves original line numbers and skips startup presses. Its normal per-line sequence is unchanged. At completion it leaves neutral HID connected; `stop` releases the connection and restores stock RNDIS+ADB. Manual operation remains the default and never arms the next boot.

The Mac SSH helper prefers `/usr/bin/nc` when available. During this run Homebrew netcat returned `No route to host` while system netcat and direct SSH worked. Similar per-process LAN access failures can affect host Python; sending the hello-world test from the Luckfox over Ethernet succeeded.
