"""Tests for Luckfox reproducibility helpers."""

import hashlib
import importlib.util
from pathlib import Path
import struct
import unittest


ROOT = Path(__file__).resolve().parent


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


boot_env = load_script("civswitch_make_boot_env", "make_boot_env.py")
swap_assets = load_script(
    "civswitch_make_sd_swap_assets", "make_sd_swap_assets.py")


class BootEnvironmentTests(unittest.TestCase):
    def make_environment(self, arguments=b"root=ubi0:rootfs"):
        body = bytearray(arguments + b"\0" * 32)
        crc = boot_env._crc32(body)
        return struct.pack("<I", crc) + bytes(body)

    def test_replaces_one_cma_argument_and_updates_crc(self):
        source = self.make_environment(
            b"mtdparts=spi-nand0:256K(env) rk_dma_heap_cma=66M root=ubi0:rootfs")
        prepared = boot_env.prepare_environment(source)

        self.assertEqual(len(prepared), len(source))
        self.assertIn(b"rk_dma_heap_cma=4K", prepared)
        self.assertNotIn(b"rk_dma_heap_cma=66M", prepared)
        stored = struct.unpack("<I", prepared[:4])[0]
        self.assertEqual(stored, boot_env._crc32(prepared[4:]))

    def test_rejects_bad_source_crc(self):
        source = bytearray(self.make_environment(
            b"rk_dma_heap_cma=66M"))
        source[4] ^= 0xFF
        with self.assertRaisesRegex(
                boot_env.BootEnvironmentError, "source CRC mismatch"):
            boot_env.prepare_environment(bytes(source))

    def test_rejects_missing_or_duplicate_argument(self):
        missing = self.make_environment(b"root=ubi0:rootfs")
        duplicate = self.make_environment(
            b"rk_dma_heap_cma=66M rk_dma_heap_cma=66M")
        with self.assertRaisesRegex(
                boot_env.BootEnvironmentError, "found 0"):
            boot_env.prepare_environment(missing)
        with self.assertRaisesRegex(
                boot_env.BootEnvironmentError, "found 2"):
            boot_env.prepare_environment(duplicate)


class SwapAssetTests(unittest.TestCase):
    def test_mbr_matches_the_tested_layout(self):
        mbr = swap_assets.build_mbr(2097152)
        self.assertEqual(len(mbr), 512)
        self.assertEqual(hashlib.sha256(mbr).hexdigest(),
                         "6f76faaa6efd99aeb77c5fa893b1e12009bedff7c2ff0bbff"
                         "88301a81cf4e623")
        entry = mbr[446:462]
        self.assertEqual(entry[0], 0)
        self.assertEqual(entry[4], 0x82)
        self.assertEqual(struct.unpack("<I", entry[8:12])[0], 2048)
        self.assertEqual(struct.unpack("<I", entry[12:16])[0], 2097152)
        self.assertEqual(mbr[510:512], b"\x55\xaa")

    def test_rejects_an_unrepresentable_partition_size(self):
        with self.assertRaises(ValueError):
            swap_assets.build_mbr(0)


class SSHHelperTests(unittest.TestCase):
    def test_auto_transport_order_is_ethernet_usb_adb(self):
        helper = (ROOT / "luckfox-ssh-connect").read_text()
        auto = helper[helper.index("    auto)"):helper.index("    *)")]
        self.assertLess(auto.index("eth_up && connect_eth"),
                        auto.index("usb_up && connect_usb"))
        self.assertLess(auto.index("usb_up && connect_usb"),
                        auto.index("adb_up && connect_adb"))
        self.assertIn("ETHERNET=192.168.86.198", helper)
        self.assertIn("USB_BOARD=192.168.77.1", helper)


if __name__ == "__main__":
    unittest.main()
