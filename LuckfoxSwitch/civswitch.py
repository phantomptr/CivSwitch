#!/usr/bin/env python3
"""CivSwitch macro engine for Linux USB gadget boards.

Reads payload.txt and types the chat-XSS payload into the Switch via
/dev/hidg0 (gamepad) and /dev/hidg1 (keyboard), matching the exact
timing and report format of the PicoSwitch RP2040 firmware.

Works on any Linux board with USB gadget support (Luckfox Pico Pro,
Raspberry Pi Zero, BeagleBone, etc.).

Usage: civswitch.py [payload.txt]
"""

import struct
import sys
import time
import os

# --- Timing constants (match PicoSwitch tools/payload_to_macro.py) ---
KEY_HOLD_S = 0.032
KEY_GAP_S = 0.032
ENTER_HOLD_S = 0.032
ENTER_GAP_S = 0.200
PRE_A_WAIT_S = 0.750
POST_A_WAIT_S = 0.550
A_PRESS_S = 0.050
PREAMBLE_A_COUNT = 5
PREAMBLE_GAP_S = 0.550
ESC_TO_A_WAIT_S = 0.750
START_DELAY_S = 1.0

# --- Switch gamepad report (8 bytes, matches SwitchUsbReport) ---
HAT_NOTHING = 0x08
JOY_MID = 0x80
MASK_A = 1 << 2

def pack_gamepad(buttons=0, hat=HAT_NOTHING, lx=JOY_MID, ly=JOY_MID,
                 rx=JOY_MID, ry=JOY_MID, vendor=0):
    return struct.pack('<HBBBBBB', buttons, hat, lx, ly, rx, ry, vendor)

GAMEPAD_NEUTRAL = pack_gamepad()
GAMEPAD_A = pack_gamepad(buttons=MASK_A)

# --- Keyboard report (8 bytes, matches KeyboardReport) ---
MOD_LSHIFT = 0x02

def pack_keyboard(modifier=0, keycode=0):
    return struct.pack('BBBBBBBB', modifier, 0, keycode, 0, 0, 0, 0, 0)

KBD_RELEASE = pack_keyboard()

# --- HID keycodes (USB HID Usage Table, same values as TinyUSB HID_KEY_*) ---
HID_KEY_A = 0x04
HID_KEY_1 = 0x1E
HID_KEY_0 = 0x27
HID_KEY_ENTER = 0x28
HID_KEY_ESCAPE = 0x29
HID_KEY_SPACE = 0x2C
HID_KEY_MINUS = 0x2D
HID_KEY_EQUAL = 0x2E
HID_KEY_BRACKET_LEFT = 0x2F
HID_KEY_BRACKET_RIGHT = 0x30
HID_KEY_BACKSLASH = 0x31
HID_KEY_SEMICOLON = 0x33
HID_KEY_APOSTROPHE = 0x34
HID_KEY_GRAVE = 0x35
HID_KEY_COMMA = 0x36
HID_KEY_PERIOD = 0x37
HID_KEY_SLASH = 0x38
HID_KEY_TAB = 0x2B

ASCII_TO_HID = {}
for i, c in enumerate('abcdefghijklmnopqrstuvwxyz'):
    ASCII_TO_HID[c] = (0, HID_KEY_A + i)
    ASCII_TO_HID[c.upper()] = (MOD_LSHIFT, HID_KEY_A + i)
for i, c in enumerate('1234567890'):
    ASCII_TO_HID[c] = (0, HID_KEY_1 + i)
