#!/usr/bin/env python3
"""Make SimpleInit's free(NULL) safe before building the Meizu 15 image.

The device hits a data abort at SimpleInitMain+0xB5D0C:

    ldur w3, [x19, #-0x10]      ; Head->Signature, x19 == 0
    ESR 0x25 / ISS 0x4         ; translation fault, level 0
    FAR 0xFFFFFFFFFFFFFFF0     ; == NULL - 0x10

i.e. free(NULL) reads the CPOOL_HEAD signature ("Cphd") through a pointer
derived from NULL.  libs/compatible/malloc.c computes

    Head = BASE_CR(Ptr, CPOOL_HEAD, Data);
    assert(Head != NULL);              /* always true: NULL - 0x10 != 0 */
    ... if (Ptr != NULL) { ... Head->Signature ... }

so the guard exists but sits *after* the pointer arithmetic, and the compiler
happily folds the signature load in front of it.  The fix is to return early.

The script is idempotent, prints the diff it makes, and fails loudly if the
expected function is not where it is expected to be.
"""
import sys, os, re

CANDIDATES = [
    "libs/compatible/malloc.c",
    "GPLDrivers/Library/SimpleInit/libs/compatible/malloc.c",
]

GUARD = "\tif (Ptr == NULL) return;\n"


def find_file(root):
    for rel in CANDIDATES:
        p = os.path.join(root, rel)
        if os.path.isfile(p):
            return p
    for dirpath, _dirnames, filenames in os.walk(root):
        if "malloc.c" in filenames and os.sep + "compatible" + os.sep in dirpath + os.sep:
            return os.path.join(dirpath, "malloc.c")
    return None


def patch(path):
    src = open(path, encoding="utf-8", errors="surrogateescape").read()
    if GUARD.strip() and re.search(r"if\s*\(\s*Ptr\s*==\s*NULL\s*\)\s*return;", src):
        print("already patched:", path)
        return 0

    # locate "free(void *Ptr)" and the first BASE_CR after it
    m = re.search(r"\nfree\s*\(\s*void\s*\*\s*Ptr\s*\)\s*\n?\s*\{", src)
    if not m:
        print("FAIL: free(void *Ptr) not found in", path, file=sys.stderr)
        return 2
    start = m.end()
    m2 = re.compile(r"\n[^\n]*BASE_CR\s*\(\s*Ptr\s*,\s*CPOOL_HEAD").search(src, start)
    if not m2:
        print("FAIL: BASE_CR(Ptr, CPOOL_HEAD) not found in free()", file=sys.stderr)
        return 2

    indent = "\t"
    line_start = src.rfind("\n", 0, m2.start()) + 1
    line = src[line_start:m2.start()]
    if line.startswith("\t") or line.startswith(" "):
        indent = line[: len(line) - len(line.lstrip())]

    insert_at = line_start
    new = src[:insert_at] + indent + "if (Ptr == NULL) return;   /* meizu15: free(NULL) must not probe CPOOL_HEAD */\n" + src[insert_at:]

    # sanity: guard sits before the BASE_CR
    gi = new.find("if (Ptr == NULL) return;")
    cr = new.find("BASE_CR (Ptr, CPOOL_HEAD")
    if cr < 0:
        cr = new.find("BASE_CR(Ptr, CPOOL_HEAD")
    assert 0 <= gi < cr, "guard not before BASE_CR"

    open(path, "w", encoding="utf-8", errors="surrogateescape", newline="").write(new)
    print("patched:", path)
    for ln in new.splitlines():
        if "Ptr == NULL" in ln or "BASE_CR" in ln and "Ptr" in ln:
            print("   |", ln.strip())
    return 0


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    p = find_file(root)
    if not p:
        print("FAIL: malloc.c not found under", root, file=sys.stderr)
        return 2
    return patch(p)


if __name__ == "__main__":
    sys.exit(main())
