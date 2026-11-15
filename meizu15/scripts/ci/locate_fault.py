"""Disassemble around a kernel virtual address, for reading a fault report.

DarwinBoot now prints ESR_EL1 / ELR_EL1 / FAR_EL1 from the early-exception
vector. ELR is the instruction the CPU refused to execute, so the first thing
worth doing with it is looking at that instruction -- and at the dozen before it,
which usually contain the setup that produced the bad address.

    locate_fault.py <kernel.macho> <hex-va> [count]

Prints the originating symbol when it can be found in the LC_SYMTAB, then the
surrounding words decoded far enough to read: branches, loads/stores, adrp/add
pairs, and the register operands of each. It is deliberately not a full
disassembler -- it answers "what is this instruction" and "where in the kernel is
this", which is what a photograph of three hex numbers can support.
"""
import struct
import sys

# Only the encodings that show up in start.s / pmap / arm_init paths. Anything
# else prints as a raw word, which is still useful: the encoding can be pasted
# into any disassembler.
def decode(w, pc):
    """Return (mnemonic, is_branch_target) for a handful of arm64 encodings."""
    if w == 0xD503201F:
        return "nop", False
    if w == 0xD503205F:
        return "wfe", False
    if w == 0xD503207F:
        return "wfi", False
    if w == 0xD5033FDF:
        return "isb", False
    if w == 0xD5033F9F:
        return "dsb sy", False
    if (w & 0xFFFFF01F) == 0xD503301F:
        return "msr/mrs", False

    if (w & 0x7C000000) == 0x14000000:                 # B / BL
        op = "bl" if (w >> 31) else "b"
        imm = w & 0x03FFFFFF
        if imm & 0x02000000:
            imm -= 0x04000000
        target = pc + (imm << 2)
        if imm == -1:
            return "b .            <-- permanent spin", True
        return "%s 0x%x" % (op, target), True

    if (w & 0xFF000010) == 0x54000000:                 # B.cond
        imm = (w >> 5) & 0x7FFFF
        if imm & 0x40000:
            imm -= 0x80000
        cond = w & 0xF
        return "b.cond %d 0x%x" % (cond, pc + (imm << 2)), True

    if (w & 0x7E000000) == 0x34000000:                 # CBZ / CBNZ
        imm = (w >> 5) & 0x7FFFF
        if imm & 0x40000:
            imm -= 0x80000
        rt = w & 0x1F
        op = "cbnz" if (w >> 24) & 1 else "cbz"
        return "%s x%d, 0x%x" % (op, rt, pc + (imm << 2)), True

    if (w & 0x7E000000) == 0x36000000:                 # TBZ / TBNZ
        imm = (w >> 5) & 0x3FFF
        if imm & 0x2000:
            imm -= 0x4000
        rt = w & 0x1F
        bit = ((w >> 31) << 5) | ((w >> 19) & 0x1F)
        op = "tbnz" if (w >> 24) & 1 else "tbz"
        return "%s x%d, #%d, 0x%x" % (op, rt, bit, pc + (imm << 2)), True

    if (w & 0x9F000000) == 0x90000000:                 # ADRP
        rd = w & 0x1F
        immlo = (w >> 29) & 3
        immhi = (w >> 5) & 0x7FFFF
        imm = (immhi << 2) | immlo
        if imm & 0x100000:
            imm -= 0x200000
        return "adrp x%d, 0x%x" % (rd, (pc & ~0xFFF) + (imm << 12)), False

    if (w & 0x7F800000) == 0x11000000:                 # ADD (immediate, 64-bit)
        rd, rn = w & 0x1F, (w >> 5) & 0x1F
        imm = (w >> 10) & 0xFFF
        if (w >> 22) & 1:
            imm <<= 12
        return "add x%d, x%d, #0x%x" % (rd, rn, imm), False

    if (w & 0x3B000000) == 0x39000000:                 # LDR/STR (unsigned imm)
        rt, rn = w & 0x1F, (w >> 5) & 0x1F
        size = w >> 30
        opc = (w >> 22) & 3
        imm = (w >> 10) & 0xFFF
        scale = 1 << size
        names = {(0, 0): "strb", (0, 1): "ldrb", (1, 0): "strh", (1, 1): "ldrh",
                 (2, 0): "str", (2, 1): "ldr", (3, 0): "str", (3, 1): "ldr"}
        name = names.get((size, opc))
        if name is None:
            return "0x%08x" % w, False
        reg = ("w%d" % rt) if (size < 3 and opc & 1 == 1 and size < 2) else ("x%d" % rt)
        if size == 2 and opc == 1:
            reg = "w%d" % rt
        return "%s %s, [x%d, #0x%x]" % (name, reg, rn, imm * scale), False

    if (w & 0x3B200C00) == 0x38000000:                 # LDUR/STUR (unscaled)
        rt, rn = w & 0x1F, (w >> 5) & 0x1F
        size = w >> 30
        opc = (w >> 22) & 3
        imm = (w >> 12) & 0x1FF
        if imm & 0x100:
            imm -= 0x200
        names = {(0, 0): "sturb", (0, 1): "ldurb", (1, 0): "sturh", (1, 1): "ldurh",
                 (2, 0): "stur", (2, 1): "ldur", (3, 0): "stur", (3, 1): "ldur"}
        name = names.get((size, opc))
        if name is None:
            return "0x%08x" % w, False
        reg = ("w%d" % rt) if size == 2 else ("x%d" % rt)
        return "%s %s, [x%d, #%d]" % (name, reg, rn, imm), False

    if (w & 0xFFC00000) == 0xA9800000 or (w & 0xFFC00000) == 0xA9000000:
        rt, rn = w & 0x1F, (w >> 5) & 0x1F
        rt2 = (w >> 10) & 0x1F
        imm = ((w >> 15) & 0x7F) << 3
        if imm & 0x200:
            imm -= 0x400
        op = "ldp" if (w >> 22) & 1 else "stp"
        return "%s x%d, x%d, [x%d, #%d]" % (op, rt, rt2, rn, imm), False

    if (w & 0xFFC00000) == 0xA9000000:
        pass

    if (w & 0xFFFFFC1F) == 0xD65F0000:
        return "ret", True
    if (w & 0xFFFFFC1F) == 0xD61F0000:
        return "br x%d" % ((w >> 5) & 0x1F), True
    if (w & 0xFFFFF81F) == 0xD63F0000:
        return "blr x%d" % ((w >> 5) & 0x1F), True

    if (w & 0xFFE0001F) == 0xD4000001:
        return "svc #0", False
    if (w & 0xFFE0001F) == 0xD4000002:
        return "hvc #0", False
    if (w & 0xFFE0001F) == 0xD4000003:
        return "smc #0", False

    return "0x%08x" % w, False


