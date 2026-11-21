#!/usr/bin/env python3
"""Lay the SDM660 translation layer into an XNU source tree and build it in.

    python3 install_sdm660_shim.py <xnu-src root>

`kernel/sdm660/` in this repository holds the layer; the kernel build does not
know that directory exists, so two things have to happen in the tree before
`make`:

  1. the files are copied under osfmk/arm64/ (where the rest of the arm64
     platform code lives), and
  2. osfmk/conf/files.arm64 gets one line per file, which is how XNU decides
     what to compile -- `standard` means "always", as opposed to `optional
     <symbol>`.

Both steps are idempotent: running this twice changes nothing the second time.

Why not a driver in HakiOS's sense (`drivers/<subsystem>/` + a `conf` block)?
Because those are built through CONFIG_HAKIOS_<NAME> symbols resolved by
HakiOS's own config generator, and this layer has to work in *this* device
repository's build, which only patches the tree. A `standard` line is the
smallest thing that does the job, and it is visible in `files.arm64` afterwards.
"""
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]          # this repository
LAYER = HERE / "kernel" / "sdm660"
FILES = (
    "sdm660_shim.h",
    "sdm660_shim.c",
    "sdm660_uart.c",
    "sdm660_gic.c",
    "sdm660_timer.c",
)
BUILT = ("sdm660_shim.c", "sdm660_uart.c", "sdm660_gic.c", "sdm660_timer.c")

FILES_LIST = "osfmk/conf/files.arm64"
MARKER = "osfmk/arm64/sdm660_shim.c"


def find_xnu(root, suffix):
    matches = [p for p in Path(root).rglob(suffix) if ".dSYM" not in str(p)]
    if not matches:
        raise SystemExit("not found under %s: %s" % (root, suffix))
    return matches[0]


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1

    root = Path(argv[1])
    files_list = find_xnu(root, FILES_LIST)
    dest_dir = files_list.parents[1] / "arm64"      # osfmk/arm64
    if not dest_dir.is_dir():
        raise SystemExit("%s: no arm64 directory next to %s" % (dest_dir, files_list))

    copied = 0
    for name in FILES:
        src = LAYER / name
        if not src.is_file():
            raise SystemExit("%s: missing from the layer" % src)
        dst = dest_dir / name
        if dst.is_file() and dst.read_bytes() == src.read_bytes():
            continue
        shutil.copyfile(src, dst)
        print("  copied %s" % name)
        copied += 1

    text = files_list.read_text(encoding="utf-8")
    if MARKER in text:
        print("  %s already lists the layer" % FILES_LIST)
    else:
        block = ("\n#\n# SDM660 (Meizu 15) translation layer -- kernel/sdm660/ in the device\n"
                 "# repository; installed by scripts/ci/install_sdm660_shim.py.\n#\n")
        for name in BUILT:
            block += "osfmk/arm64/%s\t\tstandard\n" % name
        files_list.write_text(text.rstrip("\n") + "\n" + block, encoding="utf-8")
        print("  added %d file(s) to %s" % (len(BUILT), FILES_LIST))

    # Verify what the build will actually see.
    after = files_list.read_text(encoding="utf-8")
    missing = [n for n in BUILT if "osfmk/arm64/%s" % n not in after]
    if missing:
        raise SystemExit("verification failed: not in the files list: %s" % missing)
    absent = [n for n in FILES if not (dest_dir / n).is_file()]
    if absent:
        raise SystemExit("verification failed: not in the tree: %s" % absent)

    print("done (%d copied, %d built in)" % (copied, len(BUILT)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
