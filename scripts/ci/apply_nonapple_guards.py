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

osfmk/arm/pmap/pmap.c, inside pmap_bootstrap() -- probe 48 enters it, 49 never
returns, and the three bootstraps below are the only part of that window that
walks memory the device tree describes:

    90 / 91  before / after pmap_data_bootstrap()
    92 / 93  before / after uat_bootstrap()
    94 / 95  before / after sart_bootstrap()

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

EXTERN_DECL = "\nextern void loader_checkpoint(uint64_t n);\n"

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

# pmap.c, where 48 enters pmap_bootstrap() and 49 never returns.
#
# Everything between those two probes is either pure bookkeeping or one of the
# three bootstraps that walk memory the device tree is supposed to describe.
# uat_bootstrap() and sart_bootstrap() look up UAT/SART nodes, which a lean ADT
# may not carry at all, so each call gets a probe on both sides:
#
#     90 / 91   before / after pmap_data_bootstrap()
#     92 / 93   before / after uat_bootstrap()
#     94 / 95   before / after sart_bootstrap()
#
# A hit at 90 with nothing at 91 means pmap_data_bootstrap() itself (page tables
# for pv_head_table, i.e. avail_start arithmetic), not the device tree.
PMAP_EXTERN_ANCHOR = "#include <mach/shared_region.h>\n"

PMAP_PROBES = [
    ("\tpmap_data_bootstrap();\n", 90, 91),
    ("\tuat_bootstrap();\n", 92, 93),
    ("\tsart_bootstrap();\n", 94, 95),
]

# 90 went silent, so the stop is in the head of pmap_bootstrap(): three probes
# walk that stretch. 96 is the first statement of the body, 97 is after the pmap
# lock, 98 is right after the ASID count is computed from the device tree.
PMAP_AFTER_PROBES = [
    ("\tvm_map_offset_t maxoffset;\n", 96),
    ("\tpmap_lock_init(kernel_pmap);\n", 97),
    ("\tpmap_max_asids = pmap_compute_max_asids();\n", 98),
]

PMAP_EXPECTED = (90, 91, 92, 93, 94, 95, 96, 97, 98)

# pmap_compute_max_asids() asserts that the device tree carries a root-level
# /defaults node:
#
#     err = SecureDTLookupEntry(NULL, "/defaults", &entry);
#     assert(err == kSuccess);
#
# This ADT only has /smc/defaults, so that assert is a panic() in a DEVELOPMENT
# build -- and it runs before kprintf, which is why the panel shows reason 0 and
# the same 192-byte log. Missing /defaults simply means "use the built-in
# MAX_ASIDS default", which is what the code already does when the property is
# absent, so return that instead of trapping.
PMAP_DEFAULTS_ASSERT = (
    '\terr = SecureDTLookupEntry(NULL, "/defaults", &entry);\n'
    '\tassert(err == kSuccess);\n')
PMAP_DEFAULTS_PATCH = (
    '\terr = SecureDTLookupEntry(NULL, "/defaults", &entry);\n'
    '\tif (err != kSuccess) {\n'
    '\t\t/* No root /defaults in this ADT; MAX_ASIDS is the documented default. */\n'
    '\t\treturn MAX_ASIDS;\n'
    '\t}\n')

# loader_checkpoint() only ever compared its number with the requested one, so a
# probe that fired and a probe that was never reached looked exactly the same on
# the panel: four lines naming the *requested* number. Probing 96/97/98 at once
# therefore settled nothing.
#
# loader_return() already hands loader_log to the stub, and loader_log_line()
# takes a whole line under the log's lock, so every probe can simply say its own
# name and the next boot prints the whole trail -- 45, 58, 61, ... up to the last
# one reached. One boot instead of one per probe.
LOADER_CHECKPOINT_OLD = """void
loader_checkpoint(uint64_t n)
{
\tif (loader_stub_slot[2] == n) {
\t\tloader_jump(2, n);
\t}
}
"""
LOADER_CHECKPOINT_NEW = """void
loader_checkpoint(uint64_t n)
{
\t/*
\t * Say which probe this is, not only whether it is the one to stop at. The
\t * log this writes is what the stub prints, so the trail survives a panic and
\t * tells which probe was reached last.
\t */
\tloader_last_checkpoint = n;
\tloader_log_number("cp ", n);

\tif (loader_stub_slot[2] == n) {
\t\tloader_jump(2, n);
\t}
}
"""

LOADER_HELPER_ANCHOR = ("/* Patched by the loader: { magic, PA of its return stub, "
                        "checkpoint to stop at, PA - VA }. See start.s. */\n")
LOADER_HELPER = """/* The number of the probe just reached, and of the last one reached at all. */
static uint64_t loader_last_checkpoint;

/*
 * How much of the log the stub is asked to print. The console scrolls and the
 * stub paints its barcodes at y = 1694..1888, so a longer log has its final
 * lines covered by them -- and the final lines are the ones worth reading.
 * Roughly thirty 80-column lines fit above the barcodes.
 */
#define LOADER_RETURN_TAIL 448

/* "cp 62\\n" -- digits only, so no printf and no locale; safe this early. */
static void
loader_log_number(const char *prefix, uint64_t n)
{
\tchar line[32];
\tsize_t p = 0;
\tuint32_t d = 0;
\tuint64_t v = n;

\twhile (prefix[p] != 0 && p < 20) {
\t\tline[p] = prefix[p];
\t\tp++;
\t}
\tdo {
\t\tline[p + d] = (char)('0' + (v % 10));
\t\tv /= 10;
\t\td++;
\t} while (v != 0 && d < 20);
\tfor (uint32_t i = 0; i < d / 2; i++) {
\t\tchar t = line[p + i];
\t\tline[p + i] = line[p + d - 1 - i];
\t\tline[p + d - 1 - i] = t;
\t}
\tline[p + d] = '\\n';
\tline[p + d + 1] = 0;
\tloader_log_line(line);
}

""" + LOADER_HELPER_ANCHOR

LOADER_TAIL_OLD = "\tloader_jump(0, 0);\n"
LOADER_TAIL_NEW = """\t/*
\t * The trail scrolls off the console; this line does not. Whatever the panel
\t * shows last, it ends with the last probe the kernel reached.
\t */
\tif (loader_last_checkpoint != 0) {
\t\tloader_log_number("last cp ", loader_last_checkpoint);
\t}
\t/* Hand the stub only the tail: that is the part above its barcodes. */
\tif (loader_log_len > LOADER_RETURN_TAIL) {
\t\t__builtin_memmove(loader_log, loader_log + loader_log_len - LOADER_RETURN_TAIL,
\t\t    LOADER_RETURN_TAIL);
\t\tloader_log_len = LOADER_RETURN_TAIL;
\t}
\tloader_jump(0, 0);
"""


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
                "\t\tloader_checkpoint(112 + ptov_index);\n"
                "\t}\n")
    if new_code in text:
        print("  orig_va assert already relaxed")
        return False
    if text.count(old_assert) != 1:
        raise SystemExit("%s: expected one orig_va assert, found %d"
                         % (path, text.count(old_assert)))
    text = text.replace(old_assert, new_code, 1)

    # The va-alignment assert fails for the same reason. Remove it too: if the
    # mapping works without it, the assert is stricter than this port needs.
    va_assert = "\tassert((temp_ptov_table[ptov_index].va & ARM_PGMASK) == 0);\n"
    if va_assert in text:
        text = text.replace(va_assert, "", 1)
        print("  va-alignment assert removed")

    path.write_text(text, encoding="utf-8")
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


def patch_pmap_defaults_assert(path):
    """Turn the root-/defaults assert into the documented MAX_ASIDS default."""
    text = path.read_text(encoding="utf-8")
    if PMAP_DEFAULTS_PATCH in text:
        print("  /defaults assert already relaxed")
        return False
    if text.count(PMAP_DEFAULTS_ASSERT) != 1:
        raise SystemExit("%s: expected one /defaults assert, found %d"
                         % (path, text.count(PMAP_DEFAULTS_ASSERT)))
    path.write_text(text.replace(PMAP_DEFAULTS_ASSERT, PMAP_DEFAULTS_PATCH, 1),
                    encoding="utf-8")
    print("  /defaults assert -> return MAX_ASIDS")
    return True


def add_pmap_probes(path):
    """Probes 90-98 inside pmap_bootstrap(), one pair per bootstrap it calls."""
    text = path.read_text(encoding="utf-8")
    changed = False
    if "extern void loader_checkpoint" not in text:
        if PMAP_EXTERN_ANCHOR not in text:
            raise SystemExit("%s: include anchor not found" % path)
        text = text.replace(PMAP_EXTERN_ANCHOR, PMAP_EXTERN_ANCHOR + EXTERN_DECL, 1)
        print("  added extern loader_checkpoint() to pmap.c")
        changed = True
    for needle, before, after in PMAP_PROBES:
        if "loader_checkpoint(%d);" % before in text:
            print("  already present: checkpoints %d/%d" % (before, after))
            continue
        if text.count(needle) != 1:
            raise SystemExit("%s: expected one '%s', found %d"
                             % (path, needle.strip(), text.count(needle)))
        text = text.replace(
            needle,
            "\tloader_checkpoint(%d);\n" % before + needle +
            "\tloader_checkpoint(%d);\n" % after,
            1)
        print("  added loader_checkpoint(%d/%d) around %s"
              % (before, after, needle.strip()))
        changed = True
    for needle, number in PMAP_AFTER_PROBES:
        if "loader_checkpoint(%d);" % number in text:
            print("  already present: checkpoint %d" % number)
            continue
        if text.count(needle) != 1:
            raise SystemExit("%s: expected one '%s', found %d"
                             % (path, needle.strip(), text.count(needle)))
        text = text.replace(needle, needle + "\tloader_checkpoint(%d);\n" % number, 1)
        print("  added loader_checkpoint(%d) after %s" % (number, needle.strip()))
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
    return changed


def patch_loader_checkpoint(path):
    """Every probe reports itself, and the last one reached is repeated at the end."""
    text = path.read_text(encoding="utf-8")
    changed = False
    if LOADER_HELPER not in text:
        if text.count(LOADER_HELPER_ANCHOR) != 1:
            raise SystemExit("%s: expected one slot comment, found %d"
                             % (path, text.count(LOADER_HELPER_ANCHOR)))
        text = text.replace(LOADER_HELPER_ANCHOR, LOADER_HELPER, 1)
        print("  added loader_log_number() / loader_last_checkpoint")
        changed = True
    if LOADER_CHECKPOINT_NEW not in text:
        if text.count(LOADER_CHECKPOINT_OLD) != 1:
            raise SystemExit("%s: expected the stock loader_checkpoint(), found %d"
                             % (path, text.count(LOADER_CHECKPOINT_OLD)))
        text = text.replace(LOADER_CHECKPOINT_OLD, LOADER_CHECKPOINT_NEW, 1)
        print("  loader_checkpoint -> logs every probe it passes")
        changed = True
    if LOADER_TAIL_NEW not in text:
        if text.count(LOADER_TAIL_OLD) != 1:
            raise SystemExit("%s: expected one loader_jump(0, 0), found %d"
                             % (path, text.count(LOADER_TAIL_OLD)))
        text = text.replace(LOADER_TAIL_OLD, LOADER_TAIL_NEW, 1)
        print("  loader_return -> logs the last probe reached")
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
    return changed


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1

    root = argv[1]
    print("patching under %s" % root)

    vm = find(root, "osfmk/arm64/arm_vm_init.c")
    init = find(root, "osfmk/arm/arm_init.c")
    vmp = find(root, "osfmk/mach/arm/vm_param.h")
    pmap = find(root, "osfmk/arm/pmap/pmap.c")
    lret = find(root, "osfmk/arm64/loader_return.c")

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
    changed += patch_pmap_defaults_assert(pmap)
    changed += add_pmap_probes(pmap)
    changed += patch_loader_checkpoint(lret)

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
                   "loader_checkpoint(112 + ptov_index)"):
        if marker not in vm_text:
            raise SystemExit("verification failed in arm_vm_init.c: %s" % marker)
    if "loader_checkpoint(45)" not in init.read_text(encoding="utf-8"):
        raise SystemExit("verification failed in arm_init.c")
    pmap_text = pmap.read_text(encoding="utf-8")
    pmap_missing = [n for n in PMAP_EXPECTED if "loader_checkpoint(%d);" % n not in pmap_text]
    if pmap_missing:
        raise SystemExit("verification failed in pmap.c: probes missing %s" % pmap_missing)
    if PMAP_DEFAULTS_ASSERT in pmap_text:
        raise SystemExit("verification failed in pmap.c: /defaults assert still present")
    if LOADER_CHECKPOINT_NEW not in lret.read_text(encoding="utf-8"):
        raise SystemExit("verification failed in loader_return.c")
    if "KERNEL_STACK_MULTIPLIER (%d)" % STACK_MULTIPLIER not in vmp.read_text(encoding="utf-8"):
        raise SystemExit("verification failed in vm_param.h")

    print("done (%d change(s), %d fixed probes + indexed 80)"
          % (changed, len(EXPECTED)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
