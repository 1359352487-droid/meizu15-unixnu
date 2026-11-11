#!/usr/bin/env python3
"""Turn the extracted DXE manifest into an EDK2 package.

Reads dump/dxe_manifest.json (produced by extract-xbl-dxe.py) plus the .efi
files, and writes:

    edk2-firmware-build/Meizu15Pkg/
        Meizu15Pkg.dec
        Meizu15Pkg.dsc      [Components]
        Meizu15Pkg.fdf      [FV]
        Drivers/<Name>/<Name>.inf + <Name>.efi

Every FILE_GUID comes from the manifest, i.e. from the module's own FFS header.
Nothing is invented.

Usage:
    python3 scripts/make-pkg.py
    python3 scripts/make-pkg.py --manifest dump/dxe_manifest.json --force
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import sys
import uuid

# manifest carries either the short (DRIVER/APPDXE) or the long
# (DXE_DRIVER/UEFI_APPLICATION) spelling depending on the extractor version.
INF_MODULE_TYPE = {
    "DRIVER": "DXE_DRIVER",
    "DXE_DRIVER": "DXE_DRIVER",
    "DXE_CORE": "DXE_CORE",
    "APPDXE": "UEFI_APPLICATION",
    "UEFI_APPLICATION": "UEFI_APPLICATION",
}

# FFS file type used when embedding the PE32 straight through the FDF
FFS_TYPE = {
    "DXE_DRIVER": "DXE_DRIVER",
    "DXE_CORE": "DXE_CORE",
    "UEFI_APPLICATION": "APPLICATION",
}

INF_TEMPLATE = """## @file
#  {base} - prebuilt UEFI module carved from the phone's own XBL.
#
#  Origin: xbl_a -> ELF PT_LOAD -> FFS2 volume -> gzip FV_IMAGE -> this module.
#  FILE_GUID is the GUID this module was published under in the stock volume.
#
[Defines]
  INF_VERSION                    = 0x00010005
  BASE_NAME                      = {base}
  FILE_GUID                      = {guid}
  MODULE_TYPE                    = {modtype}
  VERSION_STRING                 = 1.0

[Binaries]
  PE32|{base}.efi|DEBUG
"""

DEC_TEMPLATE = """## @file
#  {name} - UEFI firmware package for this phone.
#  Assembled from prebuilt DXE modules carved out of the stock XBL,
#  on top of an open EDK2 core.
#
[Defines]
  DEC_SPECIFICATION              = 0x00010005
  PACKAGE_NAME                   = {name}
  PACKAGE_GUID                   = {guid}
  PACKAGE_VERSION                = 0.1
"""

DSC_TEMPLATE = """## @file
#  {name} platform description.
#
#  Every component is a binary-only INF. There is no driver source here: these
#  are the phone's own stock modules and must not be recompiled.
#
[Defines]
  PLATFORM_NAME                  = {name}
  PLATFORM_GUID                  = {guid}
  PLATFORM_VERSION               = 0.1
  DSC_SPECIFICATION              = 0x00010005
  OUTPUT_DIRECTORY               = Build/{name}
  SUPPORTED_ARCHITECTURES        = AARCH64
  BUILD_TARGETS                  = DEBUG|RELEASE
  SKUID_IDENTIFIER               = DEFAULT
  FLASH_DEFINITION               = {name}/{name}.fdf

[LibraryClasses]

[Components]
{components}
"""

FDF_TEMPLATE = """## @file
#  {name} firmware volume description.
#
#  Size is {size_mib} MiB. The stock inner volume was only 1.75 MiB because its
#  nested FV_IMAGE was gzip-compressed; here the modules ship uncompressed into
#  a boot partition that has 64 MiB of room.
#
[Defines]
  DEFINE BLOCK_SIZE        = 0x00010000

[FD.{name_upper}_UEFI]
BaseAddress   = 0x8000
Size          = 0x{size:08X}
ErasePolarity = 1
BlockSize     = $(BLOCK_SIZE)
NumBlocks     = 0x{num_blocks:X}

0x00000000|0x{size:08X}
FV = {fv_name}

[FV.{fv_name}]
FvNameGuid         = {fv_guid}
BlockSize          = $(BLOCK_SIZE)
NumBlocks          = 0x{num_blocks:X}
FvAlignment        = 16
ERASE_POLARITY     = 1
MEMORY_MAPPED      = TRUE
STICKY_WRITE       = TRUE
LOCK_CAP           = TRUE
LOCK_STATUS        = TRUE
WRITE_DISABLED_CAP = TRUE
WRITE_ENABLED_CAP  = TRUE
WRITE_STATUS       = TRUE
WRITE_LOCK_CAP     = TRUE
WRITE_LOCK_STATUS  = TRUE
READ_DISABLED_CAP  = TRUE
READ_ENABLED_CAP   = TRUE
READ_STATUS        = TRUE
READ_LOCK_CAP      = TRUE
READ_LOCK_STATUS   = TRUE

