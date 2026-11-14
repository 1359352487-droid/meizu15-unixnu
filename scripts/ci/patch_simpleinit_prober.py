#!/usr/bin/env python3
"""Un-shadow the nested loop variable in SimpleInit's boot prober -- correctly.

`src/boot/prober.c` walks the entry table like this:

    for (size_t d = 0; ep->dir[d]; d++)
      for (size_t d = 0; ep->name[d]; d++) {
        ...
        snprintf(path, ..., "%s%s", ep->dir[d], ep->name[d]);
        ...
      }

The inner `d` shadows the outer one, so after the inner loop has run once the
outer condition reads `ep->dir[strlen(ep->name)]` and the walk stops after
`dir[0]` -- the literal "/".  That is why the menu reports
`found UEFI Shell at /shell.efi` instead of `/EFI/tools/shell.efi`.

The fix renames ONLY the inner loop variable and the `ep->name[...]` subscript.
It deliberately leaves `ep->dir[d]` alone: `d` there is the outer variable and
must stay.

That distinction is the whole point of this script.  An earlier version
renamed every standalone `d` inside the inner loop body, which turned

    ep->dir[d], ep->name[d]

into

    ep->dir[n], ep->name[n]

The outer `d` stops at the end of `dir[]`, but the body then indexed `dir[]`
with the inner counter, which keeps counting to the length of `name[]`.
`ep->dir[3]` is NULL and `ep->dir[4]` is past the array, and the firmware dies
in SimpleInitMain with

    ESR : EC 0x25 IL 0x1 ISS 0x00000007
    Data abort: Translation fault, third level

Idempotent; exits non-zero when the pattern it expects is not there instead of
silently doing nothing.

Usage:
    patch_simpleinit_prober.py <edk2-msm tree>
"""
import os
import re
import sys

CANDIDATES = [
    "GPLDrivers/Library/SimpleInit/src/boot/prober.c",
    "Library/SimpleInit/src/boot/prober.c",
    "src/boot/prober.c",
]

# The exact construct we rewrite: an outer `for` over ep->dir[d] immediately
# followed by an inner `for` that declares the SAME variable over ep->name[d].
OUTER_THEN_INNER = re.compile(
    r"for\s*\(\s*(?P<t>[A-Za-z_]\w*)\s+(?P<v>\w+)\s*=\s*0\s*;\s*"
    r"ep\s*->\s*dir\s*\[\s*(?P=v)\s*\]\s*;\s*(?P=v)\+\+\s*\)\s*"
    r"for\s*\(\s*(?P=t)\s+(?P=v)\s*=\s*0\s*;\s*"
    r"ep\s*->\s*name\s*\[\s*(?P=v)\s*\]\s*;\s*(?P=v)\+\+\s*\)",
    re.S,
)

ALREADY = re.compile(r"ep\s*->\s*name\s*\[\s*\w+\s*\]\s*;\s*\w+\+\+\s*\)")


def find_file(root):
    for rel in CANDIDATES:
        p = os.path.join(root, rel.replace("/", os.sep))
        if os.path.isfile(p):
            return p
    for dirpath, _dirnames, filenames in os.walk(root):
        if "prober.c" in filenames and (os.sep + "boot" + os.sep) in dirpath + os.sep:
            return os.path.join(dirpath, "prober.c")
    return None


def match_brace(src, open_idx):
    depth = 0
    i = open_idx
    while i < len(src):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced braces")


def inner_body_span(src, for_end):
    """Span of the inner loop body that starts just after `for (...)`, or None."""
    j = for_end
    while j < len(src) and src[j] in " \t\r\n":
        j += 1
    if j < len(src) and src[j] == "{":
        return j, match_brace(src, j)
    return None


def pick_name(src, taken):
    for cand in ("n", "name_index", "n_inner", "ni"):
        if cand in taken:
            continue
        if not re.search(r"\b" + re.escape(cand) + r"\b", src):
            return cand
    raise ValueError("no free identifier for the inner loop variable")


