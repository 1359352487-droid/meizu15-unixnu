/*
 * sdm660_shim.h -- the translation layer between XNU's Apple-hardware
 * assumptions and what a Snapdragon 660 (Meizu 15, M8810) actually has.
 *
 * Why this exists
 * ---------------
 * XNU's platform layer (pexpert + osfmk/arm64) is written for Apple silicon:
 * an AIC interrupt controller, Apple's own timebase block, Apple UARTs, Apple's
 * PMGR. None of that is present on SDM660, and none of Apple's drivers are in
 * the open-source tree -- so "porting iOS to this phone" means supplying the
 * translation here: figure out from the device tree what the board really has,
 * map those registers, and hand the kernel something it can call.
 *
 * What it does
 * ------------
 * This layer is deliberately small and passive. It does not replace XNU's
 * pmap, scheduler or exception handling. It:
 *
 *   1. reads the board description (GIC distributor/redistributor, a debug
 *      UART, the generic timer frequency) out of the device tree the loader
 *      handed over, validating every value before use;
 *   2. maps those register windows with the kernel's own mapping primitive;
 *   3. reports what it found, in one line each, so a photograph of the panel
 *      is enough to know how far it got;
 *   4. provides early console output through the QUP UART, because reading the
 *      log off the framebuffer through a camera is the slowest part of every
 *      debugging round.
 *
 * What it does NOT do yet
 * -----------------------
 *   - it does not take over interrupt delivery: HakiOS already ships
 *     osfmk/arm64/gic_v3_irq.c, and replacing a working driver with a second
 *     one is how boards get two interrupt stacks;
 *   - it does not program the PMIC, RPMh or SMMU (see docs/DRIVER-PORT.md);
 *   - nothing here touches a register unless the boot argument says so.
 *
 * Boot arguments
 * --------------
 *   sdm660_shim=1        enable the layer at all (default: off, so a kernel
 *                        built with it behaves exactly as before)
 *   sdm660_uart=<pa>     override the UART's physical address
 *   sdm660_baud=<n>      UART baud rate (default 115200)
 *   sdm660_verbose=1     one line per device tree lookup, for bring-up
 */

#ifndef _OSFMK_ARM64_SDM660_SHIM_H_
#define _OSFMK_ARM64_SDM660_SHIM_H_

#include <mach/mach_types.h>
#include <mach/vm_types.h>
#include <stdbool.h>
#include <stdint.h>

/* A register window the layer has mapped. pa == 0 means "not found". */
typedef struct sdm660_window {
	uint64_t        pa;             /* physical address as the DT gave it      */
	uint64_t        size;           /* bytes                                   */
	volatile void  *va;             /* kernel virtual address after mapping    */
} sdm660_window_t;

/*
 * Everything the layer knows about this board. Filled by sdm660_shim_probe()
 * from the device tree, then used by the individual pieces.
 */
typedef struct sdm660_board {
	sdm660_window_t gic_dist;
	sdm660_window_t gic_redist;
	sdm660_window_t uart;

	uint32_t        gic_redist_stride;   /* bytes between redistributor frames   */
	uint32_t        gic_lines;           /* number of SPIs the DT describes, if any */
	uint32_t        timebase_freq;       /* CNTFRQ_EL0 as the DT states it       */
	uint32_t        boot_cpu;            /* MPIDR of the CPU we booted on        */

	bool            probed;
	bool            uart_ready;
	bool            verbose;
} sdm660_board_t;

extern sdm660_board_t sdm660_board;

/* sdm660_shim.c: parse the DT, map the windows, report. Called once, early. */
void     sdm660_shim_start(void);

/* sdm660_uart.c: early console. Safe to call before pmap is up (it is not). */
bool     sdm660_uart_init(uint64_t override_pa, uint32_t baud);
void     sdm660_uart_putc(char c);
void     sdm660_uart_puts(const char *s);
void     sdm660_uart_puthex(uint64_t v);
void     sdm660_uart_putdec(uint64_t v);

/* sdm660_gic.c: check the controller the DT describes, and say what it is. */
bool     sdm660_gic_probe(void);
void     sdm660_gic_report(void);

/* sdm660_timer.c: confirm the architectural timer matches the DT. */
bool     sdm660_timer_probe(void);
uint64_t sdm660_timer_count(void);

/* One line to both the kernel log and the UART, so either channel shows it. */
void     sdm660_log(const char *fmt, ...);

#endif /* _OSFMK_ARM64_SDM660_SHIM_H_ */
