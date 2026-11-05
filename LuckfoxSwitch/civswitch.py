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
import gc
import json
import os
import select
import signal
import struct
import sys
import threading
import time
import traceback
from pathlib import Path


class HIDEndpoint:
    """Keep one HID interface synchronized, independently of macro sleeps.

    Nonblocking I/O lets shutdown and host output processing continue even
    when the host stops polling. USB HID reports are stateful, so the worker
    writes a report when that state changes and sends only a slow keepalive
    while it remains unchanged. Replacing a pending state retains the newest
    value without queueing thousands of redundant kernel transfers.
    """

    def __init__(self, path, initial_report, echo_output=False):
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        os.set_blocking(self.fd, False)
        self.report = initial_report
        self.echo_output = echo_output
        self.condition = threading.Condition()
        self.generation = 0
        self.sent_generation = -1
        self.error = None
        self.last_sent = None
        self.writes = 0
        self.outputs = 0
        self.last_write_at = None
        self.last_progress_at = time.monotonic()
        self.next_keepalive_at = self.last_progress_at + HID_KEEPALIVE_S
        self.force_state = False
        self.stopped = threading.Event()
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def _fail(self, error):
        with self.condition:
            if self.error is None:
                self.error = error
            self.condition.notify_all()

    def _run(self):
        echo = None
        try:
            while not self.stopped.is_set():
                now = time.monotonic()
                with self.condition:
                    pending = (self.force_state or
                               self.generation > self.sent_generation)
                if pending and now - self.last_progress_at >= HID_ACK_TIMEOUT_S:
                    with self.condition:
                        last_sent = self.last_sent
                    self._fail(TimeoutError(
                        f'{self.path}: USB host is not polling; last_queued='
                        f'{last_sent.hex() if last_sent is not None else None}'))
                    return
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
                        self.outputs += 1
                        if self.echo_output:
                            echo = output
                        else:
                            # Match the Pico keyboard SET_REPORT callback.
                            set_led(bool(output[0] & 0x02))
                if writable:
                    now = time.monotonic()
                    with self.condition:
                        report, generation = self.report, self.generation
                        should_send = (echo is not None or self.force_state or
                                       generation > self.sent_generation or
                                       now >= self.next_keepalive_at)
                    if not should_send:
                        # hidg character devices remain writable even when
                        # there is nothing new to send. Without sleeping
                        # here, select() would immediately report writable
                        # forever and each worker would busy-spin a CPU core.
                        with self.condition:
                            self.condition.wait(min(0.05, max(
                                0.0, self.next_keepalive_at - now)))
                        continue
                    packet = echo if echo is not None else report
                    try:
                        written = os.write(self.fd, packet)
                    except BlockingIOError:
                        continue
                    if written != len(packet):
                        raise OSError('Short USB HID write')
                    with self.condition:
                        self.last_sent = packet
                        self.writes += 1
                        self.last_write_at = time.monotonic()
                        self.last_progress_at = self.last_write_at
                        self.next_keepalive_at = (
                            self.last_write_at + HID_KEEPALIVE_S)
                        self.force_state = False
                    if echo is not None:
                        echo = None
                        # Pico echoes a host output report once, then resumes
                        # reporting the current input state on its next loop.
                        with self.condition:
                            self.force_state = True
                    else:
                        with self.condition:
                            self.sent_generation = generation
                            self.condition.notify_all()
                    # Pico sends whenever TinyUSB is ready. The gadget driver
                    # still determines the actual endpoint polling interval.
                    self.stopped.wait(0.001)
        except OSError as error:
            self._fail(error)

    def publish(self, report):
        """Make a changed report current without waiting for host polling.

        USB HID retains the most recent input report. Repeating an unchanged
        state does not change host behavior, but each write allocates in the
        Luckfox f_hid/dwc2 path and linearly leaks kernel memory. Coalescing
        equal reports reduces those writes to one per actual state transition.
        """
        with self.condition:
            if self.stopped.is_set():
                raise OSError('USB HID endpoint is closed')
            if self.error is not None:
                raise self.error
            if report != self.report:
                self.report = report
                self.generation += 1
            return self.generation

    def send(self, report):
        """Publish report and wait for it to complete one USB transfer."""
        generation = self.publish(report)
        deadline = time.monotonic() + HID_ACK_TIMEOUT_S
        with self.condition:
            while self.sent_generation < generation:
                if self.error is not None:
                    raise self.error
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f'{self.path}: USB host is not accepting HID reports; '
                        f'requested={report.hex()}, last_queued='
                        f'{self.last_sent.hex() if self.last_sent is not None else None}')
                self.condition.wait(remaining)

    def snapshot(self):
        with self.condition:
            return dict(path=self.path, worker_alive=self.worker.is_alive(),
                        requested=self.report.hex(),
                        last_queued=self.last_sent.hex() if self.last_sent is not None else None,
                        generation=self.generation, queued_generation=self.sent_generation,
                        writes=self.writes, outputs=self.outputs,
                        last_write_at=self.last_write_at, error=repr(self.error))

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
# The physical Switch test-input screen was observed to register only four of
# five A presses at the Pico's 550 ms spacing under Linux. One second preserves
# every changed report and keyboard timing elsewhere, while giving the host
# enough time to finish each controller-view update before the next A.
LINUX_PREAMBLE_GAP_S = 1.0
ESC_TO_A_WAIT_S = 0.750
# The Switch input-test screen can become visible before it is ready to
# consume the first gamepad state. With a two-second delay the first A was
# consistently dropped while the next button was accepted; the Switch needed
# the extra enumeration-settling interval. Four seconds preserves every
# report state without adding a visible warm-up press.
LINUX_USB_READY_DELAY_S = 4.0
# The Pico macro advances on its own timer while the host is not polling. Keep
# a long liveness bound so a genuinely dead endpoint still reaches the macro
# diagnostics instead of silently running for the rest of the payload.
HID_ACK_TIMEOUT_S = 60.0
# HID input reports are stateful, so an unchanged report need not be resent
# every millisecond. A slow keepalive is enough to detect a host that remains
# present while also avoiding the Luckfox driver's per-write memory leak.
HID_KEEPALIVE_S = 30.0
# Stop between lines before a driver leak or another allocation failure can
# kill the process while a key is held. This is a safe boundary: the finally
# block can release both HID states, unlike an OOM kill in the middle of a line.
MIN_MEM_AVAILABLE_KB = 6144

