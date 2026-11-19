"""Changes the UniXNU port made, plus the probes that finish the bisection.

Probe map (osfmk/arm64/arm_vm_init.c unless noted):

    45  arm_init.c, between processor_bootstrap() and arm_vm_init()   -- FIRES
    46  before arm_set_user_tbi()                                     -- fires
    47  after  kva_active = TRUE                                      -- SILENT
    48  before pmap_bootstrap()                                       -- silent
    49  after  pmap_bootstrap()
    50  before boot_tte = &bootstrap_pagetables
    51  before init_ptpages(cpu_tte, gVirtBase, ...)
    52  after  that init_ptpages()                                    -- fires
    53  after  arm_vm_prot_init()
    54  after  gPhysSize = mem_size = ...
    55  before physmap_base = phystokv(...)
    56  after  physmap_base += physmap_slide
    57  before the dynamic_memory_begin range check
    58  before gVirtBase = args->virtBase                            -- fires
    59  after  gPhysBase = args->physBase
    60  before gPhysSize = mem_size = ...
    61  after  arm_set_user_tbi()                                     -- fires
    62  after  arm_vm_physmap_init(args)                             -- SILENT
    63  after  set_mmu_ttb_alternate(...)
    64  after  set_mmu_ttb(invalid_ttep ...)
    65  inside arm_vm_physmap_init, after the first slide             -- fires
    66  after  arm_vm_page_granular_RWNX(...)                         -- fires
    67  after  the second slide                                       -- SILENT
    68  arm_vm_page_granular_prot entry                               -- fires
    69  before its block loop                                         -- fires
    70  after  its block loop                                         -- fires
    71  before the deferred-tte flush                                 -- fires

The function-call problem: 68-71 sit inside arm_vm_page_granular_prot(), which
arm_vm_physmap_init() calls three times -- first slide, RWNX remap, second slide.
A fixed probe number cannot say which call reached it, so 68-71 firing only proves
that *some* call got that far, quite possibly the two small ones that already
succeeded.

So probe 80 is indexed rather than fixed:

    loader_checkpoint(80 + ptov_index)

ptov_index is 0 for the first slide and 1 for the second. Boot with checkpoint=81
and only the second slide can fire it -- the one that fails.

Idempotent: each edit checks for its own result before applying.

    python apply_nonapple_guards.py <hakios/xnu-src root>
"""
import re
import sys
from pathlib import Path

EXTERN_DECL = ("\nextern void loader_checkpoint(uint64_t n);\n"
               "extern uint64_t loader_stub_slot[];\n")

STACK_MULTIPLIER = 4

# The call site, and the indexed probe that goes in front of it.
SLIDE_CALL = ("arm_vm_page_granular_prot(temp_ptov_table[ptov_index].va, "
              "temp_ptov_table[ptov_index].len, pa_offset, 1, pte_prot_APX, 1, granule);")
INDEXED_PROBE = "\tloader_checkpoint(80 + ptov_index);"

# (line substring, "before"|"after", probe number). Applied in one pass, so a line
# can carry a probe on each side.
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
    ("tt_entry_t *deferred_ttep_pair[2] = {NULL};", "after", 68),
    ("while ((_end - align_start) >= ARM_TT_L2_SIZE) {", "before", 69),
    ("if (align_start < _end) {", "before", 70),
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

EXPECTED = (46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57,
            58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71)


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
    if text.count(old) != 1:
        raise SystemExit("%s: expected one '%s'" % (path, old))
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("  KERNEL_STACK_MULTIPLIER -> %d" % STACK_MULTIPLIER)
    return True


def raise_int_stacks(path):
    """The stack arm_init() actually runs on -- start.s sets SP to intstack_top."""
    text = path.read_text(encoding="utf-8")
    changed = False
    for name in ("INTSTACK_SIZE", "EXCEPSTACK_SIZE"):
        pattern = re.compile(r"^#define\s+%s\s+\(4\*4096\)" % name, re.M)
        replacement = "#define %-23s (16*4096)" % name
        if re.search(re.escape(replacement), text):
            print("  %s already 64 KiB" % name)
            continue
        if not pattern.search(text):
            raise SystemExit("%s: no '#define %s (4*4096)'" % (path, name))
        text = pattern.sub(replacement, text, count=1)
        print("  %s -> 64 KiB" % name)
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
    return changed


def guard_monitor(path):
    text = path.read_text(encoding="utf-8")
    if "#if XNU_MONITOR\n\tml_enable_monitor();" in text:
        print("  already guarded: ml_enable_monitor")
        return False
    needle = "\tml_enable_monitor();\n"
    if text.count(needle) != 1:
        raise SystemExit("%s: expected one ml_enable_monitor call" % path)
    path.write_text(text.replace(needle, "#if XNU_MONITOR\n\tml_enable_monitor();\n#endif\n", 1),
                    encoding="utf-8")
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
    path.write_text(text.replace(anchor, anchor + EXTERN_DECL, 1), encoding="utf-8")
    print("  added extern loader_checkpoint()")
    return True


# (line substring, "before"|"after", base number). The probe is emitted as
# loader_checkpoint(base + ptov_index), so it names the call it fired on.
SLIDE_INDEXED = [
    ("if (__improbable(ptov_index >= PTOV_TABLE_SIZE)) {", "before", 82),
    ("assert((orig_va & ARM_PGMASK) == 0);", "after", 84),
    ("assert((temp_ptov_table[ptov_index].va & ARM_PGMASK) == 0);", "after", 86),
    ("temp_ptov_table[ptov_index].len = round_page(len);", "after", 88),
]


