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
outer condition reads `ep->dir[<len(name)>]` and the walk stops after `dir[0]`
-- the literal "/".  That is why the menu reports
`found UEFI Shell at /shell.efi` instead of `/EFI/tools/shell.efi`.

Two mistakes this script exists to avoid, both of which shipped a broken
firmware before:

  * renaming every standalone `d` in the inner body, which turns
    `ep->dir[d]` into `ep->dir[n]` and indexes the directory table with the
    *name* counter.  `dir[]` is shorter, so the read runs off the end and the
    firmware dies in SimpleInitMain with
    `Data abort: Translation fault, third level`.
  * rewriting `ep->name[d]` but not the declaration `size_t d = 0` in the inner
    `for`, which leaves `n` used but never declared.

So the rewrite is done on the exact source span of the inner `for` statement,
with three separate substitutions, and the result is verified before writing.

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

# Outer loop over ep->dir[d], immediately followed by the inner loop over
# ep->name[d] that re-declares the same variable.
NESTED = re.compile(
    r"for\s*\(\s*(?P<t>[A-Za-z_]\w*)\s+(?P<v>\w+)\s*=\s*0\s*;"
    r"\s*ep\s*->\s*dir\s*\[\s*(?P=v)\s*\]\s*;"
    r"\s*(?P=v)\s*\+\+\s*\)"
    r"\s*for\s*\(\s*(?P=t)\s+(?P=v)\s*=\s*0\s*;"
    r"\s*ep\s*->\s*name\s*\[\s*(?P=v)\s*\]\s*;"
    r"\s*(?P=v)\s*\+\+\s*\)",
    re.S,
)

# Names that are already taken anywhere in the file are not candidates.
IDENT = re.compile(r"[A-Za-z_]\w*")


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


def body_span(src, after_header):
    """Span of the statement that follows a `for (...)` header."""
    j = after_header
    while j < len(src) and src[j] in " \t\r\n":
        j += 1
    if j < len(src) and src[j] == "{":
        return j, match_brace(src, j)
    end = src.find(";", j)
    if end < 0:
        raise ValueError("dangling loop body")
    return j, end + 1


def pick_name(src, taken):
    used = set(IDENT.findall(src))
    for cand in ("n", "name_index", "n_inner", "ni", "idx"):
        if cand in used or cand == taken:
            continue
        return cand
    raise ValueError("no free identifier for the inner loop variable")


def patch(path):
    src = open(path, encoding="utf-8", errors="surrogateescape").read()

    m = NESTED.search(src)
    if not m:
        if re.search(r"ep\s*->\s*dir\s*\[\s*d\s*\]", src) and \
           re.search(r"ep\s*->\s*name\s*\[\s*n\s*\]", src):
            print("already patched:", path)
            return 0
        print("FAIL: no shadowed `dir[d]` / `name[d]` pair in", path, file=sys.stderr)
        print("      inspect the file by hand; upstream may have restructured it",
              file=sys.stderr)
        return 2

    var = m.group("v")
    new = pick_name(src, var)

    # Work on the inner `for` only: everything from the second `for` to the end
    # of its body.
    inner_start = m.start("v") if False else src.index("for", m.start() + 1)
    body = body_span(src, m.end(0))
    inner_end = body[1]

    span = src[inner_start:inner_end]

    # 1) the declaration `size_t d = 0` -> `size_t n = 0`
    decl = re.compile(r"(\b[A-Za-z_]\w*\s+)" + re.escape(var) + r"(\s*=\s*0\s*;)")
    span, n1 = decl.subn(lambda mm: mm.group(1) + new + mm.group(2), span, count=1)
    # 2) the condition `ep->name[d]` -> `ep->name[n]`
    cond = re.compile(r"(ep\s*->\s*name\s*\[\s*)" + re.escape(var) + r"(\s*\])")
    span, n2 = cond.subn(lambda mm: mm.group(1) + new + mm.group(2), span, count=1)
    # 3) the increment `; d++)` -> `; n++)`
    incr = re.compile(r"(;\s*)" + re.escape(var) + r"(\s*\+\+\s*\)\s*\{?)", re.S)
    span, n3 = incr.subn(lambda mm: mm.group(1) + new + mm.group(2), span, count=1)

    if (n1, n2, n3) != (1, 1, 1):
        print("FAIL: expected one match each for declaration/condition/increment, "
              "got %s -- refusing to write" % ((n1, n2, n3),), file=sys.stderr)
        return 3

    # 4) the body: only `ep->name[var]`, never `ep->dir[var]`.
    body_start_in_span = body[0] - inner_start
    head, tail = span[:body_start_in_span], span[body_start_in_span:]
    tail, n4 = re.subn(
        r"(ep\s*->\s*name\s*\[\s*)" + re.escape(var) + r"(\s*\])",
        lambda mm: mm.group(1) + new + mm.group(2),
        tail,
    )
    span = head + tail

    out = src[:inner_start] + span + src[inner_end:]

    # Verify before writing. Each of these has shipped a broken build before.
    checks = [
        (r"ep\s*->\s*dir\s*\[\s*" + re.escape(var) + r"\s*\]", True,
         "ep->dir[%s] must survive (outer loop)" % var),
        (r"ep\s*->\s*dir\s*\[\s*" + re.escape(new) + r"\s*\]", False,
         "ep->dir[%s] must not exist (inner body)" % new),
        (r"ep\s*->\s*name\s*\[\s*" + re.escape(new) + r"\s*\]", True,
         "ep->name[%s] must exist" % new),
        (r"\b[A-Za-z_]\w*\s+" + re.escape(new) + r"\s*=\s*0\s*;", True,
         "'%s' must be declared" % new),
    ]
    failures = []
    for pat, want, why in checks:
        got = re.search(pat, out) is not None
        if got != want:
            failures.append(why)
    if failures:
        print("FAIL: verification failed -- refusing to write", file=sys.stderr)
        for f in failures:
            print("      -", f, file=sys.stderr)
        return 3
    if out.count("{") != src.count("{") or out.count("}") != src.count("}"):
        print("FAIL: brace count changed -- refusing to write", file=sys.stderr)
        return 3

    open(path, "w", encoding="utf-8", errors="surrogateescape", newline="").write(out)
    print("patched:", path)
    print("  inner variable `%s` -> `%s`; ep->dir[] still on `%s`" % (var, new, var))
    for i, line in enumerate(out.splitlines(), 1):
        if "->dir[" in line or "->name[" in line:
            print("   %4d | %s" % (i, line.strip()))
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