# --- Switch gamepad report (8 bytes, matches SwitchUsbReport) ---
HAT_NOTHING = 0x08
JOY_MID = 0x80
MASK_Y = 1 << 0
MASK_B = 1 << 1
MASK_A = 1 << 2
MASK_X = 1 << 3
MASK_L = 1 << 4
MASK_R = 1 << 5

def pack_gamepad(buttons=0, hat=HAT_NOTHING, lx=JOY_MID, ly=JOY_MID,
                 rx=JOY_MID, ry=JOY_MID, vendor=0):
    return struct.pack('<HBBBBBB', buttons, hat, lx, ly, rx, ry, vendor)

GAMEPAD_NEUTRAL = pack_gamepad()
GAMEPAD_A = pack_gamepad(buttons=MASK_A)
# The input-test screen only retains a short highlight. Repeated A presses are
# hard to count reliably there, so the diagnostic uses five distinct buttons.
CONTROLLER_TEST_SEQUENCE = (
    ('A', MASK_A),
    ('B', MASK_B),
    ('X', MASK_X),
    ('Y', MASK_Y),
    ('L', MASK_L),
)
CONTROLLER_TEST_HOLD_S = 0.2
CONTROLLER_TEST_WARMUP_LABEL = 'R'
CONTROLLER_TEST_WARMUP_MASK = MASK_R
CONTROLLER_TEST_WARMUP_HOLD_S = 0.35
CONTROLLER_TEST_WARMUP_SETTLE_S = 2.0

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
        self.typing_position = None

    def snapshot(self):
        return dict(typing_position=self.typing_position,
                    gamepad=self.gamepad.snapshot(), keyboard=self.keyboard.snapshot())

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
        # Do not advance the macro until the kernel has accepted this report.
        # Replacing a held state with the next state while the host is busy
        # can drop controller presses and reorder or lose keyboard characters.
        self.gamepad.send(report)

    def send_keyboard(self, report):
        # Every key-down must reach the endpoint before its key-up. The worker
        # retains the current state; this wait bounds how long the macro may
        # stall on endpoint backpressure.
        self.keyboard.send(report)

    def press_button(self, mask, hold_s=A_PRESS_S):
        self.send_gamepad(pack_gamepad(buttons=mask))
        time.sleep(hold_s)
        self.send_gamepad(GAMEPAD_NEUTRAL)

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
        for index, ch in enumerate(line):
            self.typing_position = dict(character=index + 1, length=len(line), value=ch)
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
        self.typing_position = None


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


