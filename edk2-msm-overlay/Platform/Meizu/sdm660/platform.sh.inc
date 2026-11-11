# Meizu 15 device hooks for edk2-msm.
#
# build.sh loads hooks in this order, later definitions win:
#   Silicon/Qualcomm/sdm660/platform.sh.inc      <- defines platform_build_bootimg
#   Platform/Meizu/sdm660/platform.sh.inc        <- this file, overrides it
#
# The upstream hook calls abootimg with no --pagesize, so it defaults to 2048.
# This device needs 4096: the stock boot.img in dump/boot.img reports
# page_size=4096 (header_version 0), and the bootloader computes the kernel,
# ramdisk and DTB offsets from that value. A 2048 image would put every segment
# in the wrong place.

function platform_build_bootimg(){
	abootimg \
		--create "${OUTDIR}/boot-${DEVICE}${EXT}.img" \
		--pagesize 4096 \
		-k "${WORKSPACE}/uefi-${DEVICE}-kernel" -r ramdisk \
		||return "$?"
}
