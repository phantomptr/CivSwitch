"""Host-side transport tests; these do not emulate a Nintendo Switch."""

import ast
import contextlib
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import re
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import call, patch

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
        # The workers continuously repeat neutral reports. Drain the bounded
        # socket pairs before close, otherwise a backpressure test can leave
        # close() waiting for space while the host deliberately is not polling.
        try:
            for host in self.hosts.values():
                if host.fileno() < 0:
                    continue
                host.setblocking(False)
                while True:
                    try:
                        host.recv(64)
                    except BlockingIOError:
                        break
                    except OSError:
                        break
        finally:
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

    def drain(self, name):
        drained = 0
        while select.select([self.hosts[name]], [], [], 0)[0]:
            self.hosts[name].recv(64)
            drained += 1
        return drained

    def assert_no_report(self, name, timeout=0.12):
        self.assertFalse(
            select.select([self.hosts[name]], [], [], timeout)[0],
            f'{name} sent an unchanged HID report')

    def fill_device_send_queue(self, name, packet):
        device = self.devices[name]
        device.setblocking(False)
        try:
            while True:
                device.send(packet)
        except (BlockingIOError, OSError) as error:
            if isinstance(error, OSError) and error.errno not in (
                    errno.EAGAIN, errno.EWOULDBLOCK, errno.ENOBUFS):
                raise
            pass
        finally:
            device.setblocking(True)

    def test_initial_neutral_reports_are_not_repeated_forever(self):
        for name, report in (('pad', civswitch.GAMEPAD_NEUTRAL),
                             ('keyboard', civswitch.KBD_RELEASE)):
            self.assertEqual(self.hosts[name].recv(64), report)
        # USB HID retains the last report. Repeating an unchanged neutral
        # state creates thousands of unnecessary driver transactions, which
        # leaked roughly 1 KiB each on the Luckfox f_hid/dwc2 path.
        time.sleep(0.05)
        self.drain('pad')
        self.drain('keyboard')
        self.assert_no_report('pad')
        self.assert_no_report('keyboard')

    def test_state_changes_are_sent_without_repeating_held_state(self):
        self.drain('pad')
        self.drain('keyboard')
        self.writer.send_gamepad(civswitch.GAMEPAD_A)
        self.receive_until('pad', civswitch.GAMEPAD_A)
        self.assert_no_report('pad')
        self.writer.send_keyboard(civswitch.pack_keyboard(keycode=4))
        self.receive_until('keyboard', civswitch.pack_keyboard(keycode=4))
        self.assert_no_report('keyboard')
        self.assert_no_report('pad')
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

    def test_key_state_waits_instead_of_dropping_under_host_backpressure(self):
        # A real bounded fd queue models a host that pauses while the Switch
        # redraws. The key-down must remain pending until the endpoint accepts
        # it; replacing it with key-up would lose the character even though
        # the macro line counter advanced.
        held = civswitch.pack_keyboard(keycode=civswitch.ASCII_TO_HID['s'][1])
        self.writer.send_keyboard(held)
        self.receive_until('keyboard', held)
        self.devices['keyboard'].setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2048)
        self.fill_device_send_queue('keyboard', held)
        sender = threading.Thread(
            target=self.writer.send_keyboard, args=(civswitch.KBD_RELEASE,))
        sender.start()
        sender.join(timeout=0.25)
        self.assertTrue(sender.is_alive())
        self.assertTrue(self.writer.keyboard.worker.is_alive())

        # Resume host polling. The release is accepted only after the held
        # state has been queued, preserving report order and the character.
        self.receive_until('keyboard', civswitch.KBD_RELEASE)
        self.receive_until('pad', civswitch.GAMEPAD_NEUTRAL)
        sender.join(timeout=0.5)
        self.assertFalse(sender.is_alive())
        state = self.writer.keyboard.snapshot()
        self.assertEqual(state['last_queued'], civswitch.KBD_RELEASE.hex())
        self.assertEqual(state['queued_generation'], state['generation'])

    def test_forced_reports_are_bounded_and_identical(self):
        self.writer.send_keyboard(civswitch.KBD_RELEASE)
        self.drain('keyboard')
        initial_writes = self.writer.keyboard.snapshot()['writes']
        key_down = civswitch.pack_keyboard(keycode=civswitch.HID_KEY_A)
        self.writer.send_keyboard_repeated(key_down, 3, 0)
        for _ in range(3):
            self.receive_until('keyboard', key_down)
        self.assert_no_report('keyboard')
        self.assertEqual(self.writer.keyboard.snapshot()['writes'],
                         initial_writes + 3)

    def test_repeated_report_count_must_be_positive(self):
        with self.assertRaisesRegex(ValueError, 'must be positive'):
            self.writer.send_keyboard_repeated(civswitch.KBD_RELEASE, 0, 0)

    def test_missing_host_polling_becomes_a_bounded_transport_error(self):
        # The macro no longer waits on each transfer, so the worker owns the
        # liveness check. A disconnected or permanently suspended host must
        # still reach run_macro's failure diagnostics.
        self.devices['keyboard'].setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2048)
        self.fill_device_send_queue('keyboard', civswitch.KBD_RELEASE)
        with patch.object(civswitch, 'HID_ACK_TIMEOUT_S', 0.15):
            with self.assertRaises((TimeoutError, OSError)) as caught:
                self.writer.send_keyboard(civswitch.pack_keyboard(keycode=4))
        if isinstance(caught.exception, TimeoutError):
            self.assertIn('USB host is not accepting HID reports',
                          str(caught.exception))
        else:
            self.assertIn(caught.exception.errno,
                          (errno.EAGAIN, errno.EWOULDBLOCK, errno.ENOBUFS))


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
            press_button = civswitch.HIDWriter.press_button
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

    def normalize_linux_preamble(self, trace):
        self.assertEqual(
            trace[:3],
            [
                (round(civswitch.LINUX_USB_READY_DELAY_S * 1000),
                 civswitch.GAMEPAD_NEUTRAL, civswitch.KBD_RELEASE),
                (round(civswitch.CONTROLLER_WARMUP_HOLD_S * 1000),
                 civswitch.pack_gamepad(buttons=civswitch.MASK_R),
                 civswitch.KBD_RELEASE),
                (round(civswitch.CONTROLLER_WARMUP_SETTLE_S * 1000),
                 civswitch.GAMEPAD_NEUTRAL, civswitch.KBD_RELEASE),
            ])
        # The bounded Switch 1 first-state warm-up is intentionally Linux-only.
        # Everything after it must retain Pico state and timing parity.
        trace = [trace[0]] + trace[3:]
        linux_gap = round(civswitch.LINUX_PREAMBLE_GAP_S * 1000)
        pico_gap = round(civswitch.PREAMBLE_GAP_S * 1000)
        return [
            (pico_gap, pad, keyboard)
            if (milliseconds == linux_gap and
                pad == civswitch.GAMEPAD_NEUTRAL and
                keyboard == civswitch.KBD_RELEASE)
            else (milliseconds, pad, keyboard)
            for milliseconds, pad, keyboard in trace
        ]

    def test_all_printable_keys_and_repeated_keys_match_pico_states_and_durations(self):
        payload = ''.join(chr(i) for i in range(32, 127)) + '\taaAA!!00\nsecond line\n'
        python = self.normalize_linux_preamble(self.python_trace(payload))
        pico = self.pico_trace(payload)
        self.assertEqual(python[0][0],
                         round(civswitch.LINUX_USB_READY_DELAY_S * 1000))
        self.assertEqual(python[1:], pico[1:])

    def test_shipped_payload_matches_pico_states_and_durations(self):
        payload = Path(__file__).resolve().parents[1] / 'PicoSwitch/payload.txt'
        if not payload.exists():
            self.skipTest('Generate PicoSwitch/payload.txt first for full payload parity')
        text = payload.read_text()
        python = self.normalize_linux_preamble(self.python_trace(text))
        pico = self.pico_trace(text)
        self.assertEqual(python[0][0],
                         round(civswitch.LINUX_USB_READY_DELAY_S * 1000))
        self.assertEqual(python[1:], pico[1:])

    def test_pico_and_luckfox_payloads_are_the_87_line_source_artifact(self):
        root = Path(__file__).resolve().parents[1]
        paths = (root / 'PicoSwitch/payload.txt',
                 root / 'LuckfoxSwitch/payload.txt')
        if not all(path.exists() for path in paths):
            self.skipTest('Generate the payload before checking deployed parity')
        payloads = [path.read_bytes() for path in paths]
        self.assertEqual(payloads[0], payloads[1])
        self.assertEqual(len(payloads[0].splitlines()), 87)
        self.assertEqual(
            hashlib.sha256(payloads[0]).hexdigest(),
            '3b0f97d015fd465fcf60bbd302aebd56bc2268730d03188f543cf791f9fb240f')


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

    def test_gamepad_uses_pico_compatible_interrupt_output(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / 'LuckfoxSwitch/usb-gadget-hid.sh').read_text()
        self.assertNotIn('"$HID0/no_out_endpoint"', script)
        self.assertIn('echo 1 > "$HID1/no_out_endpoint"', script)
        self.assertNotIn('CIVSWITCH_USB_SPEED', script)

    def test_luckfox_reuses_the_vendor_rockchip_gadget(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / 'LuckfoxSwitch/usb-gadget-hid.sh').read_text()
        self.assertIn('if [ -d "$CONFIGFS/rockchip" ]; then', script)
        self.assertIn('GADGET="$CONFIGFS/rockchip"', script)
        self.assertIn('/etc/init.d/S50usbdevice stop', script)
        self.assertNotIn('echo "$USB_SPEED" > "$GADGET/max_speed"', script)

    def test_gadget_releases_vendor_and_reused_hid_device_ids(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / 'LuckfoxSwitch/usb-gadget-hid.sh').read_text()
        self.assertIn('"$CONFIGFS"/*/functions/hid.usb*', script)
        self.assertIn('while [ ! -c /dev/hidg0 ] || [ ! -c /dev/hidg1 ]; do',
                      script)

    def test_gadget_blocks_vendor_usb_uevent_reclaim(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / 'LuckfoxSwitch/usb-gadget-hid.sh').read_text()
        restore = (root / 'LuckfoxSwitch/restore-default-gadget.sh').read_text()
        self.assertIn("echo USB_FORCE_CHANGED >> /tmp/.usb_config", script)
        self.assertIn("awk '/[S]99usb0config/ {print $1}'", script)
        self.assertIn('dmesg -n 1', script)
        self.assertIn('/etc/init.d/S10udev stop', script)
        self.assertIn("sed -i '/^USB_FORCE_CHANGED$/d' /tmp/.usb_config",
                      restore)
        self.assertIn('/sbin/udevd -d', restore)
        self.assertNotIn('/etc/init.d/S10udev start', restore)
        self.assertIn('/etc/init.d/S50usbdevice start', restore)
        self.assertNotIn('/etc/init.d/S50usbdevice restart', restore)
        self.assertIn('USB_CONFIG_FILE=/tmp/.usb_config', restore)
        self.assertIn('usb_rndis_en\\nusb_adb_en', restore)
        self.assertLess(restore.index('/etc/init.d/S50usbdevice start'),
                        restore.index(
                            "sed -i '/^USB_FORCE_CHANGED$/d' /tmp/.usb_config"))
        self.assertIn('/run/civswitch.printk', restore)


