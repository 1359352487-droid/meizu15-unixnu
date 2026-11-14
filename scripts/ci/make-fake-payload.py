#!/usr/bin/env python3
"""Build a throwaway payload so a firmware test does not cost 12 MB of fastboot.

Iterating on the boot *menu* (does prober see 2 items? does the launcher get
called?) does not need the real 15.8 MB kernel in the payload volume. Every byte
of it has to travel over USB 2.0 in `fastboot boot`, which is the slowest part of
the loop by far.

This writes:

    <out>/kernel.macho        a minimal but well-formed arm64 MH_EXECUTE header
    <out>/kernel.macho.adt    an ADT-sized blob of zeroes

`scripts/build-esp.sh` only insists on the 64-bit Mach-O magic, so the volume
packs fine. DarwinBoot will parse the header, find no load commands and no
segments, and return an error -- which is exactly the signal we want: the
launcher reached the loader and called it.

Usage:
    make-fake-payload.py <out-dir> [kernel-size] [adt-size]
"""
import os
import struct
import sys

MAGIC    = 0xFEEDFACF          # MH_MAGIC_64, little-endian on disk: cf fa ed fe
CPU_ARM64 = 0x0100000C
MH_EXECUTE = 0x2


def fake_macho(size):
    header = struct.pack(
        "<IiiIIIII",
        MAGIC,          # magic
        CPU_ARM64,      # cputype
        0,              # cpusubtype
        MH_EXECUTE,     # filetype
        0,              # ncmds  -- no load commands on purpose
        0,              # sizeofcmds
        0,              # flags
        0,              # reserved
    )
    assert len(header) == 32, len(header)
    if size < len(header):
        raise SystemExit("kernel-size must be >= 32")
    return header + b"\x00" * (size - len(header))


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    ksize = int(sys.argv[2]) if len(sys.argv) > 2 else 8192
    asize = int(sys.argv[3]) if len(sys.argv) > 3 else 13996

    os.makedirs(out, exist_ok=True)
    kpath = os.path.join(out, "kernel.macho")
    apath = os.path.join(out, "kernel.macho.adt")

    kernel = fake_macho(ksize)
    open(kpath, "wb").write(kernel)
    open(apath, "wb").write(b"\x00" * asize)

    print("fake payload written to", out)
    print("  %s  %d bytes  magic %s" % (kpath, len(kernel), kernel[:4].hex()))
    print("  %s  %d bytes" % (apath, asize))
    print("DarwinBoot will reject this kernel on purpose: no load commands.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