ASCII_TO_HID.update({
    ' ': (0, HID_KEY_SPACE), '\t': (0, HID_KEY_TAB),
    '!': (MOD_LSHIFT, HID_KEY_1), '@': (MOD_LSHIFT, HID_KEY_1 + 1),
    '#': (MOD_LSHIFT, HID_KEY_1 + 2), '$': (MOD_LSHIFT, HID_KEY_1 + 3),
    '%': (MOD_LSHIFT, HID_KEY_1 + 4), '^': (MOD_LSHIFT, HID_KEY_1 + 5),
    '&': (MOD_LSHIFT, HID_KEY_1 + 6), '*': (MOD_LSHIFT, HID_KEY_1 + 7),
    '(': (MOD_LSHIFT, HID_KEY_1 + 8), ')': (MOD_LSHIFT, HID_KEY_0),
    '-': (0, HID_KEY_MINUS), '_': (MOD_LSHIFT, HID_KEY_MINUS),
    '=': (0, HID_KEY_EQUAL), '+': (MOD_LSHIFT, HID_KEY_EQUAL),
    '[': (0, HID_KEY_BRACKET_LEFT), '{': (MOD_LSHIFT, HID_KEY_BRACKET_LEFT),
    ']': (0, HID_KEY_BRACKET_RIGHT), '}': (MOD_LSHIFT, HID_KEY_BRACKET_RIGHT),
    '\\': (0, HID_KEY_BACKSLASH), '|': (MOD_LSHIFT, HID_KEY_BACKSLASH),
    ';': (0, HID_KEY_SEMICOLON), ':': (MOD_LSHIFT, HID_KEY_SEMICOLON),
    "'": (0, HID_KEY_APOSTROPHE), '"': (MOD_LSHIFT, HID_KEY_APOSTROPHE),
    '`': (0, HID_KEY_GRAVE), '~': (MOD_LSHIFT, HID_KEY_GRAVE),
    ',': (0, HID_KEY_COMMA), '<': (MOD_LSHIFT, HID_KEY_COMMA),
    '.': (0, HID_KEY_PERIOD), '>': (MOD_LSHIFT, HID_KEY_PERIOD),
    '/': (0, HID_KEY_SLASH), '?': (MOD_LSHIFT, HID_KEY_SLASH),
})


class HIDWriter:
    def __init__(self, gamepad_dev='/dev/hidg0', keyboard_dev='/dev/hidg1'):
        self.gamepad_fd = os.open(gamepad_dev, os.O_WRONLY)
        self.keyboard_fd = os.open(keyboard_dev, os.O_WRONLY)

    def close(self):
        os.close(self.gamepad_fd)
        os.close(self.keyboard_fd)

    def send_gamepad(self, report):
        os.write(self.gamepad_fd, report)

    def send_keyboard(self, report):
        os.write(self.keyboard_fd, report)

    def press_a(self, hold_s=A_PRESS_S):
        self.send_gamepad(GAMEPAD_A)
        time.sleep(hold_s)
        self.send_gamepad(GAMEPAD_NEUTRAL)

    def press_key(self, keycode, modifier=0, hold_s=KEY_HOLD_S):
        self.send_keyboard(pack_keyboard(modifier, keycode))
        time.sleep(hold_s)
        self.send_keyboard(KBD_RELEASE)

    def type_line(self, line):
        prev_keycode = None
        for ch in line:
            if ch not in ASCII_TO_HID:
                continue
            modifier, keycode = ASCII_TO_HID[ch]
            if keycode == prev_keycode:
                self.send_keyboard(KBD_RELEASE)
                time.sleep(KEY_GAP_S)
            self.send_keyboard(pack_keyboard(modifier, keycode))
            time.sleep(KEY_HOLD_S)
            prev_keycode = keycode
        self.send_keyboard(KBD_RELEASE)


def set_led(on):
    try:
        with open('/sys/class/leds/work/brightness', 'w') as f:
            f.write('255' if on else '0')
    except (FileNotFoundError, PermissionError):
        pass


def run_macro(payload_path):
    with open(payload_path, encoding='utf-8') as f:
        lines = [l for l in f.read().splitlines() if l.strip()]

    if not lines:
        print('No payload lines to type.')
        return

    print(f'CivSwitch: {len(lines)} line(s), starting in {START_DELAY_S}s...')
    time.sleep(START_DELAY_S)

    hid = HIDWriter()
    set_led(True)

    try:
        # Preamble: press A x5
        for i in range(PREAMBLE_A_COUNT):
            hid.press_a()
            time.sleep(PREAMBLE_GAP_S)

        for i, line in enumerate(lines):
            print(f'  [{i+1}/{len(lines)}] {line[:60]}{"..." if len(line) > 60 else ""}')

            # Esc -> wait -> A -> wait
            hid.press_key(HID_KEY_ESCAPE)
            time.sleep(ESC_TO_A_WAIT_S)
            hid.press_a()
            time.sleep(POST_A_WAIT_S)

            # Type the line
            hid.type_line(line)

            # Enter x2
            hid.press_key(HID_KEY_ENTER, hold_s=ENTER_HOLD_S)
            time.sleep(ENTER_GAP_S)
            hid.press_key(HID_KEY_ENTER, hold_s=ENTER_HOLD_S)

            # Separator between batches
            if i < len(lines) - 1:
                time.sleep(PRE_A_WAIT_S)
                hid.press_a()
                time.sleep(POST_A_WAIT_S)

        print('CivSwitch: macro complete.')
    finally:
        set_led(False)
        hid.close()


if __name__ == '__main__':
    payload = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'payload.txt')
    run_macro(payload)
