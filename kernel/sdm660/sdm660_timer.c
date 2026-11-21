/*
 * sdm660_timer.c -- confirm the architectural timer is the one XNU will use.
 *
 * XNU's arm64 timebase goes through rtclock_timebase_func, a set of function
 * pointers that Apple's platform code fills in for Apple's own timer block.
 * On a non-Apple SoC the architectural generic timer is what exists:
 *
 *   CNTFRQ_EL0   the frequency, in Hz, as firmware left it
 *   CNTPCT_EL0   the physical count, which is what ml_get_hwclock() reads
 *   CNTV_CVAL / CNTV_TVAL / CNTV_CTL   the virtual timer, per CPU
 *
 * Its frequency on SDM660 is 19.2 MHz (the same 0x124F800 the stock device
 * tree declares as timebase-frequency), and that value is what the device
 * tree's /cpus/timebase-frequency is checked against here.
 *
 * This file does not install the timer: cpu_timebase_init() already does, and
 * that is the call the port is currently sitting inside (checkpoints 25 -> 26).
 * What it adds is the one fact that call cannot report by itself -- whether
 * CNTFRQ_EL0 is actually populated at this point in the boot. A bootloader that
 * clears it leaves CNTFRQ at 0, and every interval computed from it then
 * collapses to zero, which looks like "the timer never fires" from a distance.
 */
#include <machine/machine_routines.h>

#include "sdm660_shim.h"

#define SDM660_TIMEBASE_HZ      19200000u

uint64_t
sdm660_timer_count(void)
{
	return __builtin_arm_rsr64("CNTPCT_EL0");
}

/*
 * True when the architectural timer is usable as-is. When CNTFRQ_EL0 reads 0
 * the device tree value is reported instead, and the log says which one is in
 * force, because "the kernel's clock runs at 19.2 MHz" and "the kernel thinks
 * its clock runs at 19.2 MHz" are different statements and only the second one
 * is ever harmless.
 */
bool
sdm660_timer_probe(void)
{
	uint64_t cntfrq = __builtin_arm_rsr64("CNTFRQ_EL0");

	if (cntfrq == 0) {
		sdm660_log("sdm660: CNTFRQ_EL0 reads 0; the device tree says %u Hz\n",
		    sdm660_board.timebase_freq ? sdm660_board.timebase_freq : SDM660_TIMEBASE_HZ);
		return false;
	}
	if (sdm660_board.timebase_freq != 0 &&
	    sdm660_board.timebase_freq != (uint32_t)cntfrq) {
		sdm660_log("sdm660: CNTFRQ_EL0 is %llu Hz but the tree says %u Hz\n",
		    cntfrq, sdm660_board.timebase_freq);
	}
	return true;
}
