/*
 * sdm660_uart.c -- early console through the board's debug UART.
 *
 * This is the piece that changes the workflow: today every round ends with a
 * photograph of the panel, because the only way the kernel's log reaches the
 * outside world is the loader's return stub painting it before handing control
 * back. One working UART removes that.
 *
 * Two register layouts are implemented, because "the debug UART on SDM660" is
 * not one thing:
 *
 *   SDM660_UART_LEGACY   an 8250-shaped block (GSBI-style): THR at 0x00,
 *                        LSR at 0x14 with bit 5 = transmit holding register
 *                        empty. Firmware that leaves a UART up for the OS
 *                        normally leaves it in this shape.
 *
 *   SDM660_UART_GENI     QUPv3 "GENI" serial engine: the transmit FIFO is at
 *                        0x100, and the engine has to be told a character is
 *                        waiting before every byte.
 *
 * Neither layout is programmed from scratch here. Bringing up a QUP needs the
 * RPMh clock and interconnect votes, which is a driver of its own (see
 * docs/DRIVER-PORT.md, section 4.2); what this file relies on is that the
 * bootloader -- ABL on this phone -- has already clocked the debug UART and
 * set its baud rate, which is the usual arrangement and the only one that gets
 * console output out of the first second of boot.
 *
 * So: nothing here reconfigures a clock, and nothing here is fatal. If the
 * UART does not answer, the layer says so once and the log stays on the
 * framebuffer.
 */
#include <pexpert/pexpert.h>
#include <machine/machine_routines.h>
#include <libkern/libkern.h>

#include "sdm660_shim.h"

#define SDM660_UART_LEGACY      0
#define SDM660_UART_GENI        1

/* 8250-shaped: the offsets a GSBI/many-vendor debug port uses. */
#define UART_LEGACY_THR         0x00
#define UART_LEGACY_LSR         0x14
#define UART_LEGACY_LSR_THRE    0x20    /* transmitter holding register empty */

/* QUPv3 GENI serial engine: transmit FIFO and its status. */
#define UART_GENI_TX_FIFO       0x100
#define UART_GENI_STATUS        0x040
#define UART_GENI_STATUS_TX     (1u << 2)

static volatile uint8_t *uart_base;
static int               uart_kind = SDM660_UART_LEGACY;

static inline uint32_t
uart_rd32(uint32_t off)
{
	return *(volatile uint32_t *)(uart_base + off);
}

static inline void
uart_wr32(uint32_t off, uint32_t val)
{
	*(volatile uint32_t *)(uart_base + off) = val;
}

/*
 * Push one byte. Bounded wait: an unclocked UART must not become a hang, which
 * would turn a debugging aid into the bug.
 */
static void
uart_raw_putc(char c)
{
	uint32_t spins;

	if (uart_base == NULL) {
		return;
	}
	if (uart_kind == SDM660_UART_GENI) {
		for (spins = 0; spins < 100000; spins++) {
			if (uart_rd32(UART_GENI_STATUS) & UART_GENI_STATUS_TX) {
				break;
			}
		}
		uart_wr32(UART_GENI_TX_FIFO, (uint32_t)(uint8_t)c);
		return;
	}
	for (spins = 0; spins < 100000; spins++) {
		if (uart_rd32(UART_LEGACY_LSR) & UART_LEGACY_LSR_THRE) {
			break;
		}
	}
	uart_wr32(UART_LEGACY_THR, (uint32_t)(uint8_t)c);
}

/*
 * Probe. The only reliable test that does not disturb a running console is to
 * look at the line status register: a peripheral that is not clocked reads
 * back as all-ones or all-zeroes, and a live one almost never does. If the
 * legacy guess fails, try the GENI offsets and keep whichever answered.
 *
 * override_pa is unused here (the caller already set sdm660_board.uart and
 * mapped it); it stays in the signature because future board variants may want
 * to map a second port for a second channel.
 */
bool
sdm660_uart_init(uint64_t override_pa, uint32_t baud)
{
	uint32_t lsr;
	uint32_t enabled = 0;

	(void)baud;

	if (sdm660_board.uart.va == NULL) {
		return false;
	}
	uart_base = (volatile uint8_t *)sdm660_board.uart.va;

	if (PE_parse_boot_argn("sdm660_uart_geni", &enabled, sizeof(enabled)) && enabled) {
		uart_kind = SDM660_UART_GENI;
	} else if (PE_parse_boot_argn("sdm660_uart_legacy", &enabled, sizeof(enabled)) && enabled) {
		uart_kind = SDM660_UART_LEGACY;
	} else {
		lsr = uart_rd32(UART_LEGACY_LSR);
		if (lsr == 0 || lsr == 0xffffffffu) {
			uart_kind = SDM660_UART_GENI;   /* try the other one below */
		}
	}

	if (uart_kind == SDM660_UART_GENI) {
		lsr = uart_rd32(UART_GENI_STATUS);
		if (lsr == 0xffffffffu) {
			sdm660_log("sdm660: uart does not answer at either layout\n");
			uart_base = NULL;
			return false;
		}
	}

	uart_kind = uart_kind;  /* keep the choice visible in a debugger */
	return true;
}

void
sdm660_uart_putc(char c)
{
	if (c == '\n') {
		uart_raw_putc('\r');
	}
	uart_raw_putc(c);
}

void
sdm660_uart_puts(const char *s)
{
	for (; *s != 0; s++) {
		sdm660_uart_putc(*s);
	}
}

void
sdm660_uart_puthex(uint64_t v)
{
	static const char digits[] = "0123456789abcdef";
	char buf[19];
	int i = 0;

	buf[i++] = '0';
	buf[i++] = 'x';
	for (int shift = 60; shift >= 0; shift -= 4) {
		buf[i++] = digits[(v >> shift) & 0xf];
	}
	buf[i] = 0;
	sdm660_uart_puts(buf);
}

void
sdm660_uart_putdec(uint64_t v)
{
	char buf[24];
	int i = 0;

	do {
		buf[i++] = (char)('0' + (v % 10));
		v /= 10;
	} while (v != 0 && i < 20);
	while (i > 0) {
		sdm660_uart_putc(buf[--i]);
	}
}
