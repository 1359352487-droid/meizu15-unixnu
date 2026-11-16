"""Two changes the UniXNU port made that this build is missing.

Both come from `UniXNU/UniXNU` -- see `docs/xnu/four-k-page-tables.md` and
`patches/xnu/non-apple-cache-monitor.patch`. The reasoning is the same here: this
kernel is built for BCM2837 with 4 KiB pages, on a SoC that is not Apple silicon,
so the Apple-only paths in arm_vm_init() and start.s have to be guarded off.

1. osfmk/arm64/arm_vm_init.c -- guard ml_enable_monitor()

   arm_vm_init() calls it unconditionally and it is the first thing the function
   does after arm_vm_physmap_init():

       arm_vm_physmap_init(args);
       set_mmu_ttb_alternate(cpu_ttep & TTBR_BADDR_MASK);
       ml_enable_monitor();                 <- here
       set_mmu_ttb(invalid_ttep & TTBR_BADDR_MASK);

   On Apple silicon that turns on the page protection layer. On this SoC there is
   no PPL, and the call is not merely redundant: it sits inside the one function
   the boot never gets past.

2. osfmk/arm/arm_init.c -- one more probe, checkpoint 45

   The planted probes stop at 16, and two calls remain between 16 and 17:

       cp16 *
            processor_bootstrap();
            arm_vm_init(xmaxmem, args);     17
       cp17 silent

   45 goes between them, so a boot that still fails tells us which of the two is
   at fault instead of leaving them merged.

The script is idempotent: it checks for its own edit before applying it, so a
tree that already has the guard is left alone.

    python apply_nonapple_guards.py <hakios/xnu-src root>
"""
import sys
from pathlib import Path


def find(root, suffix):
    matches = [p for p in Path(root).rglob(suffix) if ".dSYM" not in str(p)]
    if not matches:
        raise SystemExit("not found under %s: %s" % (root, suffix))
    return matches[0]


def guard_monitor(path):
    text = path.read_text(encoding="utf-8")
    if "#if XNU_MONITOR\n\tml_enable_monitor();" in text:
        print("  already guarded: %s" % path)
        return False

    needle = "\tml_enable_monitor();\n"
    count = text.count(needle)
    if count != 1:
        raise SystemExit(
            "%s: expected exactly one '\\tml_enable_monitor();', found %d" % (path, count)
        )

    text = text.replace(
        needle,
        "#if XNU_MONITOR\n"
        "\tml_enable_monitor();\n"
        "#endif\n",
        1,
    )
    path.write_text(text, encoding="utf-8")
    print("  guarded ml_enable_monitor() in %s" % path)
    return True


def add_probe(path):
    text = path.read_text(encoding="utf-8")
    if "loader_checkpoint(45)" in text:
        print("  already present: checkpoint 45")
        return False

    needle = "\tprocessor_bootstrap();\n"
    count = text.count(needle)
    if count != 1:
        raise SystemExit(
            "%s: expected exactly one '\\tprocessor_bootstrap();', found %d" % (path, count)
        )

    text = text.replace(
        needle,
        "\tprocessor_bootstrap();\n"
        "\tloader_checkpoint(45);\n",
        1,
    )
    path.write_text(text, encoding="utf-8")
    print("  added loader_checkpoint(45) after processor_bootstrap() in %s" % path)
    return True


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1

    root = argv[1]
    print("patching under %s" % root)

    changed = 0
    changed += guard_monitor(find(root, "osfmk/arm64/arm_vm_init.c"))
    changed += add_probe(find(root, "osfmk/arm/arm_init.c"))

    # Verify rather than trust: a silent no-op here would cost a full build+boot.
    monitor = find(root, "osfmk/arm64/arm_vm_init.c").read_text(encoding="utf-8")
    if "#if XNU_MONITOR\n\tml_enable_monitor();" not in monitor:
        raise SystemExit("verification failed: monitor guard not in place")

    init = find(root, "osfmk/arm/arm_init.c").read_text(encoding="utf-8")
    if "loader_checkpoint(45)" not in init:
        raise SystemExit("verification failed: checkpoint 45 not in place")

    print("done (%d change(s))" % changed)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
