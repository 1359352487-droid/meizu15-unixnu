"""Cut the firmware volume out of an Android boot image.

boot.img is: Android boot header (page-sized) + a gzip'd EDK2 firmware volume.
The volume is what UEFIExtract wants to look at, and edk2 pads the segment with
whatever follows the last gzip member, so decompress members until they run out.

Usage:
    unpack-bootimg-fv.py <boot.img> <out.fv>
"""
import struct
import sys
import zlib

ANDROID_MAGIC = b"ANDROID!"


def main(boot, out_path):
    data = open(boot, "rb").read()
    if data[:8] != ANDROID_MAGIC:
        raise SystemExit("not an Android boot image: %r" % data[:8])

    kernel_size = struct.unpack_from("<I", data, 8)[0]
    page = struct.unpack_from("<I", data, 36)[0] or 2048
    print(f"boot image : {len(data):,} bytes, page {page}, kernel {kernel_size:,}")

    raw = data[page:page + kernel_size]
    out = bytearray()
    rest = raw
    members = 0
    while rest[:2] == b"\x1f\x8b":
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        out += d.decompress(rest)
        out += d.flush()
        members += 1
        rest = d.unused_data
        if not d.eof:
            break

    payload = bytes(out) if members else raw
    print(f"kernel seg : {members} gzip member(s) -> {len(payload):,} bytes")

    fvh = payload.find(b"_FVH")
    if fvh < 0:
        raise SystemExit("no _FVH: the kernel segment is not a firmware volume")
    fv_off = fvh - 0x28
    fv_len = struct.unpack_from("<Q", payload, fv_off + 0x20)[0]
    print(f"outer FV   : file offset 0x{fv_off:x}, length {fv_len:,}")

    open(out_path, "wb").write(payload[fv_off:fv_off + fv_len])
    print(f"wrote      : {out_path}  {fv_len:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
