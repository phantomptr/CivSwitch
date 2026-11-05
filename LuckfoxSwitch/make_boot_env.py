#!/usr/bin/env python3
"""Create a Luckfox boot-environment image with a smaller camera CMA.

The Rockchip environment sector starts with a little-endian CRC32 over the
remaining bytes. This tool validates that CRC, changes the existing
``rk_dma_heap_cma`` value in place, and writes the corrected checksum.
"""

import argparse
import binascii
from pathlib import Path
import struct


DEFAULT_FROM_CMA = "66M"
DEFAULT_TO_CMA = "4K"


class BootEnvironmentError(ValueError):
    pass


def _crc32(data):
    return binascii.crc32(data) & 0xFFFFFFFF


def prepare_environment(source, from_cma=DEFAULT_FROM_CMA,
                        to_cma=DEFAULT_TO_CMA):
    """Return a CRC-correct environment with one CMA argument replaced."""
    if len(source) < 8:
        raise BootEnvironmentError("environment image is too small")

    stored_crc = struct.unpack("<I", source[:4])[0]
    actual_crc = _crc32(source[4:])
    if stored_crc != actual_crc:
        raise BootEnvironmentError(
            f"source CRC mismatch: stored 0x{stored_crc:08x}, "
            f"calculated 0x{actual_crc:08x}")

    old_argument = f"rk_dma_heap_cma={from_cma}".encode("ascii")
    new_argument = f"rk_dma_heap_cma={to_cma}".encode("ascii")
    count = source.count(old_argument)
    if count != 1:
        raise BootEnvironmentError(
            f"expected exactly one {old_argument.decode()}, found {count}")

    body = source[4:].replace(old_argument, new_argument, 1)
    if len(body) > len(source) - 4:
        raise BootEnvironmentError(
            "replacement CMA value makes the environment image larger")
    # The environment occupies a fixed-size flash sector. Shrinking the
    # command line shifts the packed strings, so preserve the image length and
    # zero-pad only at the end, matching the tested prepared image.
    body = body.ljust(len(source) - 4, b"\0")
    prepared = struct.pack("<I", _crc32(body)) + body

    if struct.unpack("<I", prepared[:4])[0] != _crc32(prepared[4:]):
        raise AssertionError("generated environment CRC is invalid")
    return bytes(prepared)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="extracted source env.img")
    parser.add_argument("output", type=Path, help="prepared environment image")
    parser.add_argument(
        "--from-cma", default=DEFAULT_FROM_CMA,
        help=f"existing CMA value to replace (default: {DEFAULT_FROM_CMA})")
    parser.add_argument(
        "--to-cma", default=DEFAULT_TO_CMA,
        help=f"replacement CMA value (default: {DEFAULT_TO_CMA})")
    parser.add_argument(
        "--force", action="store_true", help="replace an existing output file")
    args = parser.parse_args()

    if args.output.exists() and not args.force:
        parser.error(f"output already exists: {args.output} (use --force)")

    try:
        prepared = prepare_environment(
            args.source.read_bytes(), args.from_cma, args.to_cma)
    except (BootEnvironmentError, OSError) as error:
        parser.error(str(error))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(prepared)
    digest = __import__("hashlib").sha256(prepared).hexdigest()
    print(f"Prepared {args.output}")
    print(f"  rk_dma_heap_cma: {args.from_cma} -> {args.to_cma}")
    print(f"  size: {len(prepared)} bytes")
    print(f"  sha256: {digest}")


if __name__ == "__main__":
    main()
