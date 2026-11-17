"""Changes the UniXNU port made, plus the probes that finish the bisection.

The UniXNU items come from `UniXNU/UniXNU` -- see `docs/xnu/four-k-page-tables.md`
and `patches/xnu/non-apple-cache-monitor.patch`. This kernel is also built for
BCM2837 with 4 KiB pages on a non-Apple SoC, so the Apple-only paths have to be
guarded off.

Probe map (all inside osfmk/arm64/arm_vm_init.c unless noted):

    45  arm_init.c, between processor_bootstrap() and arm_vm_init()   -- FIRES
    46  before arm_set_user_tbi()                                     -- silent
    47  after kva_active = TRUE                                       -- silent
    48  before pmap_bootstrap()                                       -- silent
    49  after pmap_bootstrap()
    50  before boot_tte = &bootstrap_pagetables                       -- silent
    51  before init_ptpages(cpu_tte, gVirtBase, ...)                  -- silent
    52  after that init_ptpages()                                     -- silent
    53  after arm_vm_prot_init()
    54  after gPhysSize = mem_size = ...                              -- SILENT
    55  before physmap_base = phystokv(...)                           -- silent
    56  after physmap_base += physmap_slide                           -- silent
    57  before the dynamic_memory_begin range check

54 was the last split worth making, and it came back silent. That is the striking
part: between 45 firing and 54 sitting silent there is nothing but four field
reads and integer arithmetic --

    gVirtBase      = args->virtBase;
    gPhysBase      = args->physBase;
    real_phys_size = args->memSize;
    gPhysSize = mem_size = ((gPhysBase + args->memSize) & ~PAGE_MASK) - gPhysBase;

-- with no calls, no loops and no branches. A function that faults there has not
failed at its work; it has failed at arriving. Two things can do that:

  * the stack. XNU's default kernel stack is 4*4096 = 16 KiB, and its own comment
    in osfmk/mach/arm/vm_param.h warns that -O0 builds overflow it:
        "adding -DKERNEL_STACK_MULTIPLIER=2 helps avoid kernel stack overflows
         when compiling with -O0"
    This build is KERNEL_CONFIGS=DEVELOPMENT. arm_init() additionally keeps the
    0x480-byte boot_args copy on its stack and hands its address to arm_vm_init(),
    which is a large function. Probe 45 runs in arm_init's shallower frame and
    fires; 54 runs one frame deeper and does not. That pattern fits.

  * args. If the pointer were bad, the first read at 1831 would fault, and 45
    would still fire because it happens before the call.

This change raises the stack fourfold (64 KiB) so the two explanations separate in
one boot. Nothing about the ABI depends on it -- KERNEL_STACK_SIZE only sizes the
allocation.

Idempotent: each edit checks for its own result before applying.

    python apply_nonapple_guards.py <hakios/xnu-src root>
"""
import sys
from pathlib import Path

EXTERN_DECL = "\nextern void loader_checkpoint(uint64_t n);\n"

STACK_MULTIPLIER = 4

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


def raise_kernel_stack(path):
    text = path.read_text(encoding="utf-8")
    old = "#define KERNEL_STACK_MULTIPLIER (1)"
    new = "#define KERNEL_STACK_MULTIPLIER (%d)" % STACK_MULTIPLIER
    if new in text:
        print("  stack already x%d" % STACK_MULTIPLIER)
        return False
    count = text.count(old)
    if count != 1:
        raise SystemExit("%s: expected one '%s', found %d" % (path, old, count))
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("  KERNEL_STACK_MULTIPLIER -> %d (stack %d KiB)"
          % (STACK_MULTIPLIER, 4 * 4 * STACK_MULTIPLIER))
    return True


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
    vmp = find(root, "osfmk/mach/arm/vm_param.h")

    changed = 0
    changed += raise_kernel_stack(vmp)
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

    if "KERNEL_STACK_MULTIPLIER (%d)" % STACK_MULTIPLIER not in vmp.read_text(encoding="utf-8"):
        raise SystemExit("verification failed in vm_param.h: stack multiplier")

    print("done (%d change(s), %d probes, stack x%d)"
          % (changed, len(EXPECTED), STACK_MULTIPLIER))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
