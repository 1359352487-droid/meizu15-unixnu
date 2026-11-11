#!/usr/bin/env python3
"""Carve the DXE drivers out of this phone's own xbl_a partition.

The firmware ships as a 64-bit AArch64 ELF with no section headers. One of its
LOAD segments is a UEFI firmware volume; inside that volume is a gzip-compressed
nested FV_IMAGE that holds the actual DXE drivers. This script walks that whole
chain and writes the drivers out.

Usage:
    python3 scripts/extract-xbl-dxe.py dump/xbl_a.img \
        --out-dir dump/dxe \
        --manifest dump/dxe_manifest.json

Requires: uefi_firmware  (pip install uefi_firmware)

Nothing is executed and no device is touched; this only reads a file you already
dumped from your own phone.
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import struct
import sys
import zlib

FV_SIG = b"_FVH"
FV_HDR_SIG_OFFSET = 40          # signature sits 40 bytes into EFI_FIRMWARE_VOLUME_HEADER
FFS2_GUID = bytes.fromhex("78e58c8c3d8a1c4f9935896185c32dd3")

FFS_TYPES = {
    0x00: "RAW", 0x01: "FREEFORM", 0x02: "SEC", 0x03: "PEI_CORE", 0x04: "PEIM",
    0x05: "DXE_CORE", 0x06: "PEIM_DXE", 0x07: "DRIVER", 0x08: "COMBINED_PEIM",
    0x09: "APPDXE", 0x0A: "SMM", 0x0B: "FV_IMAGE", 0x0C: "COMBINED_SMM",
    0x0D: "SMM_CORE", 0x0F: "PAD",
}
# FFS type -> the MODULE_TYPE an EDK2 binary-only INF must declare
INF_MODULE_TYPE = {
    "DRIVER": "DXE_DRIVER",
    "DXE_CORE": "DXE_CORE",
    "APPDXE": "UEFI_APPLICATION",
}


def guid_str(raw: bytes) -> str:
    d1, d2, d3 = struct.unpack_from("<IHH", raw, 0)
    return f"{d1:08x}-{d2:04x}-{d3:04x}-{raw[8:10].hex()}-{raw[10:16].hex()}"


def elf_loads(data: bytes):
    """Yield (offset, filesz, vaddr) for every PT_LOAD of a 64-bit LE ELF."""
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise SystemExit("not a 64-bit little-endian ELF")
    e_phoff = struct.unpack_from("<Q", data, 32)[0]
    e_phentsize = struct.unpack_from("<H", data, 54)[0]
    e_phnum = struct.unpack_from("<H", data, 56)[0]
    for i in range(e_phnum):
        o = e_phoff + i * e_phentsize
        p_type = struct.unpack_from("<I", data, o)[0]
        p_offset, p_vaddr, _, p_filesz = struct.unpack_from("<QQQQ", data, o + 8)
        if p_type == 1 and p_filesz:
            yield p_offset, p_filesz, p_vaddr


def find_fv(buf: bytes):
    """Return (fv_offset, fv_length, header_length) of the first plausible FV."""
    pos = 0
    while True:
        i = buf.find(FV_SIG, pos)
        if i < 0:
            return None
        fv = i - FV_HDR_SIG_OFFSET
        if fv >= 0 and fv + 56 <= len(buf):
            fvlen = struct.unpack_from("<Q", buf, fv + 32)[0]
            hdrlen = struct.unpack_from("<H", buf, fv + 48)[0]
            if 56 <= hdrlen <= 0x1000 and 24 < fvlen <= len(buf) - fv + 0x1000:
                return fv, fvlen, hdrlen
        pos = i + 4


def walk_ffs(buf, fv_off, fv_len, hdr_len):
    """Yield (offset, type_id, size) for every FFS file in the volume."""
    end = min(fv_off + fv_len, len(buf))
    off = fv_off + hdr_len
    while off + 24 <= end:
        if buf[off:off + 16] in (b"\xff" * 16, b"\x00" * 16):
            # erased free space: step forward, do not treat it as end-of-volume
            step = off
            while step + 24 <= end and buf[step:step + 16] in (b"\xff" * 16, b"\x00" * 16):
                step += 8
            if step + 24 > end:
                return
            off = step
            continue
        size = buf[off + 20] | (buf[off + 21] << 8) | (buf[off + 22] << 16)
        ftype = buf[off + 18]
        if size < 24 or off + size > end:
            off += 8
            continue
        yield off, ftype, size
        off += (size + 7) // 8 * 8


def inflate_guided_section(payload: bytes) -> bytes:
    """The nested FV_IMAGE is a 24-byte GUIDed-section wrapper around a gzip stream."""
    if len(payload) > 24 and payload[24:26] == b"\x1f\x8b":
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        out = d.decompress(payload[24:]) + d.flush()
        return out
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xbl", help="path to a dumped xbl_a image")
    ap.add_argument("--out-dir", default="dump/dxe", help="where to write the .efi files")
    ap.add_argument("--manifest", default="dump/dxe_manifest.json")
    args = ap.parse_args()

    try:
        from uefi_firmware import AutoParser
    except ImportError:
        print("need the uefi_firmware package: pip install uefi_firmware", file=sys.stderr)
        return 2

    data = open(args.xbl, "rb").read()
    print(f"xbl image: {len(data)} bytes")

    # 1. find the firmware volume among the ELF LOAD segments
    inner = None
    for off, filesz, vaddr in elf_loads(data):
        seg = data[off:off + filesz]
        hit = find_fv(seg)
        if not hit:
            continue
        fv, fvlen, hdrlen = hit
        print(f"  LOAD seg @0x{off:x} vaddr=0x{vaddr:x}: FV at +0x{fv:x}, "
              f"len=0x{fvlen:x}, hdrlen={hdrlen}")
        # 2. find the compressed nested volume
        for f_off, ftype, fsize in walk_ffs(seg, fv, fvlen, hdrlen):
            if ftype == 0x0B:                     # FV_IMAGE
                payload = seg[f_off + 24:f_off + fsize]
                inner = inflate_guided_section(payload)
                print(f"    FV_IMAGE @+0x{f_off:x} size={fsize} -> "
                      f"inflated {len(inner)} bytes")
                break
        if inner:
            break
    if inner is None:
        print("no compressed FV_IMAGE found", file=sys.stderr)
        return 3

    # 3. parse the decompressed volume
    #    the inflated buffer carries an 8-byte preamble before the FV header
    for skip in (0, 8):
        try:
            fvobj = AutoParser(inner[skip:]).parse()
        except Exception:
            fvobj = None
        if fvobj is not None and getattr(fvobj, "valid_header", False):
            print(f"  parsed volume at skip={skip}, size={fvobj.size}")
            break
    else:
        print("could not parse the inflated volume", file=sys.stderr)
        return 4

    files = fvobj.firmware_filesystems[0].files
    os.makedirs(args.out_dir, exist_ok=True)
    manifest = []
    written = 0
    for idx, f in enumerate(files):
        t = getattr(f, "type", None)
        tid = t[0] if isinstance(t, tuple) else t
        if tid == 0x0F:                            # PAD
            continue
        name = ""
        pe = None
        for o in getattr(f, "objects", []) or []:
            st = getattr(o, "type", None)
            stid = st[0] if isinstance(st, tuple) else st
            if stid == 0x15:                       # UI section: UTF-16 name
                try:
                    s = (getattr(o, "data", b"") or b"").decode("utf-16-le").rstrip("\x00")
                    if s.isprintable():
                        name = s
                except UnicodeDecodeError:
                    pass
            if stid in (0x10, 0x12, 0x14):         # PE32 / PIC / TE
                pe = getattr(o, "data", None)

        entry = {
            "idx": idx,
            "ffs_type": tid,
            "ffs_type_name": FFS_TYPES.get(tid, str(tid)),
            "guid": guid_str(f.guid),
            "size": getattr(f, "size", 0),
            "name": name,
            "has_pe": pe is not None,
            "pe_size": len(pe) if pe else 0,
        }
        manifest.append(entry)

        if pe:
            fn = f"{idx:02d}_{FFS_TYPES.get(tid, tid)}_{entry['guid'][:8]}"
            if name:
                fn += "_" + name.replace(" ", "_")[:40]
            with open(os.path.join(args.out_dir, fn + ".efi"), "wb") as fh:
                fh.write(pe)
            written += 1

    os.makedirs(os.path.dirname(os.path.abspath(args.manifest)), exist_ok=True)
    with open(args.manifest, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)

    print(f"\nwrote {written} PE images to {args.out_dir}")
    print(f"manifest: {args.manifest} ({len(manifest)} entries)")
    print("\nNext: generate the EDK2 wrappers with")
    print("  python3 scripts/make-pkg.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
