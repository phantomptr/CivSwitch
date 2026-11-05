#!/bin/sh
# Enable the CivSwitch SD-card swap partition when present.
#
# The Luckfox Pico Pro exposes only about 55 MB to Linux with the vendor's
# 66 MB camera CMA reservation. The CivSwitch boot environment reduces CMA to
# 4K; this optional swap partition is an additional OOM safety net.
set -eu

if grep -q '^/dev/' /proc/swaps 2>/dev/null; then
    echo "CivSwitch swap: already active"
    grep '^/dev/' /proc/swaps
    exit 0
fi

candidate="${CIVSWITCH_SWAP_DEVICE:-}"
enabled=0
if [ -z "$candidate" ]; then
    for path in /dev/disk/by-label/CIVSWAP /dev/disk/by-partlabel/CIVSWAP \
            /dev/mmcblk1p1 /dev/mmcblk1p2 /dev/mmcblk1p3 /dev/mmcblk1p4 \
            /dev/mmcblk0p1 /dev/mmcblk0p2 /dev/mmcblk0p3 /dev/mmcblk0p4; do
        [ -b "$path" ] || continue
        # This Buildroot's blkid reports no type for a valid swap partition,
        # so empty output means "unknown", not "not swap". Only reject a
        # candidate when blkid positively identifies a different type.
        if command -v blkid >/dev/null 2>&1; then
            type=$(blkid -o value -s TYPE "$path" 2>/dev/null || true)
            if [ -n "$type" ] && [ "$type" != swap ]; then
                continue
            fi
        fi
        if swapon "$path" 2>/dev/null; then
            candidate="$path"
            enabled=1
            break
        fi
    done
fi

if [ -z "$candidate" ] || [ ! -b "$candidate" ]; then
    echo "CivSwitch swap: no SD swap partition found"
    exit 0
fi

if [ "$enabled" = 0 ]; then
    if ! swapon "$candidate" 2>/dev/null; then
        echo "CivSwitch swap: could not enable $candidate" >&2
        exit 0
    fi
fi

echo "CivSwitch swap: enabled $candidate"
grep '^/dev/' /proc/swaps
