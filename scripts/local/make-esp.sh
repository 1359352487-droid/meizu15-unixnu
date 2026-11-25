#!/bin/bash
# meizu15 local-mac: build the ESP payload volume without mtools.
#
# scripts/build-esp.sh uses mformat/mcopy from mtools, which macOS 12 (Tier 3)
# cannot install from Homebrew bottles and which needs autotools to build.
# hdiutil makes the same FAT volume: a raw image (layout NONE), formatted MS-DOS,
# under 16 MiB so GenFds keeps the FV at FFS1 (see build-esp.sh's long comment --
# a section at 0x1000000 promotes the whole volume to FFS3 and the payload DXE
# then fails to find it).
set -euo pipefail
KERNEL=${1:?usage: make-esp.sh <kernel.macho> <adt> <out.img> [size_kb]}
ADT=${2:?}
OUT=${3:?}
SIZEKB=${4:-16320}
TOML=$(dirname "$0")/unixnu.toml

magic=$(od -An -tx1 -N4 "$KERNEL" | tr -d ' \n')
case "$magic" in
  cffaedfe) ;;                     # plain 64-bit Mach-O
  5d000000) ;;                     # EFI LZMA (DarwinBoot unpacks it)
  *) echo "kernel is neither Mach-O nor LZMA (magic $magic)" >&2; exit 1 ;;
esac
[ -f "$TOML" ] || { echo "missing $TOML" >&2; exit 1; }

rm -f "$OUT" "$OUT.dmg"
# hdiutil insists on a .dmg name; -layout NONE still writes a raw image, which is what FFS wants.
# -c 8 = 4 KiB clusters, matching scripts/build-esp.sh (mformat -c 8). hdiutil's
# default (512 B sectors, 1 KiB clusters) produced a FAT16 volume with 16k clusters,
# which is one of the two differences from the CI image we are trying to rule out.
hdiutil create -size "${SIZEKB}k" -fs MS-DOS -fsargs "-c 8" -layout NONE -volname UNIXNU -ov "$OUT.dmg" >/dev/null
mv "$OUT.dmg" "$OUT"
MNT=$(mktemp -d)
hdiutil attach -nobrowse -mountpoint "$MNT" "$OUT" >/dev/null
trap 'hdiutil detach "$MNT" >/dev/null 2>&1 || true; rm -rf "$MNT"' EXIT
mkdir -p "$MNT/EFI/UniXNU/Payloads"
export COPYFILE_DISABLE=1
[ -n "${LAUNCHER:-}" ] && cp "$LAUNCHER" "$MNT/EFI/UniXNU/launcher.efi"
# BDS falls back to its own shell when nothing is bootable; the removable-media
# path is the one it always tries, so put the launcher there too.
[ -n "${LAUNCHER:-}" ] && mkdir -p "$MNT/EFI/BOOT" && cp "$LAUNCHER" "$MNT/EFI/BOOT/BOOTAA64.EFI"
[ -n "${LAUNCHER:-}" ] && printf '\EFI\UniXNU\launcher.efi\r\n' > "$MNT/startup.nsh"
[ -n "${INITRD:-}" ] && cp "$INITRD" "$MNT/EFI/UniXNU/Payloads/launchd.macho"
cp "$TOML"   "$MNT/EFI/UniXNU/unixnu.toml"
cp "$KERNEL" "$MNT/EFI/UniXNU/Payloads/kernel.macho"
cp "$ADT"    "$MNT/EFI/UniXNU/Payloads/kernel.macho.adt"
sync
find "$MNT" -name '._*' -delete 2>/dev/null || true
find "$MNT" -name '.DS_Store' -delete 2>/dev/null || true
du -sk "$MNT"
hdiutil detach "$MNT" >/dev/null
trap - EXIT
rm -rf "$MNT"
ls -l "$OUT"
# the volume must stay under 16 MiB or GenFds promotes the FV to FFS3
sz=$(wc -c < "$OUT")
[ "$sz" -lt 16777216 ] || { echo "ESP is $sz bytes, at or over 16 MiB" >&2; exit 1; }
echo "esp: $sz bytes"