{file_entries}
"""


def sanitize(name: str, fallback: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]", "_", name) if name else fallback
    s = re.sub(r"_+", "_", s).strip("_")
    if not s or s[0].isdigit():
        s = "Mod_" + s
    return s


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default="dump/dxe_manifest.json")
    ap.add_argument("--dxe-dir", default="dump/dxe")
    ap.add_argument("--pkg-root", default="edk2-firmware-build")
    ap.add_argument("--name", default="Meizu15Pkg")
    ap.add_argument("--size-mib", type=int, default=5)
    ap.add_argument("--force", action="store_true", help="overwrite an existing package")
    args = ap.parse_args()

    if not os.path.exists(args.manifest):
        print(f"missing {args.manifest}; run scripts/extract-xbl-dxe.py first", file=sys.stderr)
        return 2

    manifest = json.load(open(args.manifest, encoding="utf-8"))
    pkg = os.path.join(args.pkg_root, args.name)
    if os.path.isdir(pkg) and not args.force:
        print(f"{pkg} exists; pass --force to rebuild", file=sys.stderr)
        return 3

    mods = []
    used = {}
    for e in manifest:
        if not e.get("has_pe"):
            continue
        mt = INF_MODULE_TYPE.get(e.get("ffs_type_name", ""))
        if not mt:
            continue
        base = sanitize(e.get("name", ""), f"Module{e['idx']:02d}")
        if base in used:
            used[base] += 1
            base = f"{base}_{used[base]}"
        else:
            used[base] = 1
        mods.append(dict(e, base=base, modtype=mt))
    mods.sort(key=lambda m: m["base"])

    drv = os.path.join(pkg, "Drivers")
    os.makedirs(drv, exist_ok=True)

    copied = 0
    for m in mods:
        sub = os.path.join(drv, m["base"])
        os.makedirs(sub, exist_ok=True)
        cand = glob.glob(os.path.join(args.dxe_dir, f"{m['idx']:02d}_*.efi"))
        if not cand:
            print(f"  missing .efi for {m['base']} (idx {m['idx']})", file=sys.stderr)
            continue
        shutil.copyfile(cand[0], os.path.join(sub, f"{m['base']}.efi"))
        with open(os.path.join(sub, f"{m['base']}.inf"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(INF_TEMPLATE.format(base=m["base"], guid=m["guid"], modtype=m["modtype"]))
        copied += 1

    dec_guid = uuid.uuid5(uuid.NAMESPACE_DNS, f"{args.name}.dec")
    plat_guid = uuid.uuid5(uuid.NAMESPACE_DNS, f"{args.name}.dsc")
    fv_guid = uuid.uuid5(uuid.NAMESPACE_DNS, f"{args.name}.fv")

    with open(os.path.join(pkg, f"{args.name}.dec"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(DEC_TEMPLATE.format(name=args.name, guid=dec_guid))

    # [Components] stays empty on purpose. Every module is a prebuilt PE32 that is
    # embedded through the FDF; declaring binary-only INFs here makes EDK2 try to
    # apply a build rule to them, which fails for DXE_CORE:
    #   error F003: Don't Find common rule RULE.COMMON.DXE_CORE
    comps = "  # (intentionally empty: all modules are embedded via the FDF)"
    with open(os.path.join(pkg, f"{args.name}.dsc"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(DSC_TEMPLATE.format(name=args.name, guid=plat_guid, components=comps))

    entries = []
    for m in mods:
        ftype = FFS_TYPE.get(m["modtype"], "FREEFORM")
        path = f"{args.name}/Drivers/{m['base']}/{m['base']}.efi"
        entries.append(
            f"FILE {ftype} = {m['guid']} {{\n"
            f"  SECTION PE32 = {path}\n"
            f"}}\n")
    infs = "\n".join(entries)
    size = args.size_mib * 1024 * 1024
    num_blocks = size // 0x10000
    with open(os.path.join(pkg, f"{args.name}.fdf"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(FDF_TEMPLATE.format(
            name=args.name, name_upper=args.name.upper(), size=size, size_mib=args.size_mib,
            num_blocks=num_blocks, fv_name=args.name.upper() + "_FV",
            fv_guid=fv_guid, file_entries=infs))

    print(f"{args.name}: {copied} modules, {size_mib if False else args.size_mib} MiB volume")
    print(f"  {pkg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
