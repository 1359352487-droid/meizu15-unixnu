#!/usr/bin/env python3
"""Prove what the finished boot image will hand to SimpleInit -- offline.

The payload volume lives inside a COMPRESSED firmware section, so `strings` and
mdir cannot see it in the shipped image. This walks the real artefact:

    boot.img  ->  gzip  ->  firmware volume  ->  FFS 3F1A9C22-…
              ->  COMPRESSED section  ->  48 MiB FAT  ->  directory tree

and then asserts the layout SimpleInit needs:

  /bootaa64.efi                     the launcher, at the volume root
  /startup.nsh                      runs it when the Shell is what the menu picks
  /EFI/BOOT/BOOTAA64.EFI            the same launcher under the standard name
  /EFI/UniXNU/unixnu.toml           boot config
  /EFI/UniXNU/Payloads/kernel.macho the 15.8 MB arm64 kernel
  /EFI/UniXNU/Payloads/kernel.macho.adt

Why at the root: prober.c's shadowed loop variable means only dir[0] -- "/" --
is ever probed, so a launcher under /EFI/BOOT is invisible to the menu.

Usage:
    verify-payload-image.py <boot.img> [--uefiextract /path/to/UEFIExtract]
                                        [--keep DIR]

UEFIExtract (LongSoft/UEFITool releases, ~1 MB) does the COMPRESSED-section
decompression; everything else is this repo's own code.
"""
import argparse
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fat_ls import Fat  # noqa: E402  (same directory)

PAYLOAD_SECTION_SIZE = 50331648          # 48 MiB FAT
ANDROID_MAGIC = b"ANDROID!"

REQUIRED = {
    "/bootaa64.efi": 20480,
    "/startup.nsh": None,                # size varies with the hint line
    "/efi/boot/bootaa64.efi": 20480,
    "/efi/unixnu/unixnu.toml": None,
    "/efi/unixnu/payloads/kernel.macho": None,
    "/efi/unixnu/payloads/kernel.macho.adt": 13996,
}


def unpack_fv(boot, out_fv):
    data = open(boot, "rb").read()
    if data[:8] != ANDROID_MAGIC:
        raise SystemExit("not an Android boot image: %r" % data[:8])
    kernel_size = struct.unpack_from("<I", data, 8)[0]
    page = struct.unpack_from("<I", data, 36)[0] or 2048

    raw = data[page:page + kernel_size]
    out, rest, members = bytearray(), raw, 0
    while rest[:2] == b"\x1f\x8b":
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        out += d.decompress(rest)
        out += d.flush()
        members += 1
        rest = d.unused_data
        if not d.eof:
            break
    payload = bytes(out) if members else raw

    fvh = payload.find(b"_FVH")
    if fvh < 0:
        raise SystemExit("no _FVH: the kernel segment is not a firmware volume")
    fv_off = fvh - 0x28
    fv_len = struct.unpack_from("<Q", payload, fv_off + 0x20)[0]
    open(out_fv, "wb").write(payload[fv_off:fv_off + fv_len])
    return len(data), members, fv_len


def find_uefiextract(explicit):
    if explicit:
        return explicit
    for name in ("UEFIExtract", "UEFIExtract.exe"):
        for base in (os.getcwd(), os.path.join(os.getcwd(), "tools")):
            p = os.path.join(base, name)
            if os.path.isfile(p):
                return p
    found = shutil.which("UEFIExtract") or shutil.which("UEFIExtract.exe")
    if found:
        return found
    raise SystemExit(
        "UEFIExtract not found. Download the release for this platform:\n"
        "  https://github.com/LongSoft/UEFITool/releases -> UEFIExtract_NE_*_<platform>.zip\n"
        "and pass --uefiextract /path/to/UEFIExtract")


def extract(uefiextract, fv_path, workdir):
    """Run UEFIExtract and return the dumped FAT image path."""
    res = subprocess.run([uefiextract, fv_path], cwd=workdir,
                         capture_output=True, text=True)
    if res.returncode not in (0, 1):     # UEFIExtract returns 1 for "no VTF"
        raise SystemExit("UEFIExtract failed (%d): %s" % (res.returncode, res.stderr.strip()))

    for root, _dirs, files in os.walk(workdir):
        for f in files:
            if f == "body.bin":
                p = os.path.join(root, f)
                if os.path.getsize(p) == PAYLOAD_SECTION_SIZE and "3F1A9C22" in p.upper():
                    return p
    raise SystemExit("the %d-byte payload section was not found in the dump"
                     % PAYLOAD_SECTION_SIZE)


