"""Changes the UniXNU port made, plus the probes that finish the bisection.

The UniXNU items come from `UniXNU/UniXNU` -- see `docs/xnu/four-k-page-tables.md`
and `patches/xnu/non-apple-cache-monitor.patch`. This kernel is also built for
BCM2837 with 4 KiB pages on a non-Apple SoC, so the Apple-only paths have to be
guarded off.

1. osfmk/arm64/arm_vm_init.c -- guard ml_enable_monitor()

   arm_vm_init() calls it unconditionally and it is the first thing the function
   does after arm_vm_physmap_init(). On Apple silicon it turns on the page
   protection layer; this SoC has none. Guarding it did not move the wall, but it
   removes a call that is known to be wrong here.

2. osfmk/arm/arm_init.c -- checkpoint 45

   Between the planted probes 16 and 17 sit exactly two calls:

       processor_bootstrap();
       arm_vm_init(xmaxmem, args);

   45 goes between them. It fired, which cleared processor_bootstrap().

3. osfmk/arm64/arm_vm_init.c -- checkpoints 46..53

   Everything after arm_vm_init() was entered is still unverified. The probes cut
   the function into sections so one build answers several boots' worth of
   questions:

       45  fired    arm_vm_init() was entered
       -- first 325 lines --
       50  boot_tte assignment           physical memory sizing, early_random(),
                                         physmap_base and the physmap_end
                                         arithmetic are done
       -- page table page allocation --
       51  before the first init_ptpages argument list is evaluated
       52  after that init_ptpages() call  the primary page tables are built
       53  after arm_vm_prot_init()       segment lookup and protection init done
       46  fired?   before arm_set_user_tbi()
       -- the live table switch --
       47  fired?   after kva_active = TRUE
       48  fired?   before pmap_bootstrap()
       49  fired?   after pmap_bootstrap()
       17  silent   arm_vm_init() never returns

   52 is the interesting one: it isolates init_ptpages(), which walks the segment
   list and builds every L1/L2 entry the kernel will run on after it leaves the
   bootstrap tables.

Idempotent: each edit checks for its own result before applying.

    python apply_nonapple_guards.py <hakios/xnu-src root>
"""
import sys
from pathlib import Path

EXTERN_DECL = "\nextern void loader_checkpoint(uint64_t n);\n"

# (line substring, "before"|"after", probe number). Applied in one pass, so a line
# can carry a probe on each side.
VM_PROBES = [
    ("boot_tte = (tt_entry_t *)&bootstrap_pagetables;", "before", 50),
    ("init_ptpages(cpu_tte, gVirtBase, dynamic_memory_begin", "before", 51),
    ("init_ptpages(cpu_tte, gVirtBase, dynamic_memory_begin", "after", 52),
    ("arm_vm_prot_init(args);", "after", 53),
    ("arm_set_user_tbi();", "before", 46),
    ("kva_active = TRUE;", "after", 47),
    ("pmap_bootstrap(dynamic_memory_begin);", "before", 48),
    ("pmap_bootstrap(dynamic_memory_begin);", "after", 49),
]


def find(root, suffix):
    matches = [p for p in Path(root).rglob(suffix) if ".dSYM" not in str(p)]
    if not matches:
        raise SystemExit("not found under %s: %s" % (root, suffix))
    return matches[0]


def guard_monitor(path):
    text = path.read_text(encoding="utf-8")
    if "#if XNU_MONITOR\n\tml_enable_monitor();" in text:
        print("  already guarded: ml_enable_monitor")
        return False

    needle = "\tml_enable_monitor();\n"
    count = text.count(needle)
    if count != 1:
        raise SystemExit("%s: expected one '\\tml_enable_monitor();', found %d" % (path, count))

    text = text.replace(needle, "#if XNU_MONITOR\n\tml_enable_monitor();\n#endif\n", 1)
    path.write_text(text, encoding="utf-8")
    print("  guarded ml_enable_monitor()")
    return True


def add_extern(path):
    """loader_checkpoint() is declared in arm_init.c by the hakios patch, not here."""
    text = path.read_text(encoding="utf-8")
    if "extern void loader_checkpoint" in text:
        print("  extern already present")
        return False

    anchor = "#include <pexpert/arm64/boot.h>\n"
    if anchor not in text:
        raise SystemExit("%s: include anchor not found" % path)

    text = text.replace(anchor, anchor + EXTERN_DECL, 1)
    path.write_text(text, encoding="utf-8")
    print("  added extern loader_checkpoint()")
    return True


def add_vm_probes(path):
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")

    changed = 0
    out = []
    for line in lines:
        before, after = [], []
        for needle, position, number in VM_PROBES:
            if needle not in line:
                continue
            if "loader_checkpoint(%d)" % number in text:
                continue
            (before if position == "before" else after).append(number)

        for number in before:
            out.append("\tloader_checkpoint(%d);" % number)
            print("  added loader_checkpoint(%d)" % number)
            changed += 1
        out.append(line)
        for number in after:
            out.append("\tloader_checkpoint(%d);" % number)
            print("  added loader_checkpoint(%d)" % number)
            changed += 1

    if changed:
        path.write_text("\n".join(out), encoding="utf-8")
    return changed


def add_arm_init_probe(path):
    text = path.read_text(encoding="utf-8")
    if "loader_checkpoint(45)" in text:
        print("  already present: checkpoint 45")
        return False

    needle = "\tprocessor_bootstrap();\n"
    count = text.count(needle)
    if count != 1:
        raise SystemExit("%s: expected one '\\tprocessor_bootstrap();', found %d" % (path, count))

    text = text.replace(needle, "\tprocessor_bootstrap();\n\tloader_checkpoint(45);\n", 1)
    path.write_text(text, encoding="utf-8")
    print("  added loader_checkpoint(45) after processor_bootstrap()")
    return True


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1

    root = argv[1]
    print("patching under %s" % root)

    vm = find(root, "osfmk/arm64/arm_vm_init.c")
    init = find(root, "osfmk/arm/arm_init.c")

    changed = 0
    changed += guard_monitor(vm)
    changed += add_extern(vm)
    changed += add_vm_probes(vm)
    changed += add_arm_init_probe(init)

    # Verify rather than trust: a silent no-op would cost a full build and boot.
    vm_text = vm.read_text(encoding="utf-8")
    for number in (46, 47, 48, 49, 50, 51, 52, 53):
        if "loader_checkpoint(%d)" % number not in vm_text:
            raise SystemExit("verification failed: checkpoint %d missing" % number)
    for marker in ("extern void loader_checkpoint", "#if XNU_MONITOR"):
        if marker not in vm_text:
            raise SystemExit("verification failed in arm_vm_init.c: %s" % marker)

    if "loader_checkpoint(45)" not in init.read_text(encoding="utf-8"):
        raise SystemExit("verification failed in arm_init.c: checkpoint 45")

    print("done (%d change(s))" % changed)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
