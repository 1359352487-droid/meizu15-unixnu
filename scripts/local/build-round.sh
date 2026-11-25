#!/bin/bash
# meizu15: one round of local build -- kernel, ESP, firmware, retargeted boot.img.
# usage: build-round.sh <output-name>   (e.g. probes2 -> out/boot-meizu15-payload-probes2.img)
set -e
cd "$(dirname "$0")/.."
NAME=${1:?usage: build-round.sh <output-name>}
H=src/hakios-xnu-kernel-main
B=$H/xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837
K=$B/mach.development.bcm2837

rm -f "$K" "$B/allsymbols"
(cd "$H" && ./build-local.sh > "build-$NAME.log" 2>&1) || true      # exits 2 at check_all_exports by design
[ -x "$K" ] || { echo "kernel missing after build" >&2; exit 1; }
echo "kernel $(wc -c < "$K") bytes"

INITRD=/tmp/m15root/launchd ./out/make-esp.sh "$K" src/meizu15-unixnu-main/inputs/meizu15-8plus.adt \
  src/edk2-msm/Platform/Meizu/sdm660/payload/unixnu-esp.img 16320 >/dev/null

cd src/edk2-msm
export PATH=/tmp/llvm/bin:/tmp/tools/bin:/tmp/acpica-src/generate/unix/bin:$PATH
export WORKSPACE=$PWD PACKAGES_PATH=$PWD/Common/edk2:$PWD/Common/edk2-platforms \
       EDK_TOOLS_PATH=$PWD/Common/edk2/BaseTools CONF_PATH=$PWD/Common/edk2/Conf
./build.sh -d meizu15 -r RELEASE > "build-edk2-$NAME.log" 2>&1
cd ../..

/usr/bin/python3 src/meizu15-unixnu-main/scripts/ci/retarget_header.py \
  src/edk2-msm/boot-meizu15.img src/meizu15-unixnu-main/edk2-msm-overlay/boot-header-1632.bin \
  "out/boot-meizu15-payload-$NAME.img"
ls -l "out/boot-meizu15-payload-$NAME.img"
