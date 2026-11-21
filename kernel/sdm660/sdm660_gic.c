/*
 * sdm660_gic.c -- check the interrupt controller the device tree describes.
 *
 * This file deliberately does NOT take over interrupt delivery. HakiOS already
 * ships osfmk/arm64/gic_v3_irq.c and uses it on QEMU's virt machine, which is
 * a GICv3; SDM660 is also a GICv3. Two drivers for one controller is how a
 * board ends up with two interrupt stacks and one of them never drains.
 *
 * What is missing is not the driver, it is the board description: XNU's
 * pe_arm_map_interrupt_controller() walks the device tree looking for an
 * "interrupt-controller" node, and inputs/meizu15-8plus.adt has none -- which
 * is exactly what the panel has been saying since kprintf came up:
 *
 *     pe_arm_map_interrupt_controller: failed to find the interrupt-controller
 *
 * So the job here is to say, in one line, what the controller actually is
 * (GICv3, how many lines, what the redistributor stride looks like) so that the
 * node can be written into the ADT with the numbers the hardware reports rather
 * than numbers guessed from a datasheet.
 *
 * Register map (ARM IHI 0069, "GICv3 distributor"):
 *   GICD_PIDR2  0x0FE8   bits[7:4] = architecture: 0x3 = GICv3, 0x4 = GICv4
 *   GICD_TYPER  0x0004   bits[4:0] = ITLinesNumber; SPIs = 32 * (N + 1)
 *   GICR_TYPER  0x0008   in the first redistributor frame, per core
 */
#include <machine/machine_routines.h>
#include <libkern/libkern.h>

#include "sdm660_shim.h"

#define GICD_CTLR               0x0000
#define GICD_TYPER              0x0004
#define GICD_PIDR2              0x0FE8
#define GICR_TYPER              0x0008

#define GICD_PIDR2_ARCH_MASK    0xF0
#define GICD_PIDR2_ARCH_GICV3   0x30
#define GICD_PIDR2_ARCH_GICV4   0x40

static uint32_t
gicd_rd32(uint32_t off)
{
	return *(volatile uint32_t *)((volatile uint8_t *)sdm660_board.gic_dist.va + off);
}

static uint32_t
gicr_rd32(uint32_t off)
{
	return *(volatile uint32_t *)((volatile uint8_t *)sdm660_board.gic_redist.va + off);
}

/*
 * Fill in what the tree did not tell us, if the controller answers. Returns
 * false when there is no window to look at, which is not an error at this
 * stage -- it just means the ADT still has to grow the node.
 */
bool
sdm660_gic_probe(void)
{
	uint32_t pidr2;

	if (sdm660_board.gic_dist.va == NULL) {
		return false;
	}
	pidr2 = gicd_rd32(GICD_PIDR2);
	if ((pidr2 & GICD_PIDR2_ARCH_MASK) == GICD_PIDR2_ARCH_GICV3 ||
	    (pidr2 & GICD_PIDR2_ARCH_MASK) == GICD_PIDR2_ARCH_GICV4) {
		sdm660_board.gic_lines = 32 * ((gicd_rd32(GICD_TYPER) & 0x1f) + 1);
		return true;
	}
	return false;
}

void
sdm660_gic_report(void)
{
	uint32_t pidr2;
	uint64_t typer;

	if (sdm660_board.gic_dist.va == NULL) {
		return;         /* the caller already said the node is missing */
	}

	pidr2 = gicd_rd32(GICD_PIDR2);
	if (!sdm660_gic_probe()) {
		sdm660_log("sdm660: gic at 0x%llx does not look like a GICv3 "
		    "(PIDR2 0x%x)\n", sdm660_board.gic_dist.pa, pidr2);
		return;
	}

	typer = sdm660_board.gic_redist.va ?
	    (uint64_t)gicr_rd32(GICR_TYPER) | ((uint64_t)gicr_rd32(GICR_TYPER + 4) << 32) : 0;

	sdm660_log("sdm660: gicv3 dist 0x%llx, %u lines, redist 0x%llx stride 0x%x, "
	    "ctlr 0x%x, typer 0x%llx\n",
	    sdm660_board.gic_dist.pa,
	    sdm660_board.gic_lines,
	    sdm660_board.gic_redist.pa,
	    sdm660_board.gic_redist_stride,
	    gicd_rd32(GICD_CTLR),
	    typer);
}
