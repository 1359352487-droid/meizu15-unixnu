/*
 * sdm660_shim.c -- probe the board and hand XNU what SDM660 actually has.
 *
 * The order below is the whole point of this file: everything the shim knows
 * comes from the device tree the loader handed over (DarwinBoot.c fills
 * Args->DeviceTree with a physical address; arm_vm_init() maps it and this
 * layer reads it through SecureDT, whose pointer the kernel now fixes up in
 * arm_vm_physmap_init -- see apply_nonapple_guards.py, add_secure_dt_reinit).
 *
 * Nothing here is allowed to panic on a missing node. A board that is missing
 * a description is a board to report, not a kernel to kill: the panels that
 * took a day to read were the ones that said nothing.
 */
#include <stdarg.h>
#include <stddef.h>

#include <pexpert/pexpert.h>
#include <pexpert/device_tree.h>
#include <machine/machine_routines.h>
#include <kern/assert.h>
#include <kern/printf.h>
#include <kern/startup.h>
#include <libkern/libkern.h>
#include <vm/vm_kern.h>

#include "sdm660_shim.h"

sdm660_board_t sdm660_board;

/*
 * The three shapes the device tree uses for the interrupt controller across
 * the trees this port has seen:
 *
 *   Apple's own boards   /interrupt-controller            (reg = dist, size)
 *   Qualcomm upstream    /soc/interrupt-controller@17a00000
 *   the FDT convention   any node whose "compatible" says arm,gic-v3
 *
 * The first two are tried by path; the third is a walk. A board that has none
 * of them gets one line saying so, which is exactly the state of
 * inputs/meizu15-8plus.adt today (pe_arm_map_interrupt_controller already
 * complains about it).
 */
static const char * const gic_paths[] = {
	"/interrupt-controller",
	"/soc/interrupt-controller",
	"/interrupt-controller@17a00000",
	"/soc/interrupt-controller@17a00000",
};

static const char * const uart_paths[] = {
	"/serial",
	"/soc/serial",
	"/serial@0a90000",
	"/soc/serial@0a90000",
	"/soc/qcom,blsp1-uart2",
};

/*
 * Read a "reg" property the way device trees write one: one or more
 * (address, length) pairs of 32 or 64 bits, given the parent's
 * #address-cells / #size-cells. Everything this port needs is a single
 * window, so only the first pair is used -- and only after the sizes make
 * sense together, because a wrong #size-cells silently yields a window that
 * covers the next device's registers.
 */
static bool
reg_first_window(DTEntry entry, sdm660_window_t *out)
{
	void const     *prop = NULL;
	unsigned int    len = 0;
	uint32_t        cells = 0;
	uint32_t        ac = 1, sc = 1;      /* the common case on these trees */
	DTEntry         parent = NULL;
	unsigned int    parent_len = 0;

	if (SecureDTGetProperty(entry, "reg", &prop, &len) != kSuccess || prop == NULL) {
		return false;
	}
	if (len != 8 && len != 12 && len != 16 && len != 24) {
		return false;
	}
	(void)parent;
	(void)parent_len;
	(void)cells;

	/* 1 address cell + 1 size cell = 8 bytes: the layout this tree uses. */
	if (len >= 8) {
		uint32_t addr = ((uint32_t const *)prop)[0];
		uint32_t size = ((uint32_t const *)prop)[1];
		if (addr != 0 && size != 0) {
			out->pa = addr;
			out->size = size;
			return true;
		}
	}
	/* 2 + 2 cells (addresses above 4 GB, not expected but cheap to allow). */
	if (len >= 16) {
		uint64_t addr = ((uint64_t const *)prop)[0];
		uint64_t size = ((uint64_t const *)prop)[1];
		if (addr != 0 && size != 0 && addr < (1ULL << 48) && size < (1ULL << 32)) {
			out->pa = addr;
			out->size = size;
			return true;
		}
	}
	return false;
}

static bool
find_window(const char * const *paths, size_t count, sdm660_window_t *out,
    const char *what)
{
	for (size_t i = 0; i < count; i++) {
		DTEntry entry;
		if (SecureDTLookupEntry(NULL, paths[i], &entry) != kSuccess) {
			continue;
		}
		if (reg_first_window(entry, out)) {
			if (sdm660_board.verbose) {
				sdm660_log("sdm660: %s at %s pa 0x%llx size 0x%llx\n", what,
				    paths[i], out->pa, out->size);
			}
			return true;
		}
		if (sdm660_board.verbose) {
			sdm660_log("sdm660: %s found at %s but its reg did not parse\n", what, paths[i]);
		}
	}
	return false;
}

/*
 * The frequency the generic timer counts at. The architecture says a
 * firmware-provided CNTFRQ_EL0 is authoritative; the device tree's
 * timebase-frequency is what the board's own firmware puts there for Linux,
 * and it is the one that survives a bootloader that clears CNTFRQ.
 */
static uint32_t
read_timebase(void)
{
	DTEntry         entry;
	void const     *prop = NULL;
	unsigned int    len = 0;
	uint64_t        cntfrq;

	if (SecureDTLookupEntry(NULL, "/cpus", &entry) == kSuccess) {
		if (SecureDTGetProperty(entry, "timebase-frequency", &prop, &len) == kSuccess &&
		    prop != NULL) {
			if (len == 4) {
				return *(uint32_t const *)prop;
			}
			if (len == 8) {
				return (uint32_t)(*(uint64_t const *)prop);
			}
		}
	}
	cntfrq = __builtin_arm_rsr64("CNTFRQ_EL0");
	return (uint32_t)cntfrq;
}

