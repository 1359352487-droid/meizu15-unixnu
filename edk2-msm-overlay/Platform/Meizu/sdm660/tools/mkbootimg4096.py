#!/usr/bin/env python3
"""Minimal Android boot image writer with an explicit page size.

Why this exists: edk2-msm's sdm660 hook shells out to `abootimg`, which hardcodes
a 2048-byte page size and exposes no flag to change it (its help lists only
-c/-f/-k/-r/-s). The Meizu 15 bootloader reads page_size out of the header and
derives every segment offset from it, and the stock boot.img on this device says
4096. A 2048 image therefore misplaces kernel/ramdisk/DTB and will not boot.

Header layout written here is AOSP boot image v0:
    8s  magic "ANDROID!"
    I   kernel_size
    I   kernel_addr
    I   ramdisk_size
    I   ramdisk_addr
    I   second_size
    I   second_addr
    I   tags_addr
    I   page_size
    I   header_version  (v0 keeps this word as 0)
    I   os_version
Everything after the header is page aligned.

Usage:
    mkbootimg4096.py <out.img> <kernel> <ramdisk> [page_size] [kernel_addr]
                     [ramdisk_addr] [tags_addr]

An empty (0 or 1 byte) ramdisk is written as a zero-length segment, matching the
stock layout where ramdisk_size accounts only for real payload.
"""

import os
import struct
import sys


def main(argv):
    if len(argv) < 4:
        sys.stderr.write(__doc__)
        return 2

    out_path = argv[1]
    kernel_path = argv[2]
    ramdisk_path = argv[3]
    page_size = int(argv[4], 0) if len(argv) > 4 else 4096
    kernel_addr = int(argv[5], 0) if len(argv) > 5 else 0
    ramdisk_addr = int(argv[6], 0) if len(argv) > 6 else 0
    tags_addr = int(argv[7], 0) if len(argv) > 7 else 0

    if page_size < 512 or page_size & (page_size - 1):
        sys.stderr.write("page_size must be a power of two >= 512\n")
        return 2

    with open(kernel_path, "rb") as fh:
        kernel = fh.read()
    if not kernel:
        sys.stderr.write("kernel is empty: %s\n" % kernel_path)
        return 1

    ramdisk = b""
    if os.path.exists(ramdisk_path):
        with open(ramdisk_path, "rb") as fh:
            data = fh.read()
        # build.sh seeds the ramdisk with a single newline to keep the file
        # existing; treat that as "no ramdisk" instead of shipping a 1-byte one.
        if len(data) > 1:
            ramdisk = data

    header = struct.pack(
        "<8s10I",
        b"ANDROID!",
        len(kernel),
        kernel_addr,
        len(ramdisk),
        ramdisk_addr,
        0,            # second_size
        0,            # second_addr
        tags_addr,
        page_size,
        0,            # header_version
        0,            # os_version
    )

    def padding(length):
        return b"\x00" * ((page_size - length % page_size) % page_size)

    with open(out_path, "wb") as fh:
        fh.write(header)
        fh.write(padding(len(header)))
        fh.write(kernel)
        fh.write(padding(len(kernel)))
        if ramdisk:
            fh.write(ramdisk)
            fh.write(padding(len(ramdisk)))

    sys.stderr.write(
        "wrote %s: kernel=%d ramdisk=%d page_size=%d total=%d\n"
        % (out_path, len(kernel), len(ramdisk), page_size, os.path.getsize(out_path))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