def patch(path):
    src = open(path, encoding="utf-8", errors="surrogateescape").read()

    m = OUTER_THEN_INNER.search(src)
    if not m:
        # Either already fixed, or upstream restructured it. Say which.
        if re.search(r"ep\s*->\s*dir\s*\[\s*d\s*\]", src) and "ep->name[n]" in src:
            print("already patched:", path)
            return 0
        print("FAIL: no `dir[d]`/`name[d]` shadowed pair found in", path, file=sys.stderr)
        print("      inspect the file by hand; upstream may have restructured it",
              file=sys.stderr)
        return 2

    var = m.group("v")
    if var != "d":
        print("note: the shadowed variable is `%s`, not `d`" % var)

    new = pick_name(src, {var})

    # Rewrite the inner `for` header first: only its two occurrences of the
    # variable change (the one in ep->name[...] and the increment).
    header = m.group(0)
    new_header = re.sub(
        r"(ep\s*->\s*name\s*\[\s*)" + re.escape(var) + r"(\s*\]\s*;\s*)" + re.escape(var) + r"(\s*\+\+\s*\))",
        lambda mm: mm.group(1) + new + mm.group(2) + new + mm.group(3),
        header,
        count=1,
    )
    # The declaration itself: `size_t d = 0` inside the inner for.
    inner_part = re.search(
        r"for\s*\(\s*(?P<t>[A-Za-z_]\w*)\s+" + re.escape(var) + r"\s*=\s*0",
        new_header,
    )
    if not inner_part:
        print("FAIL: could not find the inner loop declaration", file=sys.stderr)
        return 2
    offset = m.start(0) + inner_part.start()
    new_header = (
        new_header[: inner_part.start()]
        + inner_part.group(0).replace(" %s =" % var, " %s =" % new, 1)
        + new_header[inner_part.end():]
    )

    out = src[: m.start(0)] + new_header + src[m.end(0):]

    # Now the body: replace ONLY `ep->name[var]`, never `ep->dir[var]`.
    body = inner_body_span(out, m.start(0) + len(new_header))
    if body:
        b0, b1 = body
        chunk = out[b0:b1]
        fixed, count = re.subn(
            r"(ep\s*->\s*name\s*\[\s*)" + re.escape(var) + r"(\s*\])",
            lambda mm: mm.group(1) + new + mm.group(2),
            chunk,
        )
        out = out[:b0] + fixed + out[b1:]
        print("inner body: %d reference(s) to ep->name[%s] -> ep->name[%s]" % (count, var, new))
    else:
        print("warning: inner loop has no braced body; only the header was rewritten",
              file=sys.stderr)

    # Verify: dir must still be indexed by the outer variable, name by the new one.
    if not re.search(r"ep\s*->\s*dir\s*\[\s*" + re.escape(var) + r"\s*\]", out):
        print("FAIL: ep->dir[%s] disappeared -- refusing to write" % var, file=sys.stderr)
        return 3
    if not re.search(r"ep\s*->\s*name\s*\[\s*" + re.escape(new) + r"\s*\]", out):
        print("FAIL: ep->name[%s] was not produced -- refusing to write" % new, file=sys.stderr)
        return 3
    if re.search(r"ep\s*->\s*dir\s*\[\s*" + re.escape(new) + r"\s*\]", out):
        print("FAIL: ep->dir[%s] must not exist -- refusing to write" % new, file=sys.stderr)
        return 3
    if out.count("{") != src.count("{") or out.count("}") != src.count("}"):
        print("FAIL: brace count changed, refusing to write", file=sys.stderr)
        return 3

    open(path, "w", encoding="utf-8", errors="surrogateescape", newline="").write(out)
    print("patched:", path)
    for line in out.splitlines():
        if "->dir[" in line or "->name[" in line:
            print("   |", line.strip())
    return 0


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    p = find_file(root)
    if not p:
        print("FAIL: prober.c not found under", root, file=sys.stderr)
        return 2
    return patch(p)


if __name__ == "__main__":
    sys.exit(main())
