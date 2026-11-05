"""Host-side transport tests; these do not emulate a Nintendo Switch."""

import ast
import contextlib
import io
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import civswitch


class HIDTransportTests(unittest.TestCase):
    def setUp(self):
        self.devices = {}
        self.hosts = {}
        for name in ('pad', 'keyboard'):
            device, host = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
            host.settimeout(0.5)
            self.devices[name] = device
            self.hosts[name] = host
        # Only the device-open boundary is mocked. Actual fd I/O, readiness,
        # concurrency, and host packets use real socket pairs.
        self.open_patch = patch.object(
            civswitch.os, 'open', side_effect=lambda name, flags:
            os.dup(self.devices[name].fileno()))
        self.open_patch.start()
        self.writer = civswitch.HIDWriter('pad', 'keyboard')

    def tearDown(self):
        self.writer.close()
        self.open_patch.stop()
        for sock in (*self.devices.values(), *self.hosts.values()):
            sock.close()

    def receive_until(self, name, expected):
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            if self.hosts[name].recv(64) == expected:
                return
        self.fail('Host did not receive expected report: ' + expected.hex())

    def test_both_interfaces_keep_reporting_without_macro_events(self):
        for name, report in (('pad', civswitch.GAMEPAD_NEUTRAL),
                             ('keyboard', civswitch.KBD_RELEASE)):
            self.assertEqual(self.hosts[name].recv(64), report)
            self.assertEqual(self.hosts[name].recv(64), report)

    def test_a_state_repeats_while_keyboard_changes(self):
        self.writer.send_gamepad(civswitch.GAMEPAD_A)
        self.receive_until('pad', civswitch.GAMEPAD_A)
        self.writer.send_keyboard(civswitch.pack_keyboard(keycode=4))
        self.receive_until('keyboard', civswitch.pack_keyboard(keycode=4))
        self.assertEqual(self.hosts['pad'].recv(64), civswitch.GAMEPAD_A)
        self.writer.send_gamepad(civswitch.GAMEPAD_NEUTRAL)
        self.receive_until('pad', civswitch.GAMEPAD_NEUTRAL)

    def test_gamepad_host_output_is_echoed_then_state_resumes(self):
        # TinyUSB passes the host's actual output length to the echo callback.
        output = bytes(range(64))
        self.hosts['pad'].send(output)
        self.receive_until('pad', output)
        self.receive_until('pad', civswitch.GAMEPAD_NEUTRAL)

    def test_keyboard_led_output_is_consumed_without_becoming_a_key(self):
        with patch.object(civswitch, 'set_led') as led:
            self.hosts['keyboard'].send(b'\x02')
            deadline = time.monotonic() + 0.5
            while not led.called and time.monotonic() < deadline:
                time.sleep(0.001)
            led.assert_called_with(True)
        self.assertEqual(self.hosts['keyboard'].recv(64), civswitch.KBD_RELEASE)

    def empty_host_output(self, interface):
        """Linux f_hid read() returns zero for a completed zero-length OUT."""
        real_read = os.read
        received = threading.Event()

        def read(fd, count):
            output = real_read(fd, count)
            if output == b'':
                received.set()
            return output

        with patch.object(civswitch.os, 'read', side_effect=read):
            self.hosts[interface].send(b'')
            self.assertTrue(received.wait(0.5), 'Worker did not consume empty OUT packet')

    def test_empty_keyboard_output_does_not_leave_f_held(self):
        # A zero-length datagram models a USB packet, not stream EOF.
        held_f = civswitch.pack_keyboard(keycode=9)
        self.writer.send_keyboard(held_f)
        self.receive_until('keyboard', held_f)
        self.empty_host_output('keyboard')
        next_key = civswitch.pack_keyboard(keycode=18)  # next letter in "for"
        self.writer.send_keyboard(next_key)
        self.receive_until('keyboard', next_key)
        self.writer.send_keyboard(civswitch.KBD_RELEASE)
        self.receive_until('keyboard', civswitch.KBD_RELEASE)

    def test_empty_gamepad_output_does_not_stop_controller(self):
        self.empty_host_output('pad')
        self.writer.send_gamepad(civswitch.GAMEPAD_A)
        self.receive_until('pad', civswitch.GAMEPAD_A)
        self.writer.send_gamepad(civswitch.GAMEPAD_NEUTRAL)
        self.receive_until('pad', civswitch.GAMEPAD_NEUTRAL)

    def test_led_io_failure_does_not_stop_keyboard_reports(self):
        attempted = threading.Event()

        def failed_led_open(*args, **kwargs):
            attempted.set()
            raise OSError('Simulated LED sysfs I/O failure')

        with patch('builtins.open', side_effect=failed_led_open):
            self.hosts['keyboard'].send(b'\x02')
            self.assertTrue(attempted.wait(0.5))
            self.writer.send_keyboard(civswitch.KBD_RELEASE)
            self.receive_until('keyboard', civswitch.KBD_RELEASE)

    def test_close_stops_report_workers(self):
        self.writer.close()
        for host in self.hosts.values():
            host.setblocking(False)
            while True:
                try:
                    host.recv(64)
                except BlockingIOError:
                    break
            time.sleep(0.01)
            with self.assertRaises(BlockingIOError):
                host.recv(64)

    def test_host_disconnect_reaches_macro_instead_of_silent_progress(self):
        # Close both local duplicates of the device peer so the worker sees
        # the host disappear on its next actual send.
        self.hosts['pad'].close()
        deadline = time.monotonic() + 0.5
        while self.writer.gamepad.error is None and time.monotonic() < deadline:
            time.sleep(0.001)
        with self.assertRaises(OSError):
            self.writer.send_gamepad(civswitch.GAMEPAD_A)