def segments(blob):
    ncmds = struct.unpack_from("<I", blob, 16)[0]
    off = 32
    out = []
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", blob, off)
        if cmd == 0x19:
            name = blob[off + 8:off + 24].rstrip(b"\x00").decode("latin-1")
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<QQQQ", blob, off + 24)
            out.append((name, vmaddr, vmsize, fileoff, filesize))
        elif cmd == 0x2:                       # LC_SYMTAB
            symoff, nsyms, stroff, strsize = struct.unpack_from("<IIII", blob, off + 8)
            out.append(("__symtab__", symoff, nsyms, stroff, strsize))
        off += cmdsize
    return out


def symbol_for(blob, segs, va):
    symoff = nsyms = stroff = None
    for name, a, b, c, d in segs:
        if name == "__symtab__":
            symoff, nsyms, stroff = a, b, c
    if symoff is None or nsyms == 0:
        return None
    best = None
    for i in range(nsyms):
        o = symoff + i * 16
        if o + 16 > len(blob):
            break
        strx, ntype, sect, desc, value = struct.unpack_from("<IBBHQ", blob, o)
        if value == 0 or (ntype & 0x0E) != 0x0E:
            continue
        end = blob.find(b"\x00", stroff + strx)
        nm = blob[stroff + strx:end].decode("latin-1")
        if value <= va and (best is None or value > best[1]):
            best = (nm, value)
    if best and (va - best[1]) < 0x200000:
        return "%s+0x%x" % (best[0], va - best[1])
    return None


def main(argv):
    blob = open(argv[1], "rb").read()
    va = int(argv[2], 16)
    count = int(argv[3]) if len(argv) > 3 else 12

    if blob[:4] != b"\xcf\xfa\xed\xfe":
        print("not a little-endian 64-bit Mach-O")
        return 1

    segs = segments(blob)
    fileoff = None
    for name, vmaddr, vmsize, foff, fsize in segs:
        if name == "__symtab__":
            continue
        if vmaddr <= va < vmaddr + vmsize and fsize:
            if foff == 0 and va >= vmaddr + fsize:
                continue                     # __PAGEZERO-style, no file content
            fileoff = foff + (va - vmaddr)
            print("0x%x -> %s, file offset 0x%x" % (va, name, fileoff))
            break
    if fileoff is None:
        print("0x%x is not in any file-backed segment" % va)
    sym = symbol_for(blob, segs, va)
    print("symbol: %s" % (sym or "(none within 2 MiB)"))

    if fileoff is None:
        return 1

    start = fileoff - count * 4
    if start < 0:
        start = 0
    for i in range(count * 2 + 1):
        o = start + i * 4
        if o + 4 > len(blob):
            break
        w = struct.unpack_from("<I", blob, o)[0]
        here = va - (fileoff - o)
        text, _ = decode(w, here)
        mark = " <== ELR" if here == va else ""
        print("  %012x  %08x  %s%s" % (here, w, text, mark))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