class StartupTests(unittest.TestCase):
    def test_resume_keeps_original_line_numbers_and_skips_startup_buttons(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory) / 'payload.txt'
            payload.write_text('first\nsecond\nthird\n')
            with patch.object(civswitch, 'wait_for_usb'), \
                    patch.object(civswitch, 'HIDWriter') as writer, \
                    patch.object(civswitch.time, 'sleep'), \
                    patch.object(civswitch, 'set_led'), \
                    patch.object(civswitch, 'record_event') as record:
                civswitch.run_macro(str(payload), start_line=2, skip_preamble=True)
            self.assertEqual(writer.return_value.type_line.call_args_list,
                             [call('second'), call('third')])
            writer.return_value.press_button.assert_not_called()
            self.assertEqual([x.kwargs['line'] for x in record.call_args_list
                              if x.args[0] == 'line-complete'], [2, 3])
            writer.return_value.close.assert_called_once()

    def test_resume_rejects_invalid_line_before_opening_usb(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory) / 'payload.txt'
            payload.write_text('first\n')
            with patch.object(civswitch, 'wait_for_usb') as wait:
                with self.assertRaises(ValueError):
                    civswitch.run_macro(str(payload), start_line=2, skip_preamble=True)
            wait.assert_not_called()

    def test_bounded_controller_press_closes_without_waiting_or_typing(self):
        with patch.object(civswitch, 'wait_for_usb'), \
                patch.object(civswitch, 'HIDWriter') as writer, \
                patch.object(civswitch.time, 'sleep'), \
                patch.object(civswitch, 'record_event'):
            civswitch.run_controller_test('A', keep_active=False)
        writer.return_value.wait.assert_not_called()
        writer.return_value.type_line.assert_not_called()
        writer.return_value.close.assert_called_once()

    def test_memory_guard_rejects_a_line_when_headroom_is_too_low(self):
        with tempfile.TemporaryDirectory() as directory:
            meminfo = Path(directory) / 'meminfo'
            meminfo.write_text('MemTotal: 55512 kB\nMemAvailable: 4096 kB\n')
            self.assertEqual(
                civswitch.available_memory_kb(meminfo), 4096)
            with self.assertRaisesRegex(MemoryError, '4096 kB available'):
                civswitch.ensure_memory_headroom(6144, meminfo)

    def test_low_memory_stops_before_the_next_line_can_oom(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory) / 'payload.txt'
            payload.write_text('test\n')
            output = io.StringIO()
            with contextlib.redirect_stdout(output), \
                    contextlib.redirect_stderr(output), \
                    patch.object(civswitch, 'HIDWriter') as writer, \
                    patch.object(civswitch, 'wait_for_usb'), \
                    patch.object(civswitch.time, 'sleep'), \
                    patch.object(civswitch, 'set_led'), \
                    patch.object(
                        civswitch, 'ensure_memory_headroom',
                        side_effect=MemoryError('only 4096 kB available')):
                writer.return_value.snapshot.return_value = {
                    'typing_position': None}
                with self.assertRaisesRegex(MemoryError, '4096 kB'):
                    civswitch.run_macro(str(payload))
            writer.return_value.type_line.assert_not_called()
            writer.return_value.close.assert_called_once()

    def test_macro_failure_records_line_character_and_traceback_in_a_real_file(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory) / 'payload.txt'
            payload.write_text('test\n')
            log = Path(directory) / 'current.log'
            with log.open('w') as output, contextlib.redirect_stdout(output), \
                    contextlib.redirect_stderr(output), \
                    patch.object(civswitch, 'HIDWriter') as writer, \
                    patch.object(civswitch, 'wait_for_usb'), \
                    patch.object(civswitch.time, 'sleep'), \
                    patch.object(civswitch, 'set_led'):
                writer.return_value.snapshot.return_value = {
                    'typing_position': {'character': 3, 'length': 4, 'value': 's'}}
                writer.return_value.type_line.side_effect = TimeoutError('host paused')
                with self.assertRaises(TimeoutError):
                    civswitch.run_macro(str(payload))
                writer.return_value.close.assert_called_once()
            content = log.read_text()
            events = [json.loads(line.split(': ', 1)[1]) for line in content.splitlines()
                      if line.startswith('CivSwitch diagnostic: ')]
            self.assertEqual([event['event'] for event in events],
                             ['start', 'line-start', 'failure'])
            failure = events[-1]
            self.assertEqual((failure['line'], failure['phase']), (1, 'type'))
            self.assertEqual(failure['hid']['typing_position']['value'], 's')
            self.assertIn('TimeoutError: host paused', content)

    def test_successful_macro_records_start_and_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory) / 'payload.txt'
            payload.write_text('test\n')
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output), \
                    patch.object(civswitch, 'HIDWriter') as writer, \
                    patch.object(civswitch, 'wait_for_usb'), \
                    patch.object(civswitch.time, 'sleep'), \
                    patch.object(civswitch, 'set_led'), \
                    patch.object(civswitch, 'flush_diagnostics'), \
                    patch.object(civswitch, 'reclaim_python_memory') as reclaim:
                writer.return_value.snapshot.return_value = {'typing_position': None}
                civswitch.run_macro(str(payload))
            events = [json.loads(line.split(': ', 1)[1])
                      for line in output.getvalue().splitlines()
                      if line.startswith('CivSwitch diagnostic: ')]
            self.assertEqual([event['event'] for event in events],
                             ['start', 'line-start', 'line-complete', 'complete'])
            writer.return_value.close.assert_called_once()
            self.assertEqual(reclaim.call_count, 2)

    def test_macro_consumes_first_controller_state_with_one_bounded_warmup(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory) / 'payload.txt'
            payload.write_text('test\n')
            with patch.object(civswitch, 'HIDWriter') as writer, \
                    patch.object(civswitch, 'wait_for_usb'), \
                    patch.object(civswitch.time, 'sleep') as sleep, \
                    patch.object(civswitch, 'set_led'), \
                    patch.object(civswitch, 'flush_diagnostics'), \
                    patch.object(civswitch, 'ensure_memory_headroom',
                                 return_value=100000), \
                    patch.object(civswitch, 'reclaim_python_memory'):
                writer.return_value.snapshot.return_value = {
                    'typing_position': None}
                civswitch.run_macro(str(payload))

        writer.return_value.send_gamepad.assert_called_once_with(
            civswitch.GAMEPAD_NEUTRAL)
        writer.return_value.press_button.assert_called_once_with(
            civswitch.MASK_R,
            hold_s=civswitch.CONTROLLER_WARMUP_HOLD_S)
        self.assertEqual(
            writer.return_value.press_a.call_count,
            civswitch.PREAMBLE_A_COUNT + 1)
        self.assertEqual(
            sleep.call_args_list[:3],
            [call(civswitch.LINUX_USB_READY_DELAY_S),
             call(civswitch.CONTROLLER_WARMUP_SETTLE_S),
             call(civswitch.LINUX_PREAMBLE_GAP_S)])

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

    def test_manual_line_mode_types_one_line_without_the_preamble(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory) / 'payload.txt'
            payload.write_text('first\nsecond\n')
            output = io.StringIO()
            with contextlib.redirect_stdout(output), \
                    contextlib.redirect_stderr(output), \
                    patch.object(civswitch, 'wait_for_usb'), \
                    patch.object(civswitch, 'HIDWriter') as writer, \
                    patch.object(civswitch.time, 'sleep'), \
                    patch.object(civswitch, 'set_led'), \
                    patch.object(civswitch, 'flush_diagnostics'):
                writer.return_value.snapshot.return_value = {
                    'typing_position': None}
                civswitch.run_single_line(str(payload), 2)

            writer.return_value.press_a.assert_called_once_with()
            writer.return_value.press_button.assert_called_once_with(
                civswitch.MASK_R,
                hold_s=civswitch.CONTROLLER_WARMUP_HOLD_S)
            writer.return_value.type_line.assert_called_once_with('second')
            self.assertEqual(
                writer.return_value.press_key.call_count, 3)
            writer.return_value.close.assert_called_once()
            events = [json.loads(line.split(': ', 1)[1])
                      for line in output.getvalue().splitlines()
                      if line.startswith('CivSwitch diagnostic: ')]
            self.assertEqual(
                [event['event'] for event in events],
                ['manual-line-start', 'manual-line-complete'])
            self.assertEqual(events[0]['line'], 2)

    def test_manual_line_mode_rejects_an_out_of_range_line(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory) / 'payload.txt'
            payload.write_text('only line\n')
            with patch.object(civswitch, 'wait_for_usb') as wait:
                with self.assertRaisesRegex(ValueError, 'between 1 and 1'):
                    civswitch.run_single_line(str(payload), 2)
            wait.assert_not_called()

    def test_raw_line_uses_keyboard_only_and_has_no_navigation_input(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory) / 'payload.txt'
            payload.write_text('first\nsecond\n')
            output = io.StringIO()
            with contextlib.redirect_stdout(output), \
                    contextlib.redirect_stderr(output), \
                    patch.object(civswitch, 'wait_for_usb'), \
                    patch.object(civswitch, 'KeyboardHID') as keyboard, \
                    patch.object(civswitch, 'HIDWriter') as gamepad_writer, \
                    patch.object(civswitch.time, 'sleep'), \
                    patch.object(civswitch, 'ensure_memory_headroom',
                                 return_value=100000), \
                    patch.object(civswitch, 'record_event') as record:
                civswitch.run_raw_line(str(payload), 2)

            gamepad_writer.assert_not_called()
            keyboard.assert_called_once_with()
            keyboard.return_value.type_line.assert_called_once_with('second')
            self.assertEqual(
                keyboard.return_value.press_key.call_args_list,
                [call(civswitch.HID_KEY_ENTER,
                      hold_s=civswitch.ENTER_HOLD_S),
                 call(civswitch.HID_KEY_ENTER,
                      hold_s=civswitch.ENTER_HOLD_S)])
            keyboard.return_value.close.assert_called_once()
            self.assertEqual(
                [item.args[0] for item in record.call_args_list],
                ['raw-line-start', 'raw-line-complete'])
            self.assertTrue(all(
                item.kwargs.get('keyboard_only')
                for item in record.call_args_list))

    def test_raw_line_rejects_an_out_of_range_line_before_usb_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory) / 'payload.txt'
            payload.write_text('only line\n')
            with patch.object(civswitch, 'wait_for_usb') as wait:
                with self.assertRaisesRegex(ValueError, 'between 1 and 1'):
                    civswitch.run_raw_line(str(payload), 2)
            wait.assert_not_called()

    def test_chat_keyboard_diagnostic_uses_one_composite_writer_and_does_not_submit(self):
        with patch.object(civswitch, 'wait_for_usb'), \
                patch.object(civswitch, 'HIDWriter') as writer, \
                patch.object(civswitch, 'KeyboardHID') as keyboard, \
                patch.object(civswitch.time, 'sleep') as sleep, \
                patch.object(civswitch, 'ensure_memory_headroom',
                             return_value=100000), \
                patch.object(civswitch, 'reclaim_python_memory') as reclaim, \
                patch.object(civswitch, 'record_event') as record:
            civswitch.run_chat_keyboard_test()

        keyboard.assert_not_called()
        writer.assert_called_once_with()
        writer.return_value.send_gamepad.assert_called_once_with(
            civswitch.GAMEPAD_NEUTRAL)
        writer.return_value.press_button.assert_called_once_with(
            civswitch.MASK_R,
            hold_s=civswitch.CONTROLLER_WARMUP_HOLD_S)
        self.assertEqual(
            writer.return_value.press_key.call_args_list,
            [call(civswitch.HID_KEY_ESCAPE)])
        writer.return_value.press_a.assert_called_once_with()
        writer.return_value.type_line.assert_called_once_with(
            civswitch.CHAT_KEYBOARD_TEST_TEXT)
        self.assertEqual(
            sleep.call_args_list,
            [call(civswitch.LINUX_USB_READY_DELAY_S),
             call(civswitch.CONTROLLER_WARMUP_SETTLE_S),
             call(civswitch.ESC_TO_A_WAIT_S),
             call(civswitch.POST_A_WAIT_S)])
        reclaim.assert_called_once_with()
        writer.return_value.close.assert_called_once()
        self.assertEqual(
            [item.args[0] for item in record.call_args_list],
            ['chat-keyboard-test-start', 'chat-keyboard-test-complete'])

    def test_controller_diagnostic_defaults_to_one_a_without_hidden_warmup(self):
        with patch.object(civswitch, 'wait_for_usb'), \
                patch.object(civswitch, 'HIDWriter') as writer, \
                patch.object(civswitch.time, 'sleep'), \
                patch.object(civswitch, 'record_event') as record:
            civswitch.run_controller_test()
        writer.return_value.send_gamepad.assert_called_once_with(
            civswitch.GAMEPAD_NEUTRAL)
        writer.return_value.press_button.assert_called_once_with(
            civswitch.MASK_A, hold_s=civswitch.CONTROLLER_TEST_HOLD_S)
        writer.return_value.press_a.assert_not_called()
        writer.return_value.send_keyboard.assert_not_called()
        writer.return_value.type_line.assert_not_called()
        self.assertEqual(
            [item.args[0] for item in record.call_args_list],
            ['controller-test-start', 'controller-test-complete'])
        writer.return_value.close.assert_called_once()

    def test_controller_diagnostic_can_probe_one_named_button(self):
        with patch.object(civswitch, 'wait_for_usb'), \
                patch.object(civswitch, 'HIDWriter') as writer, \
                patch.object(civswitch.time, 'sleep'), \
                patch.object(civswitch, 'record_event'):
            civswitch.run_controller_test('B')
        writer.return_value.press_button.assert_called_once_with(
            civswitch.MASK_B, hold_s=civswitch.CONTROLLER_TEST_HOLD_S)
        writer.return_value.send_keyboard.assert_not_called()

    def test_controller_diagnostic_can_probe_l_plus_r_registration_prompt(self):
        with patch.object(civswitch, 'wait_for_usb'), \
                patch.object(civswitch, 'HIDWriter') as writer, \
                patch.object(civswitch.time, 'sleep'), \
                patch.object(civswitch, 'record_event'):
            civswitch.run_controller_test('LR')
        writer.return_value.press_button.assert_called_once_with(
            civswitch.MASK_L | civswitch.MASK_R,
            hold_s=civswitch.CONTROLLER_TEST_HOLD_S)
        writer.return_value.send_keyboard.assert_not_called()

    def test_controller_warmup_diagnostic_presses_r_before_the_probe(self):
        with patch.object(civswitch, 'wait_for_usb'), \
                patch.object(civswitch, 'HIDWriter') as writer, \
                patch.object(civswitch.time, 'sleep'), \
                patch.object(civswitch, 'record_event') as record:
            civswitch.run_controller_test('A', warmup=True)
        writer.return_value.send_gamepad.assert_called_once_with(
            civswitch.GAMEPAD_NEUTRAL)
        self.assertEqual(
            writer.return_value.press_button.call_args_list,
            [call(civswitch.MASK_R,
                  hold_s=civswitch.CONTROLLER_WARMUP_HOLD_S),
             call(civswitch.MASK_A,
                  hold_s=civswitch.CONTROLLER_TEST_HOLD_S)])
        writer.return_value.send_keyboard.assert_not_called()
        self.assertTrue(all(
            item.kwargs.get('warmup') is True
            for item in record.call_args_list))

    def test_controller_preamble_diagnostic_matches_production_startup(self):
        with patch.object(civswitch, 'wait_for_usb'), \
                patch.object(civswitch, 'HIDWriter') as writer, \
                patch.object(civswitch.time, 'sleep') as sleep, \
                patch.object(civswitch, 'record_event') as record:
            civswitch.run_controller_preamble_test()

        writer.return_value.send_gamepad.assert_called_once_with(
            civswitch.GAMEPAD_NEUTRAL)
        writer.return_value.press_button.assert_called_once_with(
            civswitch.MASK_R,
            hold_s=civswitch.CONTROLLER_WARMUP_HOLD_S)
        self.assertEqual(
            writer.return_value.press_a.call_count,
            civswitch.PREAMBLE_A_COUNT)
        self.assertEqual(
            sleep.call_args_list[:3],
            [call(civswitch.LINUX_USB_READY_DELAY_S),
             call(civswitch.CONTROLLER_WARMUP_SETTLE_S),
             call(civswitch.LINUX_PREAMBLE_GAP_S)])
        self.assertEqual(
            [item.args[0] for item in record.call_args_list],
            ['controller-preamble-test-start',
             'controller-preamble-test-complete'])
        writer.return_value.close.assert_called_once()

    def test_controller_diagnostic_rejects_an_unknown_button_before_usb_setup(self):
        with patch.object(civswitch, 'wait_for_usb') as wait:
            with self.assertRaisesRegex(ValueError, 'Unknown controller button'):
                civswitch.run_controller_test('Z')
        wait.assert_not_called()

    def test_keyboard_diagnostic_never_opens_or_writes_the_gamepad(self):
        with patch.object(civswitch, 'wait_for_usb'), \
                patch.object(civswitch, 'KeyboardHID') as keyboard, \
                patch.object(civswitch.time, 'sleep'), \
                patch.object(civswitch, 'record_event') as record:
            civswitch.run_keyboard_test()
        keyboard.assert_called_once_with()
        keyboard.return_value.type_line.assert_called_once_with(
            civswitch.KEYBOARD_TEST_TEXT)
        keyboard.return_value.send_gamepad.assert_not_called()
        self.assertEqual(
            [item.args[0] for item in record.call_args_list],
            ['keyboard-test-start', 'keyboard-test-complete'])
        keyboard.return_value.close.assert_called_once()

    def test_keyboard_alpha_diagnostic_types_a_to_z_without_gamepad(self):
        with patch.object(civswitch, 'wait_for_usb'), \
                patch.object(civswitch, 'KeyboardHID') as keyboard, \
                patch.object(civswitch.time, 'sleep'), \
                patch.object(civswitch, 'record_event') as record:
            civswitch.run_keyboard_alpha_test()
        keyboard.assert_called_once_with()
        keyboard.return_value.type_line.assert_called_once_with(
            'abcdefghijklmnopqrstuvwxyz')
        keyboard.return_value.send_gamepad.assert_not_called()
        self.assertEqual(
            [item.args[0] for item in record.call_args_list],
            ['keyboard-alpha-test-start', 'keyboard-alpha-test-complete'])
        keyboard.return_value.close.assert_called_once()

    def test_keyboard_hid_opens_only_the_keyboard_endpoint(self):
        with patch.object(civswitch, 'HIDEndpoint') as endpoint:
            keyboard = civswitch.KeyboardHID()
        endpoint.assert_called_once_with(
            '/dev/hidg1', civswitch.KBD_RELEASE)
        self.assertFalse(hasattr(keyboard, 'gamepad'))

    def test_keyboard_key_diagnostic_uses_only_keyboard_endpoint(self):
        with patch.object(civswitch, 'wait_for_usb'), \
                patch.object(civswitch, 'KeyboardHID') as keyboard, \
                patch.object(civswitch.time, 'sleep'), \
                patch.object(civswitch, 'record_event') as record:
            civswitch.run_keyboard_key_test('slow')
        keyboard.assert_called_once_with()
        keyboard.return_value.press_key.assert_called_once_with(
            civswitch.HID_KEY_A, hold_s=0.25)
        keyboard.return_value.send_gamepad.assert_not_called()
        self.assertEqual(
            [item.args[0] for item in record.call_args_list],
            ['keyboard-key-test-start', 'keyboard-key-test-complete'])
        keyboard.return_value.close.assert_called_once()

    def test_keyboard_key_repeat_is_bounded(self):
        with patch.object(civswitch, 'wait_for_usb'), \
                patch.object(civswitch, 'KeyboardHID') as keyboard, \
                patch.object(civswitch.time, 'sleep'), \
                patch.object(civswitch, 'record_event'):
            civswitch.run_keyboard_key_test('repeat')
        key_down = civswitch.pack_keyboard(keycode=civswitch.HID_KEY_A)
        self.assertEqual(
            keyboard.return_value.send_keyboard_repeated.call_args_list,
            [call(key_down, 5, 0.015),
             call(civswitch.KBD_RELEASE, 5, 0.015)])
        keyboard.return_value.send_gamepad.assert_not_called()

    def test_keyboard_key_prime_sends_bounded_neutral_reports_first(self):
        with patch.object(civswitch, 'wait_for_usb'), \
                patch.object(civswitch, 'KeyboardHID') as keyboard, \
                patch.object(civswitch.time, 'sleep') as sleep, \
                patch.object(civswitch, 'record_event'):
            civswitch.run_keyboard_key_test('prime')
        key_down = civswitch.pack_keyboard(keycode=civswitch.HID_KEY_A)
        self.assertEqual(
            keyboard.return_value.send_keyboard_repeated.call_args_list,
            [call(civswitch.KBD_RELEASE,
                  civswitch.KEYBOARD_PRIME_REPORT_COUNT,
                  civswitch.KEYBOARD_PRIME_INTERVAL_S),
             call(key_down, civswitch.KEYBOARD_TEST_REPEAT_COUNT,
                  civswitch.KEYBOARD_TEST_REPEAT_INTERVAL_S),
             call(civswitch.KBD_RELEASE,
                  civswitch.KEYBOARD_TEST_REPEAT_COUNT,
                  civswitch.KEYBOARD_TEST_REPEAT_INTERVAL_S)])
        self.assertEqual(
            sleep.call_args_list, [call(0.25), call(0.25), call(0.15)])
        keyboard.return_value.send_gamepad.assert_not_called()

    def test_keyboard_key_prime_rejects_unbounded_report_counts(self):
        self.assertGreater(civswitch.KEYBOARD_PRIME_REPORT_COUNT, 0)
        self.assertLessEqual(civswitch.KEYBOARD_PRIME_REPORT_COUNT, 128)
        self.assertGreaterEqual(civswitch.KEYBOARD_PRIME_INTERVAL_S, 0.01)


class ManualDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent

    def test_control_command_exposes_manual_operations_only(self):
        control = (self.root / 'civswitchctl').read_text()
        for operation in ('start)', 'start-line)', 'test)', 'test-warmup)',
                          'test-preamble)',
                          'keyboard)', 'keyboard-alpha)', 'keyboard-key)',
                          'chat-keyboard)', 'raw-line)',
                          'stop)', 'status)', 'restore)'):
            self.assertIn(operation, control)
        self.assertIn('A|B|X|Y|L|R|LR|ZL|ZR|L3|R3', control)
        self.assertIn('start_job "line:$2"', control)
        self.assertIn('start_job "controller-test:$button"', control)
        self.assertIn('start_job "controller-warmup-test:$button"', control)
        self.assertIn('start_job controller-preamble-test', control)
        self.assertIn('start_job keyboard-test', control)
        self.assertIn('start_job keyboard-alpha-test', control)
        self.assertIn('start_job "keyboard-key:$2"', control)
        self.assertIn('start_job chat-keyboard-test', control)
        self.assertIn('start_job "raw-line:$2"', control)
        self.assertNotIn('civswitchctl boot', control)

    def test_job_runner_cleans_up_hid_and_stock_usb_on_every_exit(self):
        runner = (self.root / 'run-on-boot.sh').read_text()
        self.assertIn('trap cleanup EXIT INT TERM', runner)
        self.assertIn('restore-default-gadget.sh --from-job', runner)
        self.assertIn('/run/civswitch-child.pid', runner)
        self.assertIn('CivSwitch: job cleanup started', runner)
        self.assertIn('line:*)', runner)
        self.assertIn('args=(--line "$line_number")', runner)
        self.assertIn('controller-test:*)', runner)
        self.assertIn('args=(--test-controller "$button")', runner)
        self.assertIn('controller-warmup-test:*)', runner)
        self.assertIn('args=(--test-controller-warmup "$button")', runner)
        self.assertIn('controller-preamble-test)', runner)
        self.assertIn('args=(--test-controller-preamble)', runner)
        self.assertIn('keyboard-test) args=(--test-keyboard)', runner)
        self.assertIn('keyboard-alpha-test) args=(--test-keyboard-alpha)', runner)
        self.assertIn('keyboard-key:*)', runner)
        self.assertIn('args=(--test-keyboard-key "$key_mode")', runner)
        self.assertIn('chat-keyboard-test) args=(--test-chat-keyboard)', runner)
        self.assertIn('slow|repeat|prime', runner)
        self.assertIn('raw-line:*)', runner)
        self.assertIn('args=(--raw-line "$line_number")', runner)
        self.assertLess(runner.index('trap cleanup'),
                        runner.index('usb-gadget-hid.sh'))
        control = (self.root / 'civswitchctl').read_text()
        self.assertIn('/run/civswitch-child.pid', control)
        self.assertIn('"$_civswitch_state" != Z', control)

    def test_installer_persists_static_network_and_manual_control(self):
        installer = (self.root / 'install.sh').read_text()
        static_ip = (self.root / 'set-static-ip.sh').read_text()
        restore = (self.root / 'restore-default-gadget.sh').read_text()
        self.assertIn('cp "$SCRIPT_DIR/civswitchctl"', installer)
        self.assertIn('cp "$SCRIPT_DIR/set-static-ip.sh"', installer)
        self.assertIn('ln -sf "$INSTALL_DIR/civswitchctl" /usr/bin/civswitchctl',
                      installer)
        self.assertIn('192.168.86.198', installer)
        self.assertIn('S99zz-ipstatic', static_ip)
        self.assertIn('192.168.77.1', static_ip)
        self.assertIn('ifconfig usb0', static_ip)
        self.assertIn('/etc/init.d/S99zz-ipstatic start', restore)

    def test_static_network_setup_tolerates_models_without_ethernet(self):
        static_ip = (self.root / 'set-static-ip.sh').read_text()
        self.assertIn('ETH_IF="${CIVSWITCH_ETH_IF:-eth0}"', static_ip)
        self.assertGreaterEqual(
            static_ip.count('if [ -e "/sys/class/net/$ETH_IF" ]; then'), 2)
        self.assertIn('Ethernet configuration skipped', static_ip)
        self.assertNotIn('\nifconfig eth0 "$IP" netmask "$NETMASK" up\n',
                         static_ip)

    def test_installer_normalizes_root_ssh_strict_modes(self):
        installer = (self.root / 'install.sh').read_text()
        self.assertIn('chown root:root /root', installer)
        self.assertIn('chmod 700 /root', installer)
        self.assertIn('chown root:root /root/.ssh/authorized_keys', installer)
        self.assertIn('chmod 600 /root/.ssh/authorized_keys', installer)

    def test_single_host_helper_and_focused_diagnostics_are_documented(self):
        helper = (self.root / 'luckfox-ssh-connect').read_text()
        control = (self.root / 'civswitchctl').read_text()
        guide = (self.root / 'FLASHING.md').read_text()
        self.assertIn('ProxyCommand ~/bin/luckfox-ssh-connect auto', guide)
        self.assertIn('keyboard                     type CivSwitch through /dev/hidg1 only',
                      control)
        self.assertIn('test-warmup)', control)
        self.assertIn('send uncounted R, then one gamepad button', control)
        self.assertIn('keyboard-only', guide)
        self.assertIn('adb_up && connect_adb', helper)
        self.assertNotIn('yunpengs-luckfox-eth', helper)

    def test_release_bundles_optional_swap_assets(self):
        workflow = (
            self.root.parent / '.github/workflows/build-release.yml'
        ).read_text()
        self.assertIn('make_sd_swap_assets.py', workflow)
        self.assertIn('CIVSWAP-assets.tar.gz', workflow)


if __name__ == '__main__':
    unittest.main()
