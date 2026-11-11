# Meizu 15 device hooks for edk2-msm.
#
# build.sh sources platform hooks in a fixed order; later definitions win:
#   Silicon/Qualcomm/sdm660/platform.sh.inc      <- defines platform_build_bootimg
#   Platform/Meizu/sdm660/platform.sh.inc        <- this file, overrides it
#
# Why the override:
#   The upstream hook calls `abootimg`, whose page size is fixed at 2048 and which
#   offers no flag to change it -- `abootimg --pagesize 4096` merely prints its
#   help and exits 0, so the build reports "Build done" while writing nothing.
#   This device needs 4096: dump/boot.img reports page_size=4096 (header_version 0)
#   and the bootloader derives the kernel/ramdisk/DTB offsets from that field.
#
#   The image is therefore written by tools/mkbootimg4096.py, a small AOSP v0
#   writer that takes the page size explicitly. Segment addresses stay 0 to match
#   the stock image; BootShim relocates the volume to FD_BASE itself.

function platform_build_bootimg(){
	python3 "${ROOTDIR}/Platform/${VENDOR_NAME}/${SOC_PLATFORM_L}/tools/mkbootimg4096.py" \
		"${OUTDIR}/boot-${DEVICE}${EXT}.img" \
		"${WORKSPACE}/uefi-${DEVICE}-kernel" \
		ramdisk \
		4096 \
		||return "$?"
}