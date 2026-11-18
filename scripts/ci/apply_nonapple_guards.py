"""Changes the UniXNU port made, plus the probes that finish the bisection.

Probe map (osfmk/arm64/arm_vm_init.c unless noted):

    45  arm_init.c, between processor_bootstrap() and arm_vm_init()   -- FIRES
    46..53                                                  -- all silent
    54  after gPhysSize = mem_size = ...                     -- SILENT
    55..57  later in the function                           -- silent

45 fires and 54 does not, and everything between them is four field reads and one
integer arithmetic. Two explanations were tested and cleared:

  * stack -- KERNEL_STACK_MULTIPLIER went 1 -> 4 (16 KiB -> 64 KiB) and probe 54
    stayed silent, so it is not stack exhaustion.
  * __DATA_CONST write permission -- the four globals live there:
        gVirtBase      0xFFFFFFF0071B5110  __DATA_CONST
        gPhysBase      0xFFFFFFF0071B5100  __DATA_CONST
        gPhysSize      0xFFFFFFF0071B5120  __DATA_CONST
        real_phys_size 0xFFFFFFF0071B5108  __DATA_CONST
    but ARM_TTE_BOOT_BLOCK carries no AP field, so AP = 0 = AP_RWNA and the
    bootstrap tables are read-write. Not it either.

So the four assignments are cut into four sections:

    58  before gVirtBase = args->virtBase;
    59  after  gPhysBase = args->physBase;
    60  before gPhysSize = mem_size = ...
    54  after  gPhysSize = ...

  reason 2 at 58   arm_vm_init() body was entered; the fault is at or after 59
  reason 2 at 59   gVirtBase and gPhysBase stored fine
  reason 2 at 60   real_phys_size stored fine
  reason 2 at 54   only gPhysSize's arithmetic is left
  silence at 58    the body was never entered -- prologue or the call itself

Idempotent: each edit checks for its own result before applying.

    python apply_nonapple_guards.py <hakios/xnu-src root>
"""
import re
import sys
from pathlib import Path

EXTERN_DECL = "\nextern void loader_checkpoint(uint64_t n);\n"

STACK_MULTIPLIER = 4

VM_PROBES = [
    ("gVirtBase = args->virtBase;", "before", 58),
    ("gPhysBase = args->physBase;", "after", 59),
    ("gPhysSize = mem_size", "before", 60),
    ("gPhysSize = mem_size", "after", 54),
    ("physmap_base = phystokv(args->topOfKernelData);", "before", 55),
    ("physmap_base += physmap_slide;", "after", 56),
    ("if (dynamic_memory_begin > VM_MAX_KERNEL_ADDRESS)", "before", 57),
    ("boot_tte = (tt_entry_t *)&bootstrap_pagetables;", "before", 50),
    ("init_ptpages(cpu_tte, gVirtBase, dynamic_memory_begin", "before", 51),
    ("init_ptpages(cpu_tte, gVirtBase, dynamic_memory_begin", "after", 52),
    ("arm_vm_prot_init(args);", "after", 53),
    # The table switch. arm_set_user_tbi() writes TCR_EL1's TBI bits, then
    # arm_vm_physmap_init() builds the real tables, set_mmu_ttb() installs them and
    # kva_active flips the kernel onto them. Probes 46 and 47 are 12 lines apart and
    # the fault is in between, so this stretch needs its own split.
    # Inside arm_vm_physmap_init(), the three statements that do the mapping. This
    # function is static and has no symbol, so probes are the only way in. Its
    # #else branch is the one that compiles here: BCM2837 does not define
    # XNU_MONITOR, so it makes two slide calls rather than the monitor branch's
    # nine -- which would overrun PTOV_TABLE_SIZE (8) and panic quietly.
    # Inside arm_vm_page_granular_prot(), which is where a 752 MB slide has to pick
    # block mappings. Entry, before the block loop, and after it -- not inside the
    # loop, which would be hundreds of calls.
    ("tt_entry_t *deferred_ttep_pair[2] = {NULL};", "after", 68),
    ("while ((_end - align_start) >= ARM_TT_L2_SIZE) {", "before", 69),
    ("if (align_start < _end) {", "before", 70),
    # After the trailing partial-block helper, before the deferred-tte flush.
    # The flush path is #if DEBUG || DEVELOPMENT and this is DEVELOPMENT, so it
    # runs here and nowhere else in the slides that already passed.
    ("if (deferred_ttep_pair[0] != NULL) {", "before", 71),
    ("arm_vm_page_granular_RWNX(end_kern, phystokv(args->topOfKernelData) - end_kern,", "before", 65),
    ("/* Device Tree (if not locked down), RAM Disk (if present), bootArgs */", "after", 66),
    ("real_avail_end - args->topOfKernelData, AP_RWNA, ARM64_GRANULE_ALLOW_BLOCK", "after", 67),
    ("arm_set_user_tbi();", "after", 61),
    ("arm_vm_physmap_init(args);", "after", 62),
    ("set_mmu_ttb_alternate(cpu_ttep & TTBR_BADDR_MASK);", "after", 63),
    ("set_mmu_ttb(invalid_ttep & TTBR_BADDR_MASK);", "after", 64),
    ("arm_set_user_tbi();", "before", 46),
    ("kva_active = TRUE;", "after", 47),
    ("pmap_bootstrap(dynamic_memory_begin);", "before", 48),
    ("pmap_bootstrap(dynamic_memory_begin);", "after", 49),
]

EXPECTED = (46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71)


def find(root, suffix):
    matches = [p for p in Path(root).rglob(suffix) if ".dSYM" not in str(p)]
    if not matches:
        raise SystemExit("not found under %s: %s" % (root, suffix))
    return matches[0]


def raise_int_stacks(path):
    """The stack arm_init() actually runs on.

    start.s sets SP to intstack_top before handing control to arm_init():

        adrp x0, EXT(intstack_top)@page
        add  x0, x0, EXT(intstack_top)@pageoff
        msr  SPSel, #0
        mov  sp, x0
        adrp lr, EXT(arm_init)@page

    So the whole bring-up -- arm_init(), its 0x480-byte copy of boot_args, and
    arm_vm_init() with its large frame -- lives on INTSTACK_SIZE, not on the
    per-thread KERNEL_STACK_SIZE. Growing KERNEL_STACK_MULTIPLIER therefore cannot
    help, which is exactly what the last boot showed.

    A prologue that pushes past the bottom of a guard-paged stack faults on the
    first store, before any statement in the body runs. Probe 58 sits on that first
    statement and never fired.
    """
    text = path.read_text(encoding="utf-8")
    changed = False
    for name in ("INTSTACK_SIZE", "EXCEPSTACK_SIZE"):
        pattern = re.compile(r"^#define\s+%s\s+\(4\*4096\)" % name, re.M)
        replacement = "#define %-23s (16*4096)" % name
        if "(16*4096)" in text and re.search(re.escape(replacement), text):
            print("  %s already 64 KiB" % name)
            continue
        if not pattern.search(text):
            raise SystemExit("%s: no '#define %s (4*4096)' to replace" % (path, name))
        text = pattern.sub(replacement, text, count=1)
        print("  %s -> 64 KiB" % name)
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
    return changed

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
    print("  KERNEL_STACK_MULTIPLIER -> %d" % STACK_MULTIPLIER)
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
    changed += raise_int_stacks(vmp)
    changed += guard_monitor(vm)
    changed += add_extern(vm)
    changed += add_vm_probes(vm)
    changed += add_arm_init_probe(init)

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

    print("done (%d change(s), %d probes)" % (changed, len(EXPECTED)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