def available_memory_kb(meminfo_path='/proc/meminfo'):
    """Return Linux MemAvailable, or None outside a Linux procfs system."""
    try:
        for line in Path(meminfo_path).read_text().splitlines():
            if line.startswith('MemAvailable:'):
                return int(line.split()[1])
    except FileNotFoundError:
        return None
    raise RuntimeError(f'MemAvailable missing from {meminfo_path}')


def ensure_memory_headroom(min_kb=MIN_MEM_AVAILABLE_KB,
                           meminfo_path='/proc/meminfo'):
    """Refuse to start another line when a crash would leave a key held."""
    available = available_memory_kb(meminfo_path)
    if available is not None and available < min_kb:
        raise MemoryError(
            f'CivSwitch memory guard: only {available} kB available; '
            f'need at least {min_kb} kB before starting another line')
    return available


def reclaim_python_memory():
    """Release cyclic Python allocations at safe, between-input boundaries."""
    gc.collect()


def load_payload_lines(payload_path):
    with open(payload_path, encoding='utf-8') as f:
        return [line for line in f.read().splitlines() if line.strip()]


def record_event(event, hid=None, **context):
    """Persist bounded checkpoints only between lines or after an exception.

    Do not fsync on each key: that would change key hold times. The last
    in-memory character and endpoint state are captured if the macro fails.
    A hard reset/OOM kill may leave only the preceding line checkpoint.
    """
    data = dict(event=event, monotonic=time.monotonic(), **context)
    if hid is not None and hasattr(hid, 'snapshot'):
        data['hid'] = hid.snapshot()
    for name, path in (('uptime', '/proc/uptime'), ('memory', '/proc/meminfo'),
                       ('process', '/proc/self/status')):
        try:
            content = Path(path).read_text()
            if name != 'uptime':
                content = '\n'.join(line for line in content.splitlines()
                                    if line.startswith(('MemTotal:', 'MemFree:',
                                                        'MemAvailable:', 'VmRSS:',
                                                        'VmSize:', 'Threads:')))
            data[name] = content.strip()
        except OSError:
            pass
    try:
        data['usb'] = {str(path): path.read_text().strip()
                       for path in Path('/sys/class/udc').glob('*/state')}
    except OSError as error:
        data['usb_error'] = str(error)
    print('CivSwitch diagnostic: ' + json.dumps(data, sort_keys=True), flush=True)
    flush_diagnostics()


def flush_diagnostics():
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        os.fsync(sys.stdout.fileno())
    except (OSError, ValueError):
        pass  # Interactive terminals and test captures need not support fsync.


def run_macro(payload_path):
    lines = load_payload_lines(payload_path)

    if not lines:
        print('No payload lines to type.')
        return

    wait_for_usb()
    hid = HIDWriter()
    phase = 'preamble'
    line_number = None
    try:
        record_event('start', hid, payload=payload_path, total=len(lines))
        print(f'CivSwitch: {len(lines)} line(s), starting in '
              f'{LINUX_USB_READY_DELAY_S}s...')
        time.sleep(LINUX_USB_READY_DELAY_S)
        set_led(True)
        # Preamble: press A x5
        for i in range(PREAMBLE_A_COUNT):
            hid.press_a()
            time.sleep(LINUX_PREAMBLE_GAP_S)

        for i, line in enumerate(lines):
            line_number = i + 1
            phase = 'open-chat'
            available_kb = ensure_memory_headroom()
            record_event('line-start', hid, line=line_number, total=len(lines),
                         length=len(line), mem_available_kb=available_kb)
            print(f'  [{i+1}/{len(lines)}] {line[:60]}{"..." if len(line) > 60 else ""}')

            # Esc -> wait -> A -> wait
            hid.press_key(HID_KEY_ESCAPE)
            time.sleep(ESC_TO_A_WAIT_S)
            hid.press_a()
            time.sleep(POST_A_WAIT_S)

            # Type the line
            phase = 'type'
            hid.type_line(line)
            reclaim_python_memory()

            # Enter x2
            phase = 'submit'
            hid.press_key(HID_KEY_ENTER, hold_s=ENTER_HOLD_S)
            time.sleep(ENTER_GAP_S)
            hid.press_key(HID_KEY_ENTER, hold_s=ENTER_HOLD_S)

            # Separator between batches
            phase = 'separator'
            if i < len(lines) - 1:
                time.sleep(PRE_A_WAIT_S)
                hid.press_a()
                time.sleep(POST_A_WAIT_S)

            record_event('line-complete', hid, line=line_number)
            reclaim_python_memory()

        print('CivSwitch: macro complete.')
        record_event('complete', hid, lines=len(lines))
        set_led(False)
        hid.wait()
    except Exception:
        record_event('failure', hid, line=line_number, phase=phase)
        traceback.print_exc()
        flush_diagnostics()
        raise
    finally:
        set_led(False)
        hid.close()


