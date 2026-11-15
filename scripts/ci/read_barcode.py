"""Read the fault barcodes DarwinTrap draws, from a description of the pixels.

The handler paints four rows of bit-blocks into the framebuffer by physical
address, because at that point there is no working stack and no boot services:

    y = 1400   ESR_EL1   32 bits
    y = 1460   ELR_EL1   64 bits
    y = 1520   FAR_EL1   64 bits
    y = 1580   marker    8 bits, always 0xA5

Layout per row: a four-pixel start tick at x = 0, then one block per bit, each
block eight pixels wide and twenty tall, separated by a one-pixel gap. White
(0xFFFFFFFF) is a one; anything dark is a zero.

This script does not read JPEGs -- decoding a photograph of a screen reliably
needs the camera original, crop boxes and thresholding that are easier to reason
about by hand. What it does is turn a run-length description of one row into the
value, so reading the numbers off a photo becomes transcription rather than
mental arithmetic:

    python read_barcode.py 64 1111 1 111 11111111 11 1 ...
    python read_barcode.py 64 --bits 1111010101...

Run-length form: alternating runs starting with dark, each count being the
number of pixels. Widths 8 (a one) and 9 (a zero, since zero-width blocks are
gap only) are what the handler produces; the script also accepts proportional
counts and normalises them.
"""
import sys


def from_runs(runs, count):
    """Turn a list of dark/light run lengths, starting dark, into a bit string."""
    width = max(runs) if runs else 0
    unit = round(width / 8.0) if width else 1
    if unit < 1:
        unit = 1
    bits = []
    for index, length in enumerate(runs):
        if index % 2 == 1:                      # light run: these are ones
            n = max(1, round(length / unit))
            bits.append("1" * n)
    text = "".join(bits)
    return text[-count:] if len(text) > count else text.rjust(count, "0")


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1

    count = int(argv[1])
    if argv[2] == "--bits":
        bits = "".join(argv[3:])
        bits = bits[-count:].rjust(count, "0")
    else:
        runs = [int(x) for x in argv[2:]]
        bits = from_runs(runs, count)

    value = int(bits, 2)
    print("bits   %s" % bits)
    print("value  0x%0*x" % (count // 4, value))

    if count == 32:
        ec = (value >> 26) & 0x3F
        il = (value >> 25) & 1
        iss = value & 0x1FFFFFF
        print()
        print("ESR_EL1 breakdown")
        print("  EC  0x%02x  %s" % (ec, {
            0x00: "unknown / undefined instruction",
            0x01: "trapped WFI/WFE",
            0x07: "SMC (AArch32)",
            0x0E: "illegal execution state",
            0x15: "SVC (AArch64)",
            0x18: "trapped MSR/MRS/system instruction",
            0x20: "instruction abort, lower EL",
            0x21: "instruction abort, same EL",
            0x22: "PC alignment fault",
            0x24: "data abort, lower EL",
            0x25: "data abort, same EL",
            0x26: "SP alignment fault",
            0x2C: "trapped FP/SIMD",
            0x3C: "BRK (breakpoint)",
        }.get(ec, "see the ARM ARM, EC 0x%02x" % ec)))
        print("  IL  %d  %s" % (il, "32-bit instruction" if il else "16-bit instruction"))
        print("  ISS 0x%07x" % iss)
        if ec in (0x24, 0x25):
            dfsc = iss & 0x3F
            print()
            print("  data abort  DFSC 0x%02x  %s" % (dfsc, {
                0x04: "translation fault, level 0 -- nothing mapped there",
                0x05: "translation fault, level 1",
                0x06: "translation fault, level 2",
                0x07: "translation fault, level 3",
                0x09: "access flag fault, level 1",
                0x0D: "permission fault, level 1",
                0x0E: "permission fault, level 2",
                0x0F: "permission fault, level 3",
                0x10: "synchronous external abort",
            }.get(dfsc, "see the ARM ARM")))
            print("  WnR %d  %s" % ((iss >> 6) & 1, "write" if (iss >> 6) & 1 else "read"))
        if ec in (0x20, 0x21):
            ifsc = iss & 0x3F
            print()
            print("  instruction abort  IFSC 0x%02x  %s" % (ifsc, {
                0x04: "translation fault, level 0",
                0x05: "translation fault, level 1",
                0x06: "translation fault, level 2",
                0x07: "translation fault, level 3",
                0x0D: "permission fault, level 1",
                0x0E: "permission fault, level 2",
                0x0F: "permission fault, level 3",
            }.get(ifsc, "see the ARM ARM")))

    if count == 64:
        print()
        print("as a VA/PA this looks like: 0x%016x" % value)
        if (value >> 56) == 0xFF:
            print("  top byte 0xFF -> kernel virtual address space")
        elif value == 0:
            print("  all zero -> the register was not set on this path")
        else:
            print("  not a kernel VA (top byte 0x%02x)" % (value >> 56))

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