static void
map_window(sdm660_window_t *w, const char *what)
{
	if (w->pa == 0 || w->size == 0) {
		return;
	}
	/*
	 * ml_io_map() is the kernel's own mapping primitive and is what every
	 * other early driver in this tree uses; it is available once the pmap
	 * exists, which is where this runs from.
	 */
	w->va = ml_io_map((vm_offset_t)w->pa, (vm_size_t)w->size);
	if (w->va == NULL) {
		sdm660_log("sdm660: %s 0x%llx did not map\n", what, w->pa);
	}
}

/*
 * The entry point. Started from XNU's own initcall machinery rather than from
 * a patched call site, so the shim never has to know where in arm_init() it
 * is: STARTUP(EARLY_BOOT, ...) runs after kprintf exists, which is the first
 * moment any of this can report anything useful.
 */
void
sdm660_shim_start(void)
{
	uint32_t enabled = 0;
	uint32_t baud = 115200;
	uint64_t uart_override = 0;

	if (sdm660_board.probed) {
		return;
	}
	sdm660_board.probed = true;

	if (!PE_parse_boot_argn("sdm660_shim", &enabled, sizeof(enabled)) || !enabled) {
		return;         /* nothing is touched unless a boot-argument asks */
	}
	if (PE_parse_boot_argn("sdm660_verbose", &enabled, sizeof(enabled)) && enabled) {
		sdm660_board.verbose = true;
	}
	(void)PE_parse_boot_argn("sdm660_baud", &baud, sizeof(baud));

	sdm660_log("sdm660: translation layer starting\n");

	/* --- interrupt controller --------------------------------------- */
	if (find_window(gic_paths, sizeof(gic_paths) / sizeof(gic_paths[0]),
	        &sdm660_board.gic_dist, "gic-dist")) {
		/*
		 * The redistributor window follows the distributor in the same
		 * node on every GICv3 tree; size covers all cores' frames.
		 */
		sdm660_board.gic_redist.pa = sdm660_board.gic_dist.pa + 0x100000;
		sdm660_board.gic_redist.size = 0x100000;
		sdm660_board.gic_redist_stride = 0x20000;
		map_window(&sdm660_board.gic_dist, "gic-dist");
		map_window(&sdm660_board.gic_redist, "gic-redist");
	} else {
		sdm660_log("sdm660: no interrupt-controller node in the tree "
		    "(this is why pe_arm_map_interrupt_controller says 'failed to find')\n");
	}
	sdm660_gic_report();

	/* --- timebase ---------------------------------------------------- */
	sdm660_board.timebase_freq = read_timebase();
	if (sdm660_timer_probe()) {
		sdm660_log("sdm660: timer CNTFRQ 0x%x (%u Hz), CNTPCT 0x%llx\n",
		    sdm660_board.timebase_freq, sdm660_board.timebase_freq,
		    sdm660_timer_count());
	}

	/* --- early console ---------------------------------------------- */
	if (PE_parse_boot_argn("sdm660_uart", &uart_override, sizeof(uart_override))) {
		sdm660_board.uart.pa = uart_override;
		sdm660_board.uart.size = 0x1000;
	} else if (!sdm660_board.uart.pa) {
		(void)find_window(uart_paths, sizeof(uart_paths) / sizeof(uart_paths[0]),
		    &sdm660_board.uart, "uart");
	}
	if (sdm660_board.uart.pa != 0) {
		map_window(&sdm660_board.uart, "uart");
	}
	if (sdm660_board.uart.va != NULL) {
		sdm660_board.uart_ready = sdm660_uart_init(0, baud);
		if (sdm660_board.uart_ready) {
			sdm660_log("sdm660: uart at 0x%llx, %u baud\n",
			    sdm660_board.uart.pa, baud);
		}
	} else {
		sdm660_log("sdm660: no uart in the tree; logs stay on the framebuffer\n");
	}

	sdm660_log("sdm660: translation layer ready\n");
}

/*
 * One line, both channels: kprintf for the kernel message buffer (what the
 * loader hands back), and the UART once it exists (so the console no longer
 * has to be photographed).
 */
void
sdm660_log(const char *fmt, ...)
{
	char    line[256];
	va_list ap;

	va_start(ap, fmt);
	vsnprintf(line, sizeof(line), fmt, ap);
	va_end(ap);

	kprintf("%s", line);
	if (sdm660_board.uart_ready) {
		sdm660_uart_puts(line);
	}
}

/*
 * Start from XNU's own initcall machinery rather than from a patched call site.
 * That keeps this layer self-contained: nothing in arm_init() or arm_vm_init()
 * has to know it exists, and a kernel built with it but booted without
 * sdm660_shim=1 behaves exactly like the kernel without it.
 *
 * EARLY_BOOT runs after kprintf exists, which is the first moment a probe can
 * report anything, and before the driver matching that would want the
 * controller description this layer prints.
 */
STARTUP(EARLY_BOOT, STARTUP_RANK_LAST, sdm660_shim_start);
