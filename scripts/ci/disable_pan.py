"""Turn PAN off, because this SoC does not have it.

HakiOS enables PAN in the BCM2837 board header, and the comment says why:

    /*
     * The cores this kernel really runs on (Cortex-A55/A78, QEMU cortex-a76) are
     * ARMv8.2: they set PSTATE.PAN on every exception entry, so the kernel must
     * know to lift it around user memory accesses.
     */
    #define __ARM_PAN_AVAILABLE__     1

and it also asks the compiler to emit PAN instructions:

    MACHINE_FLAGS_ARM64_BCM2837 = -DARM64_BOARD_CONFIG_BCM2837 \\
        -Xclang -target-feature -Xclang +pan

That is correct for A55/A78. It is wrong for Kryo 260: SDM660 is four Cortex-A73
plus four Cortex-A53, all ARMv8.0. PAN arrived in ARMv8.1 as an optional feature,
and these cores do not implement it, so `msr pan, #1` is an undefined instruction.

The instruction in question is in arm_init(), between the two probes that bracket
the failure:

    #if __ARM_PAN_AVAILABLE__
        __builtin_arm_wsr("pan", 1);
    #endif
    arm_vm_init(xmaxmem, args);

Probe 45 sits above it (fires), probe 58 sits inside arm_vm_init() (silent), and
an undefined-instruction exception here raises a panic that prints nothing because
kprintf is not up yet. halt_all_cpus() then hands back `reason 0` with the 192-byte
log written by do_cacheid() -- the same signature every probe past this point has
produced for twenty rounds.

    python disable_pan.py <hakios/xnu-src root>
"""
import re
import sys
from pathlib import Path


def find(root, suffix):
    matches = [p for p in Path(root).rglob(suffix) if ".dSYM" not in str(p)]
    if not matches:
        raise SystemExit("not found under %s: %s" % (root, suffix))
    return matches[0]


def clear_header(path):
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(r"^#define\s+__ARM_PAN_AVAILABLE__\s+1\s*$", re.M)
    if not pattern.search(text):
        if re.search(r"^#define\s+__ARM_PAN_AVAILABLE__\s+0\s*$", text, re.M):
            print("  header already says 0")
            return False
        raise SystemExit("%s: no '__ARM_PAN_AVAILABLE__ 1' to replace" % path)

    text = pattern.sub(
        "/*\n"
        " * Cleared for Kryo 260 (SDM660): four Cortex-A73 and four Cortex-A53, all\n"
        " * ARMv8.0. PAN is an ARMv8.1 optional feature and these cores do not\n"
        " * implement it, so `msr pan, #1` would be an undefined instruction.\n"
        " */\n"
        "#define __ARM_PAN_AVAILABLE__     0",
        text,
        count=1,
    )
    path.write_text(text, encoding="utf-8")
    print("  __ARM_PAN_AVAILABLE__ -> 0 in %s" % path.name)
    return True


def clear_machine_flags(path):
    text = path.read_text(encoding="utf-8")
    old = ("MACHINE_FLAGS_ARM64_BCM2837 = -DARM64_BOARD_CONFIG_BCM2837 "
           "-Xclang -target-feature -Xclang +pan")
    new = "MACHINE_FLAGS_ARM64_BCM2837 = -DARM64_BOARD_CONFIG_BCM2837"
    if old not in text:
        if new + "\n" in text:
            print("  machine flags already clean")
            return False
        raise SystemExit("%s: no '+pan' override to remove" % path)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("  removed '+pan' from MACHINE_FLAGS_ARM64_BCM2837")
    return True


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1

    root = argv[1]
    header = find(root, "pexpert/pexpert/arm64/BCM2837.h")
    makedefs = find(root, "makedefs/MakeInc.def")

    changed = 0
    changed += clear_header(header)
    changed += clear_machine_flags(makedefs)

    # Verify rather than trust.
    if not re.search(r"^#define\s+__ARM_PAN_AVAILABLE__\s+0\s*$",
                     header.read_text(encoding="utf-8"), re.M):
        raise SystemExit("verification failed: header still enables PAN")
    if "+pan" in makedefs.read_text(encoding="utf-8"):
        raise SystemExit("verification failed: '+pan' still in MakeInc.def")

    print("done (%d change(s))" % changed)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
