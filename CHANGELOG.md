# Changelog

## v1.2 — Switch 1 and Switch 2 Luckfox support

- Fix Switch 1 keyboard flicker and dropped characters by accepting keyboard LED output reports through the USB control endpoint. Keep gamepad interrupt output enabled.
- Add `civswitchctl prepare`, `press BUTTON`, and `resume N` for controller registration and continuation on the same USB connection, preserving original payload line numbers.
- Include focused keyboard/controller diagnostics, bounded repeated-report probes, and persisted failure details from the Switch 1 investigation.
- Use 40 ms key holds and 800/1200 ms focus waits consistently in the Pico generator and Luckfox macro.
- Prefer macOS system netcat in the single SSH helper; preserve Ethernet → RNDIS → ADB fallback.
- Document physical validation, the misleading CONFIGURED notification, recovery, flashing, and hardware coverage. Switch 1 reached the loader and executed hello-world; the earlier Switch 2 run also completed the persistent installer.
- Freeze the hardware-validated 87-line payload in source control and check its source/hash parity before packaging.
- Exclude Python caches from release archives and publish SHA-256 checksums.

The new keyboard output setting has physical Switch 1 validation. Switch 2's prior successful run predates that setting; Max and other Luckfox variants remain untested separately. See [the full findings](LuckfoxSwitch/FINDINGS.md).

## v1.1

Verified Switch 2 / Luckfox Pico Pro path, with flashing helpers and optional SD swap assets.
