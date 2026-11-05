#!/usr/bin/env python3
"""CivSwitch macro engine for Linux USB gadget boards.

Reads payload.txt and types the chat-XSS payload into the Switch via
/dev/hidg0 (gamepad) and /dev/hidg1 (keyboard), using the Pico firmware's
macro states and nominal timing. Linux controls USB descriptors and polling.

Works on any Linux board with USB gadget support (Luckfox Pico Pro,
Raspberry Pi Zero, BeagleBone, etc.).

Usage: civswitch.py [payload.txt]
"""

import argparse
import os
import select
import signal
import struct
import threading
import time
from pathlib import Path


class HIDEndpoint:
    """Keep one HID interface reporting, independently of macro sleeps.

    Nonblocking I/O lets shutdown and host output processing continue even
    when the host stops polling. A state change is acknowledged only after
    it has been queued to the kernel, so a short button press isn't skipped.
    This acknowledges queueing, not delivery to the Switch's application.
    """

    def __init__(self, path, initial_report, echo_output=False):
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        os.set_blocking(self.fd, False)
        self.report = initial_report
        self.echo_output = echo_output
        self.condition = threading.Condition()
        self.generation = 0
        self.sent_generation = -1
        self.error = None
        self.stopped = threading.Event()
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def _run(self):
        echo = None
        try:
            while not self.stopped.is_set():
                readable, writable, _ = select.select(
                    [self.fd] if echo is None else [], [self.fd], [], 0.05)
                if readable:
                    try:
                        output = os.read(self.fd, 64)
                    except BlockingIOError:
                        output = None
                    # f_hid is packet-oriented: an empty completed OUT
                    # transfer returns b'', not stream EOF. Ignore it like
                    # Pico's keyboard callback (bufsize == 0). Real device
                    # shutdown is reported by the gadget's I/O errors.
                    if output:
                        if self.echo_output:
                            echo = output
                        else:
                            # Match the Pico keyboard SET_REPORT callback.
                            set_led(bool(output[0] & 0x02))
                if writable:
                    with self.condition:
                        report, generation = self.report, self.generation
                    packet = echo if echo is not None else report
                    try:
                        written = os.write(self.fd, packet)
                    except BlockingIOError:
                        continue
                    if written != len(packet):
                        raise OSError('Short USB HID write')
                    if echo is not None:
                        echo = None
                    else:
                        with self.condition:
                            self.sent_generation = generation
                            self.condition.notify_all()
                    # Pico sends whenever TinyUSB is ready. The gadget driver
                    # still determines the actual endpoint polling interval.
                    self.stopped.wait(0.001)
        except OSError as error:
            with self.condition:
                self.error = error
                self.condition.notify_all()

    def send(self, report):
        deadline = time.monotonic() + 2.0
        with self.condition:
            if self.stopped.is_set():
                raise OSError('USB HID endpoint is closed')
            self.report = report
            self.generation += 1
            generation = self.generation
            while self.sent_generation < generation:
                if self.error is not None:
                    raise self.error
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('USB host is not accepting HID reports')
                self.condition.wait(remaining)

    def check(self):
        with self.condition:
            if self.error is not None:
                raise self.error

    def close(self):
        if not self.stopped.is_set():
            self.stopped.set()
            with self.condition:
                self.condition.notify_all()
            self.worker.join()
            os.close(self.fd)

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
        self.gamepad = HIDEndpoint(gamepad_dev, GAMEPAD_NEUTRAL, echo_output=True)
        try:
            self.keyboard = HIDEndpoint(keyboard_dev, KBD_RELEASE)
        except OSError:
            self.gamepad.close()
            raise
        self.closed = False

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                for endpoint, neutral in ((self.gamepad, GAMEPAD_NEUTRAL),
                                           (self.keyboard, KBD_RELEASE)):
                    try:
                        endpoint.send(neutral)
                    except OSError:
                        pass  # The host may already have disconnected.
            finally:
                self.gamepad.close()
                self.keyboard.close()

    def wait(self):
        # The Pico keeps sending neutral reports after MACRO_OP_END too.
        while True:
            self.gamepad.check()
            self.keyboard.check()
            time.sleep(0.1)

    def send_gamepad(self, report):
        self.gamepad.send(report)

    def send_keyboard(self, report):
        self.keyboard.send(report)

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
    except OSError:
        # The status LED is optional. A sysfs failure must not terminate
        # a HID worker while its last keyboard report still holds a key.
        pass


def wait_for_usb(udc_root='/sys/class/udc'):
    states = list(Path(udc_root).glob('*/state'))
    if not states:
        raise RuntimeError('No USB device controller found; set up the HID gadget first')
    print('CivSwitch: waiting for USB host configuration...', flush=True)
    while not any(state.read_text().strip() == 'configured' for state in states):
        time.sleep(0.1)


def run_macro(payload_path):
    with open(payload_path, encoding='utf-8') as f:
        lines = [l for l in f.read().splitlines() if l.strip()]

    if not lines:
        print('No payload lines to type.')
        return

    wait_for_usb()
    hid = HIDWriter()
    try:
        print(f'CivSwitch: {len(lines)} line(s), starting in {START_DELAY_S}s...')
        time.sleep(START_DELAY_S)
        set_led(True)
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
        set_led(False)
        hid.wait()
    finally:
        set_led(False)
        hid.close()


def run_controller_test():
    """Send only the Pico's five-A preamble, with a released keyboard."""
    wait_for_usb()
    hid = HIDWriter()
    try:
        print('CivSwitch: controller test; five A presses, no text.', flush=True)
        time.sleep(START_DELAY_S)
        for _ in range(PREAMBLE_A_COUNT):
            hid.press_a()
            time.sleep(PREAMBLE_GAP_S)
        print('CivSwitch: controller test complete; reports now neutral.', flush=True)
        hid.wait()
    finally:
        hid.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('payload', nargs='?', default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'payload.txt'))
    parser.add_argument('--test-controller', action='store_true',
                        help='send the five-A preamble only; never type the payload')
    args = parser.parse_args()

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        if args.test_controller:
            run_controller_test()
        else:
            run_macro(args.payload)
    except KeyboardInterrupt:
        print('CivSwitch: stopped.', flush=True)