def run_single_line(payload_path, line_number):
    """Type one indexed payload line, then release both HID interfaces.

    This mode is controlled from the host one line at a time. It deliberately
    omits the initial five-A preamble, so the Switch must already be at the
    chat screen. Each invocation has its own memory guard and clean shutdown.
    """
    lines = load_payload_lines(payload_path)
    if not lines:
        raise ValueError('No payload lines to type')
    if not 1 <= line_number <= len(lines):
        raise ValueError(
            f'Line must be between 1 and {len(lines)}, got {line_number}')

    line = lines[line_number - 1]
    wait_for_usb()
    hid = HIDWriter()
    phase = 'open-chat'
    try:
        available_kb = ensure_memory_headroom()
        record_event('manual-line-start', hid, line=line_number,
                     total=len(lines), length=len(line),
                     mem_available_kb=available_kb)
        print(f'  [{line_number}/{len(lines)}] {line[:60]}'
              f'{"..." if len(line) > 60 else ""}', flush=True)

        hid.press_key(HID_KEY_ESCAPE)
        time.sleep(ESC_TO_A_WAIT_S)
        hid.press_a()
        time.sleep(POST_A_WAIT_S)

        phase = 'type'
        hid.type_line(line)
        reclaim_python_memory()

        phase = 'submit'
        hid.press_key(HID_KEY_ENTER, hold_s=ENTER_HOLD_S)
        time.sleep(ENTER_GAP_S)
        hid.press_key(HID_KEY_ENTER, hold_s=ENTER_HOLD_S)

        record_event('manual-line-complete', hid, line=line_number,
                     total=len(lines))
    except Exception:
        record_event('failure', hid, line=line_number, phase=phase)
        traceback.print_exc()
        flush_diagnostics()
        raise
    finally:
        set_led(False)
        hid.close()


def run_controller_test():
    """Send an R warm-up and five distinct buttons with the keyboard released."""
    wait_for_usb()
    hid = HIDWriter()
    try:
        sequence = ' '.join(label for label, _ in CONTROLLER_TEST_SEQUENCE)
        print(f'CivSwitch: controller test; sending {sequence}, no text.',
              flush=True)
        time.sleep(LINUX_USB_READY_DELAY_S)
        # The physical Switch accepts the first changed state only after it has
        # initialized the controller pipeline. Use a separate button so that
        # this warm-up cannot steal the first state from the counted sequence.
        print(f'CivSwitch: controller test warm-up: '
              f'{CONTROLLER_TEST_WARMUP_LABEL} (not counted)', flush=True)
        hid.press_button(CONTROLLER_TEST_WARMUP_MASK,
                         hold_s=CONTROLLER_TEST_WARMUP_HOLD_S)
        time.sleep(CONTROLLER_TEST_WARMUP_SETTLE_S)
        for label, mask in CONTROLLER_TEST_SEQUENCE:
            print(f'CivSwitch: controller test: {label}', flush=True)
            hid.press_button(mask, hold_s=CONTROLLER_TEST_HOLD_S)
            time.sleep(LINUX_PREAMBLE_GAP_S)
        record_event('controller-test-complete', hid, sequence=sequence)
        print('CivSwitch: controller test complete; reports now neutral.',
              flush=True)
        hid.wait()
    finally:
        hid.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('payload', nargs='?', default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'payload.txt'))
    parser.add_argument('--test-controller', action='store_true',
                        help='send uncounted R, then A, B, X, Y, L; '
                             'never type the payload')
    parser.add_argument('--line', type=int, metavar='N',
                        help='type only payload line N and exit; no five-A preamble')
    args = parser.parse_args()

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        if args.test_controller and args.line is not None:
            parser.error('--test-controller and --line cannot be combined')
        if args.test_controller:
            run_controller_test()
        elif args.line is not None:
            run_single_line(args.payload, args.line)
        else:
            run_macro(args.payload)
    except KeyboardInterrupt:
        print('CivSwitch: stopped.', flush=True)
