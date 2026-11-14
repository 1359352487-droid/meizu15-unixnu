#!/usr/bin/env python3
"""Un-shadow the nested loop variable in SimpleInit's boot prober.

`src/boot/prober.c` walks the entry table like this:

    for (size_t d = 0; ep->dir[d]; d++)
      for (size_t d = 0; ep->name[d]; d++) {
        ...
      }

The inner `d` shadows the outer one.  When the inner loop finishes it has
`d == strlen(ep->name)`, so the *outer* condition `ep->dir[d]` reads past the
end of the (much shorter) directory table and the loop stops.  Net effect: only
`ep->dir[0]` -- the literal "/" -- is ever probed, which is why the menu reports
`found UEFI Shell at /shell.efi` instead of `/EFI/tools/shell.efi` and why our
launcher only shows up when it sits at the root of the volume.

The fix renames the inner variable (and every reference inside the inner loop
body) so the two loops stop interfering.

The script is idempotent, prints what it changed, and exits non-zero when it
finds a nested loop it cannot rewrite safely instead of silently doing nothing.

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

# for (TYPE var = 0; ... ; ...)  immediately followed by  for (TYPE var = 0; ... ; ...)
NESTED_FOR = re.compile(
    r"for\s*\((?P<t1>[A-Za-z_]\w*)\s+(?P<v>\w+)\s*=\s*0\s*;[^;]*;[^)]*\)\s*\{?\s*"
    r"for\s*\((?P<t2>[A-Za-z_]\w*)\s+(?P=v)\s*=\s*0\s*;[^;]*;[^)]*\)",
    re.S,
)


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
    """Index just past the '}' that closes the '{' at open_idx."""
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


def match_paren(src, open_idx):
    depth = 0
    i = open_idx
    while i < len(src):
        c = src[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced parens")


def inner_for_span(src, start):
    """Span of the inner `for (...)` statement that begins at `start`."""
    p = src.index("(", start)
    after_cond = match_paren(src, p)
    j = after_cond
    while j < len(src) and src[j] in " \t\r\n":
        j += 1
    if j < len(src) and src[j] == "{":
        return match_brace(src, j)
    end = src.find(";", j)
    if end < 0:
        raise ValueError("dangling for body")
    return end + 1


def rename_ident(text, old, new):
    """Rename a standalone identifier: not `x.old`, not `x->old`, not a prefix."""
    pattern = re.compile(r"(?<![\w.>])" + re.escape(old) + r"(?!\w)")
    return pattern.sub(new, text)


def pick_name(src, base):
    cand = base + "_inner"
    n = 0
    while re.search(r"(?<![\w.>])" + re.escape(cand) + r"(?!\w)", src):
        n += 1
        cand = "%s_inner%d" % (base, n)
    return cand


def patch(path):
    src = open(path, encoding="utf-8", errors="surrogateescape").read()
    matches = list(NESTED_FOR.finditer(src))
    if not matches:
        if "_inner" in src and "->dir[" in src:
            print("already patched (no shadowed loop left):", path)
            return 0
        print("FAIL: no shadowed nested `for` loop found in", path, file=sys.stderr)
        print("      the file may have been restructured upstream; inspect it by hand",
              file=sys.stderr)
        return 2

    # Rewrite from the back so earlier offsets stay valid.
    out = src
    changed = 0
    for m in reversed(matches):
        var = m.group("v")
        start = m.start() + m.group(0).rindex("for")
        try:
            end = inner_for_span(out, start)
        except ValueError as exc:
            print("FAIL: %s (inner loop for `%s` at offset %d)" % (exc, var, start),
                  file=sys.stderr)
            return 2
        span = out[start:end]
        new = pick_name(out, var)
        out = out[:start] + rename_ident(span, var, new) + out[end:]
        changed += 1
        print("loop %d: inner `%s` -> `%s`" % (changed, var, new))

    # Sanity: the shadowing is gone and nothing else moved.
    if NESTED_FOR.search(out):
        print("FAIL: a shadowed nested loop survived the rewrite", file=sys.stderr)
        return 3
    if out.count("{") != src.count("{") or out.count("}") != src.count("}"):
        print("FAIL: brace count changed, refusing to write", file=sys.stderr)
        return 3

    open(path, "w", encoding="utf-8", errors="surrogateescape", newline="").write(out)
    print("patched:", path, "(%d loop%s)" % (changed, "" if changed == 1 else "s"))
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
