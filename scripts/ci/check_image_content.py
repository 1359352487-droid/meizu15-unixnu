"""Check that a built boot image carries the loader-side changes.

The DXE volume is LZMA-compressed, so a plain byte scan of boot.img finds
nothing. The payload FAT is a RAW section inside that volume, which is where
kernel.macho and therefore the XNULDRST slot live.

    check_image_content.py <boot.img>
"""
import lzma
import struct
import sys
import zlib


def outer_fv(path):
    data = open(path, "rb").read()
    if data[:8] != b"ANDROID!":
        raise SystemExit("not an Android boot image")
    size = struct.unpack_from("<I", data, 8)[0]
    page = struct.unpack_from("<I", data, 36)[0] or 2048
    raw = data[page:page + size]
    out = bytearray()
    rest = raw
    while rest[:2] == b"\x1f\x8b":
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        out += d.decompress(rest)
        out += d.flush()
        rest = d.unused_data
        if not d.eof:
            break
    return bytes(out)


def fv_info(blob):
    off = blob.find(b"_FVH") - 0x28
    hdr = struct.unpack_from("<H", blob, off + 0x30)[0]
    length = struct.unpack_from("<Q", blob, off + 0x20)[0]
    return off, hdr, length


def dxe_volume(fv):
    off, hdr, length = fv_info(fv)
    pos, end = off + hdr, off + length
    while pos + 24 <= end:
        size = fv[pos + 20] | (fv[pos + 21] << 8) | (fv[pos + 22] << 16)
        if size < 24:
            break
        if fv[pos + 18] == 0x0B:            # EFI_FV_FILETYPE_FV_IMAGE
            s = pos + 24
            ssize = fv[s] | (fv[s + 1] << 8) | (fv[s + 2] << 16)
            body = fv[s:s + ssize]
            off2 = struct.unpack_from("<H", body, 20)[0]
            return lzma.decompress(body[off2:], format=lzma.FORMAT_ALONE)
        pos = (pos + size + 7) & ~7
    raise SystemExit("no FV_IMAGE file in the outer volume")


def main(path):
    inner = dxe_volume(outer_fv(path))
    print("DXE volume: %d bytes" % len(inner))

    print()
    print("DarwinBoot, UTF-16LE Print strings")
    for text in (
        "return stub at",
        "no loader_stub_slot in this kernel",
        "no room for the return stub",
    ):
        hit = text.encode("utf-16-le") in inner
        print("  %-38s %s" % (text, "FOUND" if hit else "absent"))

    print()
    print("DarwinBoot, ASCII AsciiSPrint format strings")
    #
    # The fault report added on 2026-11-15 goes through AsciiSPrint, not Print,
    # so it is plain ASCII and must NOT be looked for as UTF-16 -- doing that
    # reports "absent" for an image that has it.
    #
    for text in (
        "kernel returned: reason %lu, log %lu bytes",
        "x3 %016lx x4 %016lx",
        "x5 %016lx x6 %016lx",
        "x7 %016lx",
    ):
        hit = text.encode("ascii") in inner
        print("  %-38s %s" % (text, "FOUND" if hit else "absent"))

    print()
    print("launcher boot arguments, UTF-16LE")
    for text in ("-v loader_return_after=15", "loader_return_after"):
        hit = text.encode("utf-16-le") in inner
        print("  %-38s %s" % (text, "FOUND" if hit else "absent"))

    print()
    print("kernel slot magic, ASCII (inside the payload FAT)")
    raw = inner.decode("latin-1")
    for text in ("XNULDRST",):
        print("  %-38s %s" % (text, "FOUND" if text in raw else "absent"))


if __name__ == "__main__":
    main(sys.argv[1])
