"""Look for the places an arm64 kernel can park without faulting.

A bare `b .`, a `wfe` loop and a register poll all stop the machine silently: no
exception, no vector entry, nothing on screen. With checkpoint 10 (the first
statement of arm_init) never reached, the kernel is stuck in one of these before
its C code runs, so they are worth locating by pattern.

    find_spin.py <kernel.macho> <hex-va> [count]
"""
import struct
import sys

WFE = 0xD503205F
WFI = 0xD503207F
SEV = 0xD503209F
YIELD = 0xD503203F


def load(path):
    return open(path, "rb").read()


def section_for(blob, va):
    """Map a virtual address to a file offset using LC_SEGMENT_64 entries."""
    ncmds = struct.unpack_from("<I", blob, 16)[0]
    off = 32
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", blob, off)
        if cmd == 0x19:                       # LC_SEGMENT_64
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<QQQQ", blob, off + 24)
            name = blob[off + 8:off + 24].rstrip(b"\x00").decode("latin-1")
            if vmaddr <= va < vmaddr + vmsize and filesize:
                return name, fileoff + (va - vmaddr)
        off += cmdsize
    return None, None


def main(argv):
    blob = load(argv[1])
    va = int(argv[2], 16)
    count = int(argv[3]) if len(argv) > 3 else 4000

    name, off = section_for(blob, va)
    if off is None:
        print("0x%x is not in any file-backed segment" % va)
        return 1
    print("0x%x -> %s file 0x%x" % (va, name, off))

    # Walk backwards for a plausible prologue so the listing starts somewhere
    # meaningful, then scan forward.
    print()
    print("stalls and self-branches in the next %d instructions:" % count)
    hits = 0
    for i in range(count):
        o = off + i * 4
        if o + 4 > len(blob):
            break
        w = struct.unpack_from("<I", blob, o)[0]
        here = va + i * 4
        kind = None
        if w == WFE:
            kind = "wfe"
        elif w == WFI:
            kind = "wfi"
        elif w == SEV:
            kind = "sev"
        elif w == YIELD:
            kind = "yield"
        elif (w & 0xFC000000) == 0x14000000:      # B
            imm = w & 0x03FFFFFF
            if imm & 0x02000000:
                imm -= 0x04000000
            if imm == -1:
                kind = "b .   <-- permanent spin"
            elif imm < 0:
                kind = "b -0x%x (backward)" % (-imm * 4)
        if kind:
            print("  0x%x  %08x  %s" % (here, w, kind))
            hits += 1
    if hits == 0:
        print("  none")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