def tree(fs):
    out = {}

    def walk(entries, prefix, depth):
        for e in entries:
            if e["attr"] & 0x08:
                continue
            name = e["long"] or (e["name"] + ("." + e["ext"] if e["ext"] else ""))
            path = prefix + "/" + name
            out[path.lower()] = (e["size"], e["cluster"], bool(e["attr"] & 0x10))
            if e["attr"] & 0x10 and depth > 0:
                walk(fs.subdir(e["cluster"]), path, depth - 1)

    walk(fs.root_dir(), "", 6)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--uefiextract")
    ap.add_argument("--keep")
    args = ap.parse_args()

    work = args.keep or tempfile.mkdtemp(prefix="payload-verify-")
    os.makedirs(work, exist_ok=True)
    fv_path = os.path.join(work, "boot.fv")

    total, members, fv_len = unpack_fv(args.image, fv_path)
    print(f"image      : {total:,} bytes, {members} gzip member(s) -> FV {fv_len:,} bytes")

    ue = find_uefiextract(args.uefiextract)
    print(f"UEFIExtract: {ue}")
    fat_path = extract(ue, fv_path, work)
    fat_bytes = open(fat_path, "rb").read()
    print(f"payload FAT: {len(fat_bytes):,} bytes  sha256 {hashlib.sha256(fat_bytes).hexdigest()[:16]}…")

    fs = Fat(fat_bytes)
    entries = tree(fs)
    print(f"volume     : {'FAT32' if fs.fat32 else 'FAT16'}, "
          f"{fs.bps} B/sector, {fs.spc} sectors/cluster, {len(entries)} entries")
    print()
    for path in sorted(entries):
        size, cluster, is_dir = entries[path]
        print(f"  {'DIR ' if is_dir else 'FILE'}  {size:>10,}  cluster={cluster:<6} {path}")

    problems = []
    for want, want_size in REQUIRED.items():
        if want not in entries:
            problems.append(f"missing: {want}")
            continue
        size, _cluster, is_dir = entries[want]
        if want_size is not None and size != want_size:
            problems.append(f"wrong size: {want} is {size:,}, expected {want_size:,}")
        if is_dir:
            problems.append(f"expected a file, found a directory: {want}")

    # The launcher must be present twice and identical: the root copy is what
    # SimpleInit probes, the EFI/BOOT copy is what other firmware looks for.
    if "/bootaa64.efi" in entries and "/efi/boot/bootaa64.efi" in entries:
        a = fat_bytes[fs.cluster_offset(entries["/bootaa64.efi"][1]):
                      fs.cluster_offset(entries["/bootaa64.efi"][1]) + 20480]
        b = fat_bytes[fs.cluster_offset(entries["/efi/boot/bootaa64.efi"][1]):
                      fs.cluster_offset(entries["/efi/boot/bootaa64.efi"][1]) + 20480]
        if a != b:
            problems.append("/bootaa64.efi and /EFI/BOOT/BOOTAA64.EFI differ")
        if a[:2] != b"MZ":
            problems.append("/bootaa64.efi is not a PE image (no MZ header)")
        print(f"\nlauncher   : MZ header OK, sha256 {hashlib.sha256(a).hexdigest()[:16]}…")

    # startup.nsh has to actually run the launcher: with the menu's countdown
    # picking the UEFI Shell, it is the only path that reaches DarwinBoot.
    if "/startup.nsh" in entries:
        size, cluster, _ = entries["/startup.nsh"]
        nsh = fat_bytes[fs.cluster_offset(cluster):fs.cluster_offset(cluster) + size]
        text = nsh.decode("latin-1").strip()
        print(f"startup.nsh: {text!r}")
        if "bootaa64.efi" not in text.lower():
            problems.append("/startup.nsh does not run /bootaa64.efi")

    print()
    if problems:
        for p in problems:
            print("FAIL:", p)
        return 1
    print("RESULT: the payload volume carries the launcher at the root, the kernel, "
          "the ADT and a startup.nsh that starts it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
