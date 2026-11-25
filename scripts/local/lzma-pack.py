#!/usr/bin/env python3
"""Pack a file the way DarwinBoot now expects a compressed kernel: EFI LZMA (props + size + stream)."""
import lzma, struct, sys, pathlib
src, dst = sys.argv[1], sys.argv[2]
data = pathlib.Path(src).read_bytes()
filters = [{"id": lzma.FILTER_LZMA1, "lc": 3, "lp": 0, "pb": 2, "dict_size": 1 << 24}]
stream = lzma.compress(data, format=lzma.FORMAT_RAW, filters=filters)
out = bytes([0x5D, 0x00, 0x00, 0x00, 0x01]) + struct.pack("<Q", len(data)) + stream
pathlib.Path(dst).write_bytes(out)
print(f"lzma-pack: {src} {len(data)} -> {dst} {len(out)} ({len(data)/len(out):.1f}x)")
