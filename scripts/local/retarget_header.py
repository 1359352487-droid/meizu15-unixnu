#!/usr/bin/env python3
"""Put the Meizu 15 stock boot-image header on a freshly built kernel payload.

The CI mkbootimg4096.py writes a minimal v0 header (kernel_addr = 0, tags = 0,
empty cmdline).  The device already proved that a *stock* header boots further,
so the reproducible build always transplants the first 1632 bytes of the stock
`boot.img` and only rewrites kernel_size / ramdisk_size / second_size.

usage: retarget_header.py <built.img> <stock-header.bin> <out.img>
"""
import struct
import sys

PAGE = 4096
HDR = 1632


def main():
    built, hdrfile, out = sys.argv[1], sys.argv[2], sys.argv[3]
    b = open(built, "rb").read()
    if b[:8] != b"ANDROID!":
        sys.exit("not an Android boot image: %s" % built)
    page = struct.unpack_from("<I", b, 36)[0]
    ksize = struct.unpack_from("<I", b, 8)[0]
    if page != PAGE:
        sys.exit("unexpected page size %d" % page)
    if ksize <= 0 or page + ksize > len(b):
        sys.exit("bad kernel size %d" % ksize)

    kernel = b[page:page + ksize]
    hdr = bytearray(open(hdrfile, "rb").read()[:HDR])
    if len(hdr) < HDR:
        sys.exit("stock header file too short")

    struct.pack_into("<I", hdr, 8, len(kernel))    # kernel_size
    struct.pack_into("<I", hdr, 16, 0)             # ramdisk_size
    struct.pack_into("<I", hdr, 24, 0)             # second_size

    pad = lambda n: b"\0" * ((PAGE - n % PAGE) % PAGE)
    body = bytes(hdr) + pad(len(hdr)) + kernel
    body += pad(len(body))
    open(out, "wb").write(body)

    v = open(out, "rb").read()
    f = lambda o: struct.unpack_from("<I", v, o)[0]
    print("wrote %s  %d bytes  kernel=%d kernel_addr=0x%X tags=0x%X page=%d"
          % (out, len(v), f(8), f(12), f(32), f(36)))


if __name__ == "__main__":
    main()
