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

# The device tree reaches the kernel as a physical address (DarwinBoot.c:
# Args->DeviceTree = TreeAddress). In the XNU_MONITOR build, arm_vm_physmap_init()
# calls PE_slide_devicetree() to rebase it -- but BCM2837 does not define
# XNU_MONITOR, so this port takes the #else arm of that function, which has no
# such call at all: SecureDT keeps the physical address and the first user after
# kva_active = TRUE (pmap_compute_max_asids, then pmap_compute_pv_targets, then
# pmap_compute_io_rgns) walks it as if it were virtual. That is a data abort with
# no kprintf yet to report it.
#
# SecureDTInit() validates nothing (pexpert/gen/device_tree.c: it stores base and
# size and trusts them), so the kernel can point it at the physmap alias of the
# same blob right after the slide that maps end_kern..topOfKernelData.
SECUREDT_ANCHOR = """\tarm_vm_physmap_slide(temp_ptov_table, (args->topOfKernelData - gPhysBase + gVirtBase),
\t    real_avail_end - args->topOfKernelData, AP_RWNA, ARM64_GRANULE_ALLOW_BLOCK | ARM64_GRANULE_ALLOW_HINT); // rest of physmem
"""
SECUREDT_PATCH = SECUREDT_ANCHOR + """\t/*
\t * No PE_slide_devicetree() in this (non-XNU_MONITOR) arm, so nothing rebased the
\t * blob the loader handed over as a physical address. Point SecureDT at the
\t * physmap alias: args->deviceTreeP is that physical address, and
\t * gVirtBase - gPhysBase is the offset phystokv() uses.
\t */
\tSecureDTInit((void const *)((uintptr_t)args->deviceTreeP + (gVirtBase - gPhysBase)),
\t    (size_t)args->deviceTreeLength);
"""

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
/* Values a probe wants printed even though the tail keeps only the last
 * lines: loader_return() writes them after truncating, so they cannot scroll away. */
uint64_t loader_probe_values[6];

/*
 * How much of the log the stub is asked to print. The console scrolls and the
 * stub paints its barcodes at y = 1694..1888, so a longer log has its final
 * lines covered by them -- and the final lines are the ones worth reading.
 * A probe line is six bytes ("cp 62\n") and one console line, and about forty
 * console lines fit above the barcodes, hence 224 rather than 448.
 */
#define LOADER_RETURN_TAIL 224

/* "cp 62\\n" -- digits only, so no printf and no locale; safe this early. */
void
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
\t/*
\t * A fault leaves through the early exception vector, not through
\t * loader_jump(), so nothing writes the log back before the stub reads it and
\t * the last line can still be dirty in the cache. Write the touched lines
\t * through here, where every probe passes.
\t */
\t__asm__ volatile ("dc cvac, %0" : : "r" ((uintptr_t)loader_log & ~63UL));
\t__asm__ volatile ("dc cvac, %0" : : "r" (((uintptr_t)loader_log + loader_log_len) & ~63UL));
\t__asm__ volatile ("dsb sy");
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
\t/*
\t * Probe values come after everything else: the trim below keeps only the
\t * final LOADER_RETURN_TAIL bytes, and a value printed at its own probe can
\t * be pushed out of that window by the kernel console output that follows.
\t */
\tfor (int pv = 0; pv < 6; pv++) {
\t\tif (loader_probe_values[pv] != 0) {
\t\t\tloader_log_number("v ", loader_probe_values[pv]);
\t\t}
\t}
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
        pattern = re.compile(r"^#define\s+%s\s+\((?:4|16)\*4096\)" % name, re.M)
        replacement = "#define %-23s (64*4096)" % name
        if re.search(re.escape(replacement), text):
            print("  %s already 256 KiB" % name)
            continue
        if not pattern.search(text):
            raise SystemExit("%s: no '#define %s (4*4096)' or '(16*4096)'" % (path, name))
        text = pattern.sub(replacement, text, count=1)
        print("  %s -> 256 KiB" % name)
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


def add_sp_measure(path):
    """Log the stack pointer just before pmap_bootstrap() is called.

    96 never runs even though it is the first statement of pmap_bootstrap and
    its prologue is only sub sp,#0x240 plus five stp, so the stack it is handed
    is the thing to measure: either it is exhausted or it does not point where
    the kernel thinks.
    """
    text = path.read_text(encoding="utf-8")
    if 'loader_log_number("sp "' in text:
        print("  SP measurement already present")
        return False
    anchor = "\tpmap_bootstrap(dynamic_memory_begin);\n"
    if text.count(anchor) != 1:
        raise SystemExit("%s: expected one pmap_bootstrap call, found %d"
                         % (path, text.count(anchor)))
    code = ("\t{\n"
            "\t\tuint64_t sp_now;\n"
            "\t\t__asm__ volatile (\"mov %0, sp\" : \"=r\" (sp_now));\n"
            "\t\tloader_log_number(\"sp \", sp_now);\n"
            "\t}\n")
    text = text.replace(anchor, code + anchor, 1)
    if "extern void loader_log_number" not in text:
        text = text.replace("\nextern void loader_checkpoint(uint64_t n);\n",
                            "\nextern void loader_checkpoint(uint64_t n);\n"
                            "extern void loader_log_number(const char *prefix, uint64_t n);\n",
                            1)
    path.write_text(text, encoding="utf-8")
    print("  added SP measurement before pmap_bootstrap()")
    return True


