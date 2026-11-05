#!/usr/bin/env python3
"""Build the optional CIVSWAP SD-card image and DOS partition table.

The generated 1 GiB image contains only a Linux swap signature. It is safe to
keep as a file; it does not select or write a physical disk.
"""

import argparse
import hashlib
from pathlib import Path
import shutil
import struct
import subprocess


SECTOR_SIZE = 512
PARTITION_START_LBA = 2048
SWAP_MIB = 1024
SWAP_UUID = "e233ce15-ae64-45fe-bde5-53deef4bb097"
SWAP_LABEL = "CIVSWAP"
MBR_NAME = "mbr.bin"
SWAP_NAME = "swap.img"
CHECKSUM_NAME = "SHA256SUMS"


def build_mbr(partition_sectors):
    """Return the tested one-partition DOS MBR for a Linux-swap partition."""
    if partition_sectors <= 0 or partition_sectors > 0xFFFFFFFF:
        raise ValueError("partition_sectors must fit in a DOS MBR entry")

    mbr = bytearray(SECTOR_SIZE)
    entry = bytearray(16)
    entry[0] = 0x00                         # inactive
    entry[1:4] = b"\xfe\xff\xff"            # CHS start beyond 8 GiB
    entry[4] = 0x82                         # Linux swap
    entry[5:8] = b"\xfe\xff\xff"            # CHS end beyond 8 GiB
    entry[8:12] = struct.pack("<I", PARTITION_START_LBA)
    entry[12:16] = struct.pack("<I", partition_sectors)
    mbr[446:462] = entry
    mbr[510:512] = b"\x55\xaa"
    return bytes(mbr)


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_mkswap():
    for candidate in (
            shutil.which("mkswap"),
            "/opt/homebrew/opt/util-linux/sbin/mkswap",
            "/opt/homebrew/opt/util-linux/bin/mkswap",
            "/usr/local/opt/util-linux/sbin/mkswap",
            "/usr/local/opt/util-linux/bin/mkswap",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def create_swap_image(path, size_bytes=SWAP_MIB * 1024 * 1024):
    with path.open("wb") as image:
        image.truncate(size_bytes)
    path.chmod(0o600)

    mkswap = find_mkswap()
    if mkswap:
        subprocess.run(
            [mkswap, "-L", SWAP_LABEL, "-U", SWAP_UUID, str(path)],
            check=True)
        return

    docker = shutil.which("docker")
    if not docker:
        path.unlink()
        raise RuntimeError(
            "mkswap was not found. Install util-linux or Docker, then rerun.")

    # The digest is not user input. The output directory is the only host path
    # exposed to the container.
    subprocess.run(
        [docker, "run", "--rm",
         "-v", f"{path.parent.resolve()}:/out",
         "--entrypoint", "mkswap",
         "debian:bookworm-slim",
         "-L", SWAP_LABEL, "-U", SWAP_UUID, f"/out/{path.name}"],
        check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("sd-swap-assets"),
        help="destination directory (default: ./sd-swap-assets)")
    parser.add_argument(
        "--size-mib", type=int, default=SWAP_MIB,
        help=f"swap image size in MiB (default: {SWAP_MIB})")
    parser.add_argument(
        "--force", action="store_true", help="replace existing files")
    args = parser.parse_args()

    if args.size_mib <= 0:
        parser.error("--size-mib must be positive")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    mbr_path = args.output_dir / MBR_NAME
    swap_path = args.output_dir / SWAP_NAME

    for path in (mbr_path, swap_path):
        if path.exists() and not args.force:
            parser.error(f"output already exists: {path} (use --force)")

    size_bytes = args.size_mib * 1024 * 1024
    if size_bytes % SECTOR_SIZE:
        parser.error("swap size must be a multiple of 512 bytes")
    partition_sectors = size_bytes // SECTOR_SIZE
    mbr_path.write_bytes(build_mbr(partition_sectors))

    try:
        create_swap_image(swap_path, size_bytes)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.error(str(error))

    checksums = {
        MBR_NAME: sha256_file(mbr_path),
        SWAP_NAME: sha256_file(swap_path),
    }
    (args.output_dir / CHECKSUM_NAME).write_text(
        "".join(f"{digest}  {name}\n"
                for name, digest in checksums.items()),
        encoding="ascii")
    print(f"Created optional swap assets in {args.output_dir}")
    for name, digest in checksums.items():
        print(f"  {digest}  {name}")
    print("Writing these files to a block device is a separate, destructive step.")


if __name__ == "__main__":
    main()
