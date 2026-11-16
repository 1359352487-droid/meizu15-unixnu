"""Changes the UniXNU port made, plus the probes that finish the bisection.

Two of these come from `UniXNU/UniXNU` -- see `docs/xnu/four-k-page-tables.md`
and `patches/xnu/non-apple-cache-monitor.patch`. The reasoning carries over
because this kernel is also built for BCM2837 with 4 KiB pages on a non-Apple
SoC, so the Apple-only paths have to be guarded off.

1. osfmk/arm64/arm_vm_init.c -- guard ml_enable_monitor()

   arm_vm_init() calls it unconditionally and it is the first thing the function
   does after arm_vm_physmap_init(). On Apple silicon it turns on the page
   protection layer; this SoC has none. Guarding it did not move the wall (probe
   17 stayed silent) but it costs nothing and removes a known-wrong call.

2. osfmk/arm/arm_init.c -- checkpoint 45

   Between the planted probes 16 and 17 sit exactly two calls:

       processor_bootstrap();
       arm_vm_init(xmaxmem, args);

   45 goes between them. It fired, which cleared processor_bootstrap().

3. osfmk/arm64/arm_vm_init.c -- checkpoints 46, 47, 48, 49

   The failure is inside arm_vm_init(). That function is 580 lines and its
   milestones are:

       2141  arm_set_user_tbi();
       2143  arm_vm_physmap_init(args);
       2148  set_mmu_ttb(invalid_ttep & TTBR_BADDR_MASK);
       2150  flush_mmu_tlb();
       2151  kva_active = TRUE;          <- the switch off the bootstrap tables
       2247  pmap_bootstrap(dynamic_memory_begin);

   The probes sit so that one boot divides the function into quarters:

       46  before arm_set_user_tbi()      -> the first ~300 lines are done
       47  after kva_active = TRUE        -> physmap, both set_mmu_ttb,
                                             flush_mmu_tlb and the switch are done
       48  before pmap_bootstrap()        -> the AuxKC and arm_vm_map block is done
       49  after pmap_bootstrap()         -> pmap_bootstrap itself is done

Idempotent: every edit checks for its own result before applying.

    python apply_nonapple_guards.py <hakios/xnu-src root>
"""
import sys
from pathlib import Path

EXTERN_DECL = "\nextern void loader_checkpoint(uint64_t n);\n"

# Probe insertions against osfmk/arm64/arm_vm_init.c, in source order.
# Each entry is (anchor, replacement, marker-that-proves-it-is-done).
VM_PROBES = [
    (
        "\tarm_set_user_tbi();\n",
        "\tloader_checkpoint(46);\n"
        "\tarm_set_user_tbi();\n",
        "loader_checkpoint(46)",
    ),
    (
        "\tkva_active = TRUE;\n",
        "\tkva_active = TRUE;\n"
        "\tloader_checkpoint(47);\n",
        "loader_checkpoint(47)",
    ),
    (
        "\tpmap_bootstrap(dynamic_memory_begin);\n",
        "\tloader_checkpoint(48);\n"
        "\tpmap_bootstrap(dynamic_memory_begin);\n"
        "\tloader_checkpoint(49);\n",
        "loader_checkpoint(48)",
    ),
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
    changed = 0
    for anchor, replacement, marker in VM_PROBES:
        if marker in text:
            print("  already present: %s" % marker)
            continue
        count = text.count(anchor)
        if count != 1:
            raise SystemExit(
                "%s: anchor for %s matched %d times, expected 1"
                % (path, marker, count)
            )
        text = text.replace(anchor, replacement, 1)
        print("  added %s" % marker)
        changed += 1
    if changed:
        path.write_text(text, encoding="utf-8")
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
    for marker in ("loader_checkpoint(46)", "loader_checkpoint(47)",
                   "loader_checkpoint(48)", "loader_checkpoint(49)",
                   "extern void loader_checkpoint", "#if XNU_MONITOR"):
        if marker not in vm_text:
            raise SystemExit("verification failed in arm_vm_init.c: %s" % marker)

    if "loader_checkpoint(45)" not in init.read_text(encoding="utf-8"):
        raise SystemExit("verification failed in arm_init.c: checkpoint 45")

    print("done (%d change(s))" % changed)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