def add_slide_indexed_probes(path):
    """Probes inside arm_vm_physmap_slide, numbered by ptov_index.

    Checkpoint 81 showed the second slide never reached arm_vm_page_granular_prot,
    so the fault is in the arithmetic above the call -- and that arithmetic runs
    once per slide, which a fixed probe number cannot distinguish.
    """
    text = path.read_text(encoding="utf-8")
    changed = 0
    out = []
    for line in text.split("\n"):
        before, after = [], []
        for needle, position, base in SLIDE_INDEXED:
            if needle not in line:
                continue
            if "loader_checkpoint(%d + ptov_index)" % base in text:
                continue
            (before if position == "before" else after).append(base)
        for base in before:
            out.append("\tloader_checkpoint(%d + ptov_index);" % base)
            print("  added indexed probe %d + ptov_index" % base)
            changed += 1
        out.append(line)
        for base in after:
            out.append("\tloader_checkpoint(%d + ptov_index);" % base)
            print("  added indexed probe %d + ptov_index" % base)
            changed += 1
    if changed:
        path.write_text("\n".join(out), encoding="utf-8")
    return changed


def relax_orig_va_assert(path):
    """Turn the orig_va alignment assert into a probe.

    checkpoints 83 and 85 disagree with the arithmetic: 83 (entered the body) fires,
    85 (the assert just below it) does not, which says
    assert((orig_va & ARM_PGMASK) == 0) failed. But orig_va is
    args->topOfKernelData - gPhysBase + gVirtBase, and topOfKernelData comes from
    ALIGN_VALUE(..., SIZE_16KB) while gPhysBase and gVirtBase both end in 0x4000, so
    it should be aligned.

    Rather than trust either the reasoning or the assert, replace it with a probe
    that reports which way it actually went: 112 + ptov_index when the low bits are
    non-zero, nothing otherwise. The assert itself is left out so the slide can
    continue and later probes become meaningful.
    """
    text = path.read_text(encoding="utf-8")
    old_assert = "\tassert((orig_va & ARM_PGMASK) == 0);\n"
    new_code = ("\tif ((orig_va & ARM_PGMASK) != 0) {\n"
                "\t\tloader_stub_slot[3] = orig_va;\n"
                "\t\tloader_checkpoint(112 + ptov_index);\n"
                "\t}\n")
    if new_code in text:
        print("  orig_va assert already relaxed")
        return False
    if text.count(old_assert) != 1:
        raise SystemExit("%s: expected one orig_va assert, found %d"
                         % (path, text.count(old_assert)))
    path.write_text(text.replace(old_assert, new_code, 1), encoding="utf-8")
    print("  orig_va assert -> probe 112 + ptov_index")
    return True


def add_indexed_probe(path):
    """loader_checkpoint(80 + ptov_index) in front of the granular_prot call."""
    text = path.read_text(encoding="utf-8")
    if "loader_checkpoint(80 + ptov_index)" in text:
        print("  indexed probe 80 already present")
        return False
    if text.count(SLIDE_CALL) != 1:
        raise SystemExit("%s: expected one granular_prot call site, found %d"
                         % (path, text.count(SLIDE_CALL)))
    text = text.replace(SLIDE_CALL, INDEXED_PROBE + "\n\t" + SLIDE_CALL, 1)
    path.write_text(text, encoding="utf-8")
    print("  added indexed probe: loader_checkpoint(80 + ptov_index)")
    return True


def add_vm_probes(path):
    text = path.read_text(encoding="utf-8")
    changed = 0
    out = []
    for line in text.split("\n"):
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
    if text.count(needle) != 1:
        raise SystemExit("%s: expected one processor_bootstrap() call" % path)
    path.write_text(text.replace(needle, "\tprocessor_bootstrap();\n\tloader_checkpoint(45);\n", 1),
                    encoding="utf-8")
    print("  added loader_checkpoint(45)")
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
    changed += add_indexed_probe(vm)
    changed += add_slide_indexed_probes(vm)
    changed += relax_orig_va_assert(vm)
    changed += add_vm_probes(vm)
    changed += add_arm_init_probe(init)

    vm_text = vm.read_text(encoding="utf-8")
    missing = [n for n in EXPECTED if "loader_checkpoint(%d)" % n not in vm_text]
    if missing:
        raise SystemExit("verification failed: probes missing %s" % missing)
    for marker in ("extern void loader_checkpoint", "#if XNU_MONITOR",
                   "loader_checkpoint(80 + ptov_index)",
                   "loader_checkpoint(82 + ptov_index)",
                   "loader_checkpoint(84 + ptov_index)",
                   "loader_checkpoint(86 + ptov_index)",
                   "loader_checkpoint(88 + ptov_index)",
                   "loader_checkpoint(112 + ptov_index)",
                   "loader_stub_slot[3] = orig_va"):
        if marker not in vm_text:
            raise SystemExit("verification failed in arm_vm_init.c: %s" % marker)
    if "loader_checkpoint(45)" not in init.read_text(encoding="utf-8"):
        raise SystemExit("verification failed in arm_init.c")
    if "KERNEL_STACK_MULTIPLIER (%d)" % STACK_MULTIPLIER not in vmp.read_text(encoding="utf-8"):
        raise SystemExit("verification failed in vm_param.h")

    print("done (%d change(s), %d fixed probes + indexed 80)"
          % (changed, len(EXPECTED)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