def add_secure_dt_reinit(path):
    """Point SecureDT at the physmap alias once the physmap exists."""
    text = path.read_text(encoding="utf-8")
    if "SecureDTInit((void const *)((uintptr_t)args->deviceTreeP" in text:
        print("  SecureDT reinit already present")
        return False
    if text.count(SECUREDT_ANCHOR) != 1:
        raise SystemExit("%s: expected one PE_slide_devicetree block, found %d"
                         % (path, text.count(SECUREDT_ANCHOR)))
    path.write_text(text.replace(SECUREDT_ANCHOR, SECUREDT_PATCH, 1), encoding="utf-8")
    print("  SecureDTInit() after PE_slide_devicetree()")
    return True


# osfmk/arm/arm_init.c, the stretch between checkpoint 20 (PE_create_console) and
# 21 (machine_startup). HakiOS already numbers its own probes up to 21, so these
# take 22..28. With kprintf alive by now, the panel also carries the kernel's own
# words, not just the trail.
ARM_LATE_AFTER = [
    ("\tPE_init_printf(FALSE);\n", 22),
    ("\tcpu_machine_idle_init(TRUE);\n", 23),
    ("\tPE_init_platform(TRUE, &BootCpuData);\n", 24),
    ("\tml_map_cpu_pio();\n", 25),
    ("\tcpu_timebase_init(TRUE);\n", 26),
    ("\tgDramSize = *dram_size;\n", 27),
]
ARM_LATE_BEFORE = [
    ("\tmachine_startup(args);\n", 28),
]
ARM_LATE_EXPECTED = (22, 23, 24, 25, 26, 27, 28)

# 26 (cpu_timebase_init) fires, 27 (gDramSize) does not, and there is no panic
# text on the panel -- so the stop is a silent abort in the stretch that has no
# probe: kptimer_curcpu_up (KPERF), PE_init_cpu(), fiq_context_init(TRUE),
# pal_hib_init (HIBERNATION), then the /chosen dram-base/dram-size lookups.
# HakiOS already owns 30..35, so these take 36/37.
# 38 sits between the KPERF block and PE_init_cpu(), 39 between PE_init_cpu() and
# fiq_context_init(), 36 after both. With 26 firing and 36 not, that pins the stop
# to one of: kptimer_curcpu_up() (only if KPERF is compiled in), PE_init_cpu()
# itself, or fiq_context_init().
ARM_LATE2_AFTER = [
    ("\tPE_init_cpu();\n\tfiq_context_init(TRUE);\n",
     "\tloader_checkpoint(38);\n\tPE_init_cpu();\n\tloader_checkpoint(39);\n"
     "\tfiq_context_init(TRUE);\n\tloader_checkpoint(36);\n"),
]
ARM_LATE2_BEFORE = [
    ("\tunsigned long const *dram_base;\n\tunsigned long const *dram_size;\n\n"
     "\tif (SecureDTLookupEntry(NULL, \"/chosen\", &chosen) != kSuccess) {\n", 37),
]
ARM_LATE2_EXPECTED = (36, 37, 38, 39)


def add_arm_late2_probes(path):
    """Probes 36/37 between cpu_timebase_init() and the /chosen dram lookups."""
    text = path.read_text(encoding="utf-8")
    changed = False
    for needle, replacement in ARM_LATE2_AFTER:
        if replacement in text:
            print("  already present: checkpoints 38/39/36")
            continue
        if text.count(needle) != 1:
            raise SystemExit("%s: expected one PE_init_cpu/fiq_context pair, found %d"
                             % (path, text.count(needle)))
        text = text.replace(needle, replacement, 1)
        print("  added checkpoints 38 (before PE_init_cpu), 39 (after it), 36 (after fiq_context_init)")
        changed = True
    for needle, number in ARM_LATE2_BEFORE:
        if "\tloader_checkpoint(%d);\n" % number + needle in text:
            print("  already present: checkpoint %d" % number)
            continue
        if text.count(needle) != 1:
            raise SystemExit("%s: expected one /chosen+dram_base block, found %d"
                             % (path, text.count(needle)))
        text = text.replace(needle, "\tloader_checkpoint(%d);\n" % number + needle, 1)
        print("  added loader_checkpoint(%d) before the /chosen lookups" % number)
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
    return changed