class MacroParityTests(unittest.TestCase):
    """Compare Python output states against the actual Pico header generator."""

    def pico_trace(self, payload):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'payload.txt'
            header = Path(directory) / 'macro_script.h'
            source.write_text(payload)
            subprocess.run([sys.executable,
                            str(root / 'PicoSwitch/tools/payload_to_macro.py'),
                            str(source), str(header)], check=True,
                           stdout=subprocess.DEVNULL)
            generated = header.read_text()
        # USB HID Usage Table values, independently of the Luckfox map.
        names = {f'HID_KEY_{c}': 4 + i for i, c in enumerate('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}
        names.update({f'HID_KEY_{c}': 30 + i for i, c in enumerate('1234567890')})
        names.update({f'HID_KEY_{key}': value for key, value in zip(
            ('ENTER', 'ESCAPE', 'BACKSPACE', 'TAB', 'SPACE', 'MINUS', 'EQUAL',
             'BRACKET_LEFT', 'BRACKET_RIGHT', 'BACKSLASH', 'EUROPE_1',
             'SEMICOLON', 'APOSTROPHE', 'GRAVE', 'COMMA', 'PERIOD', 'SLASH'),
            range(40, 57))})
        names['0'] = 0
        names['KEYBOARD_MODIFIER_LEFTSHIFT'] = 2
        engine = (root / 'PicoSwitch/include/MacroEngine.h').read_text()
        delay = int(re.search(r'#define MACRO_START_DELAY_MS (\d+)', engine).group(1))
        trace = [(delay, civswitch.GAMEPAD_NEUTRAL, civswitch.KBD_RELEASE)]
        for op, arguments in re.findall(r'^    (KEYS|WAIT|PAD_BTN)\((.*?)\),$',
                                        generated, re.MULTILINE):
            args = [arg.strip() for arg in arguments.split(',')]
            milliseconds = int(args[0])
            pad, keyboard = civswitch.GAMEPAD_NEUTRAL, civswitch.KBD_RELEASE
            if op == 'KEYS':
                keyboard = bytes([names[args[1]], 0] + [names[arg] for arg in args[2:]])
            elif op == 'PAD_BTN':
                self.assertEqual(args[1], 'SWITCH_USB_MASK_A')
                pad = b'\x04\x00\x08\x80\x80\x80\x80\x00'
            if milliseconds:
                trace.append((milliseconds, pad, keyboard))
        return trace

    def python_trace(self, payload):
        trace = []

        class Recorder:
            pad = civswitch.GAMEPAD_NEUTRAL
            keyboard = civswitch.KBD_RELEASE
            press_a = civswitch.HIDWriter.press_a
            press_key = civswitch.HIDWriter.press_key
            type_line = civswitch.HIDWriter.type_line

            def send_gamepad(self, report):
                self.pad = report

            def send_keyboard(self, report):
                self.keyboard = report

            def close(self):
                pass

            def wait(self):
                pass

        recorder = Recorder()

        def sleep(seconds):
            if seconds:
                trace.append((round(seconds * 1000), recorder.pad, recorder.keyboard))

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'payload.txt'
            source.write_text(payload)
            with patch.object(civswitch, 'HIDWriter', return_value=recorder), \
                    patch.object(civswitch.time, 'sleep', side_effect=sleep), \
                    patch.object(civswitch, 'wait_for_usb'), \
                    patch.object(civswitch, 'set_led'), contextlib.redirect_stdout(io.StringIO()):
                civswitch.run_macro(str(source))
        return trace

    def test_all_printable_keys_and_repeated_keys_match_pico_states_and_durations(self):
        payload = ''.join(chr(i) for i in range(32, 127)) + '\taaAA!!00\nsecond line\n'
        self.assertEqual(self.python_trace(payload), self.pico_trace(payload))

    def test_shipped_payload_matches_pico_states_and_durations(self):
        payload = Path(__file__).resolve().parents[1] / 'PicoSwitch/payload.txt'
        if not payload.exists():
            self.skipTest('Generate PicoSwitch/payload.txt first for full payload parity')
        text = payload.read_text()
        self.assertEqual(self.python_trace(text), self.pico_trace(text))


class DescriptorParityTests(unittest.TestCase):
    def test_hid_report_descriptors_match_pico_bytes(self):
        root = Path(__file__).resolve().parents[1]
        pico = (root / 'PicoSwitch/include/SwitchConsts.h').read_text()
        script = (root / 'LuckfoxSwitch/usb-gadget-hid.sh').read_text()
        linux_arrays = re.findall(r'sys.stdout.buffer.write\(bytes\((\[.*?\])\)\)',
                                  script, re.DOTALL)
        self.assertEqual(len(linux_arrays), 2)
        for name, linux_array in zip(
                ('switch_usb_report_descriptor', 'keyboard_usb_report_descriptor'),
                linux_arrays):
            body = re.search(rf'{name}\[\] = \{{(.*?)\}};', pico, re.DOTALL).group(1)
            body = re.sub(r'//[^\n]*', '', body)
            expected = bytes(int(x.strip(), 0) for x in body.split(',') if x.strip())
            self.assertEqual(bytes(ast.literal_eval(linux_array)), expected)

    def test_gamepad_and_keyboard_packet_sizes_match_pico_endpoints(self):
        root = Path(__file__).resolve().parents[1]
        pico = (root / 'PicoSwitch/include/SwitchConsts.h').read_text()
        body = re.search(r'switch_usb_configuration_descriptor\[\] = \{(.*?)\};',
                         pico, re.DOTALL).group(1)
        body = re.sub(r'//[^\n]*', '', body)
        descriptor = bytes(int(x.strip(), 0) for x in body.split(',') if x.strip())
        interface = None
        packet_sizes = {}
        offset = 0
        while offset < len(descriptor):
            item = descriptor[offset:offset + descriptor[offset]]
            if item[1] == 4:
                interface = item[2]
            elif item[1] == 5:
                packet_sizes.setdefault(interface, set()).add(int.from_bytes(item[4:6], 'little'))
            offset += len(item)
        script = (root / 'LuckfoxSwitch/usb-gadget-hid.sh').read_text()
        for interface in (0, 1):
            configured = int(re.search(
                rf'echo (\d+) > "\$HID{interface}/report_length"', script).group(1))
            self.assertEqual({configured}, packet_sizes[interface])


class StartupTests(unittest.TestCase):
    def test_waits_until_host_configures_usb(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Path(directory) / 'controller'
            controller.mkdir()
            state = controller / 'state'
            state.write_text('attached\n')
            with patch.object(civswitch.time, 'sleep',
                              side_effect=lambda _: state.write_text('configured\n')) as sleep:
                civswitch.wait_for_usb(directory)
            sleep.assert_called_once_with(0.1)

    def test_missing_controller_fails_without_starting_macro(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, 'No USB device controller'):
                civswitch.wait_for_usb(directory)

    def test_controller_diagnostic_sends_five_a_presses_without_keyboard_input(self):
        with patch.object(civswitch, 'wait_for_usb'), \
                patch.object(civswitch, 'HIDWriter') as writer, \
                patch.object(civswitch.time, 'sleep'):
            civswitch.run_controller_test()
        self.assertEqual(writer.return_value.press_a.call_count, 5)
        writer.return_value.send_keyboard.assert_not_called()
        writer.return_value.type_line.assert_not_called()
        writer.return_value.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
