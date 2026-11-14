#!/bin/bash
# Build the UniXNU payload volume: a FAT image with the kernel, its ADT sidecar and
# the boot configuration, laid out exactly as docs/concepts/payload.md specifies.
#
#   build-esp.sh <kernel.macho> <kernel.macho.adt> <out.img> [size_mb]
#
# Layout
#   /EFI/UniXNU/unixnu.toml
#   /EFI/UniXNU/Payloads/kernel.macho
#   /EFI/UniXNU/Payloads/kernel.macho.adt
#   /EFI/UniXNU/Payloads/kernel.macho.trustcache   (only if TRUSTCACHE=... is set)
#
# The volume is deterministic: timestamps are pinned to 1980-01-01 and the files
# are copied in a fixed order, so the same inputs give the same image.
#
# Needs mtools (mformat, mmd, mcopy, mdir). On Debian/Ubuntu: apt install mtools.

set -euo pipefail

KERNEL=${1:?usage: build-esp.sh <kernel.macho> <kernel.macho.adt> <out.img> [size_mb]}
ADT=${2:?missing the ADT sidecar}
OUT=${3:?missing the output image path}
SIZE_MB=${4:-15}

for tool in mformat mmd mcopy mdir; do
  command -v "$tool" >/dev/null || { echo "build-esp: $tool not found (apt install mtools)" >&2; exit 1; }
done

[ -f "$KERNEL" ] || { echo "build-esp: no kernel at $KERNEL" >&2; exit 1; }
[ -f "$ADT" ]    || { echo "build-esp: no ADT at $ADT" >&2; exit 1; }

# A real Mach-O kernel is 64-bit little-endian: magic cffaedfe at offset 0.
# Anything else (a KPI symbol list, a truncated download) would boot to nothing.
magic=$(od -An -tx1 -N4 "$KERNEL" | tr -d ' \n')
if [ "$magic" != "cffaedfe" ]; then
  echo "build-esp: $KERNEL is not a 64-bit Mach-O (magic $magic, expected cffaedfe)" >&2
  exit 1
fi

kernel_bytes=$(wc -c < "$KERNEL" | tr -d ' ')
adt_bytes=$(wc -c < "$ADT" | tr -d ' ')
echo "build-esp: kernel $kernel_bytes bytes, ADT $adt_bytes bytes, volume ${SIZE_MB} MiB"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

cat > "$work/unixnu.toml" <<'TOML'
# Written by scripts/build-esp.sh for the Meizu 15 (M8810, SDM660).
version = 1

[boot]
timeout = 5
default = "xnu"

[kits]
autoload = false

[[entry]]
id = "xnu"
title = "iOS 16.7.16 (Meizu 15)"
type = "xnu"
path = "Payloads/kernel.macho"
args = "-v keepsyms=1 debug=0x8"
TOML

rm -f "$OUT"
# mformat -T counts 512-byte sectors (mformat's own unit, not the 2048-byte
# cluster size): 1 MiB = 2048 sectors. Getting this wrong silently halves the
# volume and the copy then fails with "Disk full".
# mformat -T counts 512-byte sectors, so 1 MiB is 2048 of them.
sectors=$(( SIZE_MB * 2048 ))
#
# 4 KiB clusters, not 1 KiB. The volume exists to carry a 15.06 MiB kernel and
# has to stay UNDER 16 MiB, because GenFds promotes the whole firmware volume to
# EFI_FIRMWARE_FILE_SYSTEM3 as soon as any one section reaches LARGE_FILE_SIZE
# (0x1000000). An FFS3 FV is not just a bigger version of an FFS1 one: every FFS
# header in it is parsed with EFI_FFS_FILE_HEADER2 and a 64-bit ExtendedSize.
# Measured on this port, that broke two things at once: Meizu15PayloadDxe walked
# the volume with GetNextFile, desynchronised at the 48 MiB payload file and
# reported "the payload volume is not in any Fv"; and SimpleInitMain faulted at
# +0x2088 with "Data abort: Translation fault, third level" even with the prober
# left unpatched.
#
# At 1 KiB clusters the FAT itself eats enough to make a 15 MiB volume too tight.
# 4 KiB clusters cut it to roughly 31 sectors per copy, which leaves 623,361
# bytes of slack in a 15.75 MiB volume.
mformat -i "$OUT" -C -T "$sectors" -h 64 -s 8 -v UNIXNU ::
# Fixed timestamps: mformat/mcopy stamp from the clock otherwise.
export MTOOLS_SKIP_CHECK=1
mmd -i "$OUT" ::/EFI
mmd -i "$OUT" ::/EFI/UniXNU
mmd -i "$OUT" ::/EFI/UniXNU/Payloads

mcopy -i "$OUT" -D o "$work/unixnu.toml"        ::/EFI/UniXNU/unixnu.toml
mcopy -i "$OUT" -D o "$KERNEL"                  ::/EFI/UniXNU/Payloads/kernel.macho
mcopy -i "$OUT" -D o "$ADT"                     ::/EFI/UniXNU/Payloads/kernel.macho.adt

if [ -n "${TRUSTCACHE:-}" ]; then
  [ -f "$TRUSTCACHE" ] || { echo "build-esp: no trust cache at $TRUSTCACHE" >&2; exit 1; }
  mcopy -i "$OUT" -D o "$TRUSTCACHE" ::/EFI/UniXNU/Payloads/kernel.macho.trustcache
fi

echo "=== volume contents ==="
mdir -i "$OUT" -/ :: | sed 's/^/  /'

echo "=== size ==="
ls -l "$OUT"