# osfmk/arm64/cpu.c, cpu_timebase_init(). Between probe 25 (ml_map_cpu_pio) and 26
# (cpu_timebase_init returns) there is nothing else -- APPLE_ARM64_ARCH_FAMILY is
# not defined for BCM2837, so configure_late_apple_regs() is compiled out. The
# function itself is short: getCpuDatap(), a block of rtclock_timebase_func
# pointers, then ml_set_reset_time(ml_get_hwclock()). 29 proves cdp was obtained
# and prints it; 31 proves the hwclock path returned.
CPU_TB_AFTER_CDP = "\tcpu_data_t *cdp = getCpuDatap();\n"
CPU_TB_BEFORE_DECREMENTER = "\tcdp->cpu_decrementer = 0x7FFFFFFFUL;\n"
CPU_EXTERN_ANCHOR = "#include <arm/machine_routines.h>\n"
CPU_EXTERN = ("\nextern void loader_checkpoint(uint64_t n);\n"
              "extern void loader_log_number(const char *prefix, uint64_t n);\n")
CPU_TB_PROBE_CODE = """\t{
\t\tuint64_t cdp_now = (uint64_t)(uintptr_t)cdp;
\t\tloader_log_number("cdp ", cdp_now);
\t}
\tloader_checkpoint(29);
"""
def add_arm_late_probes(path):
    """Probes 22-28 between PE_create_console() and machine_startup()."""
    text = path.read_text(encoding="utf-8")
    changed = False
    for needle, number in ARM_LATE_AFTER:
        if "loader_checkpoint(%d);" % number in text and needle + "\tloader_checkpoint(%d);\n" % number in text:
            print("  already present: checkpoint %d" % number)
            continue
        if text.count(needle) != 1:
            raise SystemExit("%s: expected one '%s', found %d"
                             % (path, needle.strip(), text.count(needle)))
        if needle + "\tloader_checkpoint(%d);\n" % number in text:
            print("  already present: checkpoint %d" % number)
            continue
        text = text.replace(needle, needle + "\tloader_checkpoint(%d);\n" % number, 1)
        print("  added loader_checkpoint(%d) after %s" % (number, needle.strip()))
        changed = True
    for needle, number in ARM_LATE_BEFORE:
        if needle in text and "\tloader_checkpoint(%d);\n" % number + needle in text:
            print("  already present: checkpoint %d" % number)
            continue
        if text.count(needle) != 1:
            raise SystemExit("%s: expected one '%s', found %d"
                             % (path, needle.strip(), text.count(needle)))
        text = text.replace(needle, "\tloader_checkpoint(%d);\n" % number + needle, 1)
        print("  added loader_checkpoint(%d) before %s" % (number, needle.strip()))
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
    return changed
def add_cpu_timebase_probes(path):
    """Probes 29/31 inside cpu_timebase_init(), and print the cpu_data pointer."""
    text = path.read_text(encoding="utf-8")
    changed = False
    if "extern void loader_log_number" not in text:
        if CPU_EXTERN_ANCHOR not in text:
            raise SystemExit("%s: cpu.c include anchor not found" % path)
        text = text.replace(CPU_EXTERN_ANCHOR, CPU_EXTERN_ANCHOR + CPU_EXTERN, 1)
        print("  added extern declarations to cpu.c")
        changed = True
    if "loader_checkpoint(29);" not in text:
        if text.count(CPU_TB_AFTER_CDP) != 1:
            raise SystemExit("%s: expected one getCpuDatap() line, found %d"
                             % (path, text.count(CPU_TB_AFTER_CDP)))
        text = text.replace(CPU_TB_AFTER_CDP, CPU_TB_AFTER_CDP + CPU_TB_PROBE_CODE, 1)
        print("  added loader_checkpoint(29) + cdp value in cpu_timebase_init()")
        changed = True
    if "loader_checkpoint(31);" not in text:
        if text.count(CPU_TB_BEFORE_DECREMENTER) != 1:
            raise SystemExit("%s: expected one cpu_decrementer line, found %d"
                             % (path, text.count(CPU_TB_BEFORE_DECREMENTER)))
        text = text.replace(CPU_TB_BEFORE_DECREMENTER,
                            "\tloader_checkpoint(31);\n" + CPU_TB_BEFORE_DECREMENTER, 1)
        print("  added loader_checkpoint(31) after the hwclock calls")
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
    return changed



PE_INIT_CPU_OLD = """\tloader_checkpoint(38);
\tPE_init_cpu();
\tloader_checkpoint(39);
"""
PE_INIT_CPU_NEW = """\tloader_checkpoint(38);
\t/*
\t * PE_init_cpu() does not exist in the open source tree (pexpert/pexpert/arm64
\t * carries headers only, and the symbol is stripped from the linked kernel),
\t * and it is the one call between probes 38 and 39: the run stops there with
\t * no panic text. Whatever it does on Apple silicon has no SDM660 equivalent,
\t * so skip it and see how far the rest of arm_init() gets.
\t */
\tloader_checkpoint(39);
"""


def skip_pe_init_cpu(path):
    """Drop the PE_init_cpu() call that aborts between probes 38 and 39."""
    text = path.read_text(encoding="utf-8")
    if PE_INIT_CPU_NEW in text:
        print("  PE_init_cpu already skipped")
        return False
    if text.count(PE_INIT_CPU_OLD) != 1:
        raise SystemExit("%s: expected one 38/PE_init_cpu/39 block, found %d"
                         % (path, text.count(PE_INIT_CPU_OLD)))
    path.write_text(text.replace(PE_INIT_CPU_OLD, PE_INIT_CPU_NEW, 1), encoding="utf-8")
    print("  skipped PE_init_cpu() between 38 and 39")
    return True


# osfmk/arm64/machine_routines.c, machine_startup(): probe 28 is the line before the
# call, and the whole function is
#
#     machine_startup(boot_args *args) { machine_conf(); kernel_bootstrap(); }
#
# so 72 is the entry, 73 is after machine_conf(), 74 sits in front of
# kernel_bootstrap() -- the scheduler bring-up, and the last thing between this
# port and a first kernel thread.
MACHINE_STARTUP_EXTERN_ANCHOR = "#include <kern/startup.h>\n"
MACHINE_STARTUP_EXTERN = ("\nextern void loader_checkpoint(uint64_t n);\n")
MACHINE_STARTUP_ENTRY = "machine_startup(__unused boot_args * args)\n{\n"
MACHINE_STARTUP_PROBES = [
    (MACHINE_STARTUP_ENTRY, 72, "after"),
    ("\tmachine_conf();\n", 73, "after"),
    ("\tkernel_bootstrap();\n", 74, "before"),
]
MACHINE_STARTUP_EXPECTED = (72, 73, 74)


def add_machine_startup_probes(path):
    """Probes 72/73/74 inside machine_startup()."""
    text = path.read_text(encoding="utf-8")
    changed = False
    if "extern void loader_checkpoint" not in text:
        if MACHINE_STARTUP_EXTERN_ANCHOR not in text:
            raise SystemExit("%s: include anchor not found" % path)
        text = text.replace(MACHINE_STARTUP_EXTERN_ANCHOR,
                            MACHINE_STARTUP_EXTERN_ANCHOR + MACHINE_STARTUP_EXTERN, 1)
        print("  added extern loader_checkpoint() to machine_routines.c")
        changed = True
    for needle, number, position in MACHINE_STARTUP_PROBES:
        if position == "after" and needle + "\tloader_checkpoint(%d);\n" % number in text:
            print("  already present: checkpoint %d" % number)
            continue
        if position == "before" and "\tloader_checkpoint(%d);\n" % number + needle in text:
            print("  already present: checkpoint %d" % number)
            continue
        if text.count(needle) != 1:
            raise SystemExit("%s: expected one '%s', found %d"
                             % (path, needle.strip()[:40], text.count(needle)))
        if position == "after":
            text = text.replace(needle, needle + "\tloader_checkpoint(%d);\n" % number, 1)
        else:
            text = text.replace(needle, "\tloader_checkpoint(%d);\n" % number + needle, 1)
        print("  added loader_checkpoint(%d) %s %s" % (number, position, needle.strip()[:40]))
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
    return changed


# osfmk/kern/startup.c, kernel_bootstrap(): probe 74 is the line before the call, so
# everything from here on is the kernel's own bring-up. The order is
#
#     printf(version) -> scale_setup -> vm_mem_bootstrap -> startup hooks (OSLOG)
#     -> console_init -> stackshot_init -> sched_init -> ... -> first kernel thread
#
# 75/76 bracket vm_mem_bootstrap, then 77/78 bracket the startup-hook batch and the
# scheduler init, which is the last thing before threads can exist.
KERNEL_BOOTSTRAP_PROBES = [
    ('\tprintf("%s\\n", version); /* log kernel version */\n', 75),
    ("\tvm_mem_bootstrap();\n", 76),
    ("\tconsole_init();\n", 77),
    ("\tsched_init();\n", 78),
]
KERNEL_BOOTSTRAP_EXPECTED = (75, 76, 77, 78)


def add_kernel_bootstrap_probes(path):
    """Probes 75-78 inside kernel_bootstrap()."""
    text = path.read_text(encoding="utf-8")
    changed = False
    if "extern void loader_checkpoint" not in text:
        anchor = "#include <kern/startup.h>\n"
        if anchor not in text:
            raise SystemExit("%s: include anchor not found" % path)
        text = text.replace(anchor, anchor + "\nextern void loader_checkpoint(uint64_t n);\n", 1)
        print("  added extern loader_checkpoint() to startup.c")
        changed = True
    for needle, number in KERNEL_BOOTSTRAP_PROBES:
        if needle + "\tloader_checkpoint(%d);\n" % number in text:
            print("  already present: checkpoint %d" % number)
            continue
        if text.count(needle) != 1:
            raise SystemExit("%s: expected one '%s', found %d"
                             % (path, needle.strip()[:45], text.count(needle)))
        text = text.replace(needle, needle + "\tloader_checkpoint(%d);\n" % number, 1)
        print("  added loader_checkpoint(%d) after %s" % (number, needle.strip()[:45]))
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
    return changed


# osfmk/kern/startup.c, kernel_bootstrap() after probe 78 (sched_init). 80..89 are
# taken by the physmap slide's indexed probes, so these use 100..107. The run ends
# at load_context(thread), which does not return: that is the first kernel thread.
KERNEL_BOOTSTRAP_LATE = [
    ("\tkernel_startup_initialize_upto(STARTUP_SUB_MACH_IPC);\n", 100),
    ("\tPMAP_ACTIVATE_KERNEL(master_cpu);\n", 101),
    ("\tmachine_init();\n", 102),
    ("\tclock_init();\n", 103),
    ("\ttask_init();\n", 104),
    ("\tthread_init();\n", 105),
    ("\tresult = kernel_thread_create((thread_continue_t)kernel_bootstrap_thread, NULL, MAXPRI_KERNEL, &thread);\n", 106),
]
KERNEL_BOOTSTRAP_LATE_BEFORE = [
    ('\tkernel_bootstrap_log("load_context - done");\n', 107),
]
KERNEL_BOOTSTRAP_LATE_EXPECTED = (100, 101, 102, 103, 104, 105, 106, 107)


def add_kernel_bootstrap_late_probes(path):
    """Probes 100-107 from the MACH_IPC hooks to load_context()."""
    text = path.read_text(encoding="utf-8")
    changed = False
    for needle, number in KERNEL_BOOTSTRAP_LATE:
        if needle + "\tloader_checkpoint(%d);\n" % number in text:
            print("  already present: checkpoint %d" % number)
            continue
        if text.count(needle) != 1:
            raise SystemExit("%s: expected one '%s', found %d"
                             % (path, needle.strip()[:46], text.count(needle)))
        text = text.replace(needle, needle + "\tloader_checkpoint(%d);\n" % number, 1)
        print("  added loader_checkpoint(%d) after %s" % (number, needle.strip()[:46]))
        changed = True
    for needle, number in KERNEL_BOOTSTRAP_LATE_BEFORE:
        if "\tloader_checkpoint(%d);\n" % number + needle in text:
            print("  already present: checkpoint %d" % number)
            continue
        if text.count(needle) != 1:
            raise SystemExit("%s: expected one '%s', found %d"
                             % (path, needle.strip()[:46], text.count(needle)))
        text = text.replace(needle, "\tloader_checkpoint(%d);\n" % number + needle, 1)
        print("  added loader_checkpoint(%d) before %s" % (number, needle.strip()[:46]))
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
    return changed


# osfmk/kern/startup.c, load_context(): 107 fires and the switch itself does not
# happen. The function walks machine_set_current_thread -> PMAP_ACTIVATE_KERNEL ->
# stack check -> scheduler bookkeeping -> PMAP_ACTIVATE_USER -> machine_load_context.
# PMAP_ACTIVATE_USER is the one that touches TTBR0, which this port deliberately
# leaves pointing at an invalid table, so it heads the suspect list.
MACHINE_CONTEXT_DUMP = """\t{
\t\t/* The real offsets (disassembled): act_cpudatap at 0x1c8, kstackptr at
\t\t * 0x158. fix_boot_cpu_data() already recorded those in loader_probe_values
\t\t * 0..3, so only leave a sentinel here: this runs later and would overwrite
\t\t * them, and the numbers are what the panel needs to show. */
\t\tloader_probe_values[4] = 31337;
\t}
"""

LOAD_CONTEXT_PROBES = [
    ("\tmachine_set_current_thread(thread);\n", 108),
    ("\tPMAP_ACTIVATE_KERNEL(processor->cpu_id);\n", 109),
    ("\tprocessor->active_thread = thread;\n", 110),
    ("\tprocessor_up(processor);\n", 111),
    ("\tPMAP_ACTIVATE_USER(thread, processor->cpu_id);\n", 112),
]
LOAD_CONTEXT_BEFORE = [
    ("\tmachine_load_context(thread);\n", 113),
]
LOAD_CONTEXT_EXPECTED = (108, 109, 110, 111, 112, 113)


def add_load_context_probes(path):
    """Probes 108-113 inside load_context(), plus a raw machine_thread dump."""
    text = path.read_text(encoding="utf-8")
    changed = False

    if "extern void loader_log_number" not in text:
        if "extern void loader_checkpoint(uint64_t n);\n" not in text:
            anchor = "#include <kern/startup.h>\n"
            if anchor not in text:
                raise SystemExit("%s: include anchor not found" % path)
            text = text.replace(anchor, anchor + "\nextern void loader_checkpoint(uint64_t n);\n", 1)
            print("  added extern loader_checkpoint() to startup.c")
        text = text.replace("extern void loader_checkpoint(uint64_t n);\n",
                            "extern void loader_checkpoint(uint64_t n);\n"
                            "extern void loader_log_number(const char *prefix, uint64_t n);\n"
                            "extern uint64_t loader_probe_values[6];\n", 1)
        print("  added extern loader_log_number() to startup.c")
        changed = True

    for needle, number in LOAD_CONTEXT_PROBES:
        if needle + "\tloader_checkpoint(%d);\n" % number in text:
            print("  already present: checkpoint %d" % number)
            continue
        if text.count(needle) != 1:
            raise SystemExit("%s: expected one '%s', found %d"
                             % (path, needle.strip()[:46], text.count(needle)))
        text = text.replace(needle, needle + "\tloader_checkpoint(%d);\n" % number, 1)
        print("  added loader_checkpoint(%d) after %s" % (number, needle.strip()[:46]))
        changed = True

    for needle, number in LOAD_CONTEXT_BEFORE:
        if "\tloader_checkpoint(%d);\n" % number + needle in text:
            print("  already present: checkpoint %d" % number)
            continue
        if text.count(needle) != 1:
            raise SystemExit("%s: expected one '%s', found %d"
                             % (path, needle.strip()[:46], text.count(needle)))
        text = text.replace(needle, "\tloader_checkpoint(%d);\n" % number + needle, 1)
        print("  added loader_checkpoint(%d) before %s" % (number, needle.strip()[:46]))
        changed = True

    if MACHINE_CONTEXT_DUMP not in text:
        anchor = "\tloader_checkpoint(113);\n"
        if anchor not in text:
            raise SystemExit("%s: probe 113 not found" % path)
        text = text.replace(anchor, MACHINE_CONTEXT_DUMP + anchor, 1)
        print("  added raw machine_thread dump before 113")
        changed = True

    if changed:
        path.write_text(text, encoding="utf-8")
    return changed

# Disassembled from the linked kernel (symbol _machine_load_context at
# 0xfffffff0074644e0): the very first instructions are
#
#     msr  TPIDR_EL1, x0
#     ldr  x1, [x0, #0x1c8]      <- act_cpudatap, thread offset 0x1c8
#     str  x0, [x1, #0x30]
#     ldrsh w2, [x1, ...]        <- dies here, with no output, if x1 is bad
#     msr  TPIDR_EL0, x2
#     ldr  x1, [x0, #0x148]      <- cthread
#     ldr  x1, [x0, #0x158]      <- kstackptr
#
# so the offsets to trust are 0x1c8 and 0x158, not the machine_thread member
# names (which do not exist in this tree) nor word indices.
ACPUDATAP_OFF = 0x1c8
KSTACKPTR_OFF = 0x158

BOOT_CPU_FIX = """\t{
\t\t/*
\t\t * load_context() reaches 113 and machine_load_context() then dies with no
\t\t * output. Its second instruction loads act_cpudatap from thread+0x1c8 and
\t\t * the next one dereferences it (ldrsh from [x1]), so a zero or garbage
\t\t * value there aborts before anything can be printed.
\t\t *
\t\t * Read both offsets, then point act_cpudatap at the boot CPU data.
\t\t */
\t\tchar *th = (char *)thread;
\t\textern void *cpu_datap(int cpu);   /* _cpu_datap: the CPU data for a cpu number */
\t\tvoid *cdp = cpu_datap(0);
\t\tloader_probe_values[0] = *(uint64_t *)(th + 0x1c8);
\t\tloader_probe_values[1] = *(uint64_t *)(th + 0x158);
\t\tloader_probe_values[2] = (uint64_t)(uintptr_t)cdp;
\t\t*(uint64_t *)(th + 0x1c8) = (uint64_t)(uintptr_t)cdp;
\t\tloader_probe_values[3] = *(uint64_t *)(th + 0x1c8);
\t}
"""


def fix_boot_cpu_data(path):
    """Point thread+0x1c8 (act_cpudatap) at BootCpuData before the switch."""
    text = path.read_text(encoding="utf-8")
    if "act_cpudatap from thread+0x1c8" in text:
        print("  boot cpu data fix already present")
        return False
    anchor = "\tloader_checkpoint(107);\n"
    if text.count(anchor) != 1:
        raise SystemExit("%s: expected one probe 107, found %d" % (path, text.count(anchor)))
    text = text.replace(anchor, BOOT_CPU_FIX + anchor, 1)
    print("  added act_cpudatap fix before probe 107")
    path.write_text(text, encoding="utf-8")
    return True

