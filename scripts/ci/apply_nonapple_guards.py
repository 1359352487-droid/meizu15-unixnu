"""Changes the UniXNU port made, plus the probes that finish the bisection.

The UniXNU items come from `UniXNU/UniXNU` -- see `docs/xnu/four-k-page-tables.md`
and `patches/xnu/non-apple-cache-monitor.patch`. This kernel is also built for
BCM2837 with 4 KiB pages on a non-Apple SoC, so the Apple-only paths have to be
guarded off.

Probe map (all inside osfmk/arm64/arm_vm_init.c unless noted):

    45  arm_init.c, between processor_bootstrap() and arm_vm_init()   -- fires
    46  before arm_set_user_tbi()                                     -- silent
    47  after kva_active = TRUE                                       -- silent
    48  before pmap_bootstrap()                                       -- silent
    49  after pmap_bootstrap()
    50  before boot_tte = &bootstrap_pagetables                       -- silent
    51  before init_ptpages(cpu_tte, gVirtBase, ...)                  -- silent
    52  after that init_ptpages()                                     -- silent
    53  after arm_vm_prot_init()

Everything from 46 rightwards is silent, and 50 is the earliest of them, so the
kernel stops inside the first 98 lines of the function. Four more probes cut that
stretch up:

    45 *
        gVirtBase = args->virtBase;
        gPhysBase = args->physBase;
        real_phys_size = args->memSize;
        gPhysSize = mem_size = ((gPhysBase + args->memSize) & ~PAGE_MASK) - gPhysBase;
    54  <- after the size arithmetic
        if (mem_size >= ((VM_MAX_KERNEL_ADDRESS - VM_MIN_KERNEL_ADDRESS) / 2))
            panic("Unsupported memory configuration %lx", mem_size);
    55  <- before the physmap base
        physmap_base = phystokv(args->topOfKernelData);
        physmap_slide = early_random() & ARM64_PHYSMAP_SLIDE_MASK & ~PAGE_MASK;
        assert(physmap_slide < ARM64_PHYSMAP_SLIDE_RANGE);
        physmap_base += physmap_slide;
    56  <- after the slide
        static_memory_end = physmap_base + mem_size + (PTOV_TABLE_SIZE * ARM_TT_TWIG_SIZE);
        physmap_end = physmap_base + real_phys_size + (PTOV_TABLE_SIZE * ARM_TT_TWIG_SIZE);
        dynamic_memory_begin = static_memory_end;
    57  <- before the range check
        if (dynamic_memory_begin > VM_MAX_KERNEL_ADDRESS)
            panic("Unsupported memory configuration %lx", mem_size);
    50  <- silent

55 and 56 bracket phystokv() and early_random(), which are the only two calls in
the whole stretch -- everything else is arithmetic on boot_args.

Idempotent: each edit checks for its own result before applying.

    python apply_nonapple_guards.py <hakios/xnu-src root>
"""
import sys
from pathlib import Path

EXTERN_DECL = "\nextern void loader_checkpoint(uint64_t n);\n"

# (line substring, "before"|"after", probe number). Applied in one pass, so a line
# can carry a probe on each side.
VM_PROBES = [
    ("gPhysSize = mem_size", "after", 54),
    ("physmap_base = phystokv(args->topOfKernelData);", "before", 55),
    ("physmap_base += physmap_slide;", "after", 56),
    ("if (dynamic_memory_begin > VM_MAX_KERNEL_ADDRESS)", "before", 57),
    ("boot_tte = (tt_entry_t *)&bootstrap_pagetables;", "before", 50),
    ("init_ptpages(cpu_tte, gVirtBase, dynamic_memory_begin", "before", 51),
    ("init_ptpages(cpu_tte, gVirtBase, dynamic_memory_begin", "after", 52),
    ("arm_vm_prot_init(args);", "after", 53),
    ("arm_set_user_tbi();", "before", 46),
    ("kva_active = TRUE;", "after", 47),
    ("pmap_bootstrap(dynamic_memory_begin);", "before", 48),
    ("pmap_bootstrap(dynamic_memory_begin);", "after", 49),
]

EXPECTED = (46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57)


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
    missing = [n for n in EXPECTED if "loader_checkpoint(%d)" % n not in vm_text]
    if missing:
        raise SystemExit("verification failed: probes missing %s" % missing)
    for marker in ("extern void loader_checkpoint", "#if XNU_MONITOR"):
        if marker not in vm_text:
            raise SystemExit("verification failed in arm_vm_init.c: %s" % marker)

    if "loader_checkpoint(45)" not in init.read_text(encoding="utf-8"):
        raise SystemExit("verification failed in arm_init.c: checkpoint 45")

    print("done (%d change(s), %d probes)" % (changed, len(EXPECTED)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