# osfmk/kern/startup.c, kernel_bootstrap_thread(): what runs *after* the context
# switch. Everything in machine_load_context() turned out to be legal on ARMv8.0
# (the S3_3_C4_C4_0 accesses are FPCR), so probe 113 firing and then silence may
# simply mean the switch worked and this function is where it dies.
BOOTSTRAP_THREAD_ENTRY = "kernel_bootstrap_thread(void)\n{\n"
BOOTSTRAP_THREAD_PROBES = [
    (BOOTSTRAP_THREAD_ENTRY, 114, "after"),
    ("\tplatform_stall_panic_or_spin(PLATFORM_STALL_XNU_LOCATION_KERNEL_BOOTSTRAP);\n", 115, "after"),
    ("\tidle_thread_create(processor);\n", 116, "after"),
    ("\tsched_startup();\n", 117, "after"),
]
BOOTSTRAP_THREAD_EXPECTED = (114, 115, 116, 117)


def add_kernel_bootstrap_thread_probes(path):
    """Probes 114-117 inside kernel_bootstrap_thread(), the first kernel thread."""
    text = path.read_text(encoding="utf-8")
    changed = False
    for needle, number, position in BOOTSTRAP_THREAD_PROBES:
        added = "loader_checkpoint(%d);" % number
        if position == "after":
            if needle + "\t" + added + "\n" in text:
                print("  already present: checkpoint %d" % number)
                continue
        if text.count(needle) != 1:
            raise SystemExit("%s: expected one '%s', found %d"
                             % (path, needle.strip()[:46], text.count(needle)))
        text = text.replace(needle, needle + "\t" + added + "\n", 1)
        print("  added loader_checkpoint(%d) after %s" % (number, needle.strip()[:46]))
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
    return changed

# osfmk/kern/startup.c, kernel_bootstrap_thread() after sched_startup() (117).
# This is no longer "can it boot" but "which subsystems can initialise on this SoC";
# clock_service_create and device_service_create need a working timebase and device
# tree, and PE_init_iokit is Apple platform code.
THREAD_LATE_PROBES = [
    ("\tthread_daemon_init();\n", 118),
    ("\tkernel_startup_initialize_upto(STARTUP_SUB_THREAD_CALL);\n", 119),
    ("\tthread_bind(processor);\n", 120),
    ("\tmapping_adjust();\n", 121),
    ("\tclock_service_create();\n", 122),
    ("\tdevice_service_create();\n", 123),
]
THREAD_LATE_EXPECTED = (118, 119, 120, 121, 122, 123)


def add_thread_late_probes(path):
    """Probes 118-123 in kernel_bootstrap_thread(), after sched_startup()."""
    text = path.read_text(encoding="utf-8")
    changed = False
    for needle, number in THREAD_LATE_PROBES:
        if needle + "\tloader_checkpoint(%d);\n" % number in text:
            print("  already present: checkpoint %d" % number)
            continue
        if text.count(needle) != 1:
            raise SystemExit("%s: expected one '%s', found %d"
                             % (path, needle.strip()[:46], text.count(needle)))
        text = text.replace(needle, needle + "\tloader_checkpoint(%d);\n" % number, 1)
        print("  added loader_checkpoint(%d) after %s" % (number, needle.strip()[:46]))
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
    return changed

# osfmk/kern/startup.c, kernel_bootstrap_thread() after device_service_create()
# (probe 123). clock_service_create and device_service_create both passed, so the
# Mach timebase and the device tree work at the IOKit level; what follows is
# carveout accounting, KDP, the SYSCTL hooks, the address-permutation randoms and
# PE_init_iokit (Apple platform code, likely to need skipping like PE_init_cpu).
THREAD_TAIL_PROBES = [
    ("\tphys_carveout_init();\n", 124),
    ("\tkdp_init();\n", 125),
    ("\tkernel_startup_initialize_upto(STARTUP_SUB_SYSCTL);\n", 126),
    ("\tvm_kernel_addrhash_salt_ext = early_random();\n", 127),
    ("\tPE_init_iokit();\n", 128),
    ("\tkernel_startup_initialize_upto(STARTUP_SUB_EARLY_BOOT);\n", 129),
]
THREAD_TAIL_EXPECTED = (124, 125, 126, 127, 128, 129)


def add_thread_tail_probes(path):
    """Probes 124-129, the rest of kernel_bootstrap_thread()."""
    text = path.read_text(encoding="utf-8")
    changed = False
    for needle, number in THREAD_TAIL_PROBES:
        if needle + "\tloader_checkpoint(%d);\n" % number in text:
            print("  already present: checkpoint %d" % number)
            continue
        if text.count(needle) != 1:
            raise SystemExit("%s: expected one '%s', found %d"
                             % (path, needle.strip()[:46], text.count(needle)))
        text = text.replace(needle, needle + "\tloader_checkpoint(%d);\n" % number, 1)
        print("  added loader_checkpoint(%d) after %s" % (number, needle.strip()[:46]))
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
    return changed

# kernel_bootstrap_thread(): probe 127 fires (the early_random block) and 128 does not,
# so PE_init_iokit() is the stop -- the same kind of Apple platform code as PE_init_cpu:
# headers only in the open tree, no SDM660 equivalent. Skip it.
PE_IOKIT_OLD = "\tPE_init_iokit();\n\tloader_checkpoint(128);\n"
PE_IOKIT_NEW = """\t/*
\t * PE_init_iokit() belongs to Apple's closed platform layer (like PE_init_cpu): the
\t * open tree carries pexpert headers only, and nothing on this SoC provides the
\t * IOKit platform expert it expects. Probe 127 fires and 128 does not, so it is the
\t * one call in between. Skip it and let the EARLY_BOOT hooks run.
\t */
\tloader_checkpoint(128);
"""


def skip_pe_init_iokit(path):
    """Drop the PE_init_iokit() call that aborts between probes 127 and 128."""
    text = path.read_text(encoding="utf-8")
    if PE_IOKIT_NEW in text:
        print("  PE_init_iokit already skipped")
        return False
    if text.count(PE_IOKIT_OLD) != 1:
        raise SystemExit("%s: expected one PE_init_iokit/128 block, found %d"
                         % (path, text.count(PE_IOKIT_OLD)))
    path.write_text(text.replace(PE_IOKIT_OLD, PE_IOKIT_NEW, 1), encoding="utf-8")
    print("  skipped PE_init_iokit() between 127 and 128")
    return True

# osfmk/kern/startup.c, kernel_bootstrap_thread() after the EARLY_BOOT hooks (129).
# The panel showed "Security policy loaded: Seatbelt sandbox policy" while last cp
# said 129, so execution got at least to mac_policy_initmach() (line ~719) with no
# probe to say where it stopped. 130 is the important one: spllo() is the first time
# interrupts are allowed, and this board's ADT has no interrupt-controller node.
EARLY_BOOT_PROBES = [
    ("\t(void) spllo();         /* Allow interruptions */\n", 130),
    ("\tinitialize_screen(NULL, kPEAcquireScreen);\n", 131),
    ("\tvm_commpage_text_init();\n", 132),
    ("\tmac_policy_initmach();\n", 133),
    ("\tkernel_startup_initialize_upto(STARTUP_SUB_LOCKDOWN);\n", 134),
    ("\tOSKextRemoveKextBootstrap();\n", 135),
    ("\tmachine_lockdown();\n", 136),
    ("\tPE_lockdown_iokit();\n", 137),
    ("\tvm_set_restrictions(machine_info.max_cpus);\n", 138),
]
EARLY_BOOT_BEFORE = [
    ("\tbsd_init();\n", 139),
]
EARLY_BOOT_EXPECTED = (130, 131, 132, 133, 134, 135, 136, 137, 138, 139)


def add_early_boot_probes(path):
    """Probes 130-139 from spllo() to bsd_init()."""
    text = path.read_text(encoding="utf-8")
    changed = False
    for needle, number in EARLY_BOOT_PROBES:
        if needle + "\tloader_checkpoint(%d);\n" % number in text:
            print("  already present: checkpoint %d" % number)
            continue
        if text.count(needle) != 1:
            raise SystemExit("%s: expected one '%s', found %d"
                             % (path, needle.strip()[:46], text.count(needle)))
        text = text.replace(needle, needle + "\tloader_checkpoint(%d);\n" % number, 1)
        print("  added loader_checkpoint(%d) after %s" % (number, needle.strip()[:46]))
        changed = True
    for needle, number in EARLY_BOOT_BEFORE:
        if "\tloader_checkpoint(%d);\n" % number + needle in text:
            print("  already present: checkpoint %d" % number)
            continue
        if text.count(needle) != 1:
            raise SystemExit("%s: expected one '%s', found %d"
                             % (path, needle.strip()[:46], text.count(needle)))
        text = text.replace(needle, "\tloader_checkpoint(%d);\n" % number + needle, 1)
        print("  added loader_checkpoint(%d) before %s" % (number, needle.strip()[:46]))
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
    cpu = find(root, "osfmk/arm64/cpu.c")
    mr = find(root, "osfmk/arm64/machine_routines.c")
    kstartup = find(root, "osfmk/kern/startup.c")

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
    changed += add_arm_late_probes(init)
    changed += add_arm_late2_probes(init)
    changed += skip_pe_init_cpu(init)
    changed += add_cpu_timebase_probes(cpu)
    changed += add_machine_startup_probes(mr)
    changed += add_kernel_bootstrap_probes(kstartup)
    changed += add_kernel_bootstrap_late_probes(kstartup)
    changed += add_load_context_probes(kstartup)
    changed += fix_boot_cpu_data(kstartup)
    changed += add_kernel_bootstrap_thread_probes(kstartup)
    changed += add_thread_late_probes(kstartup)
    changed += add_thread_tail_probes(kstartup)
    changed += skip_pe_init_iokit(kstartup)
    changed += add_early_boot_probes(kstartup)
    changed += add_sp_measure(vm)
    changed += add_secure_dt_reinit(vm)
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
