"""Check that the payload volume really landed inside the firmware.

Reads a boot image, gunzips the kernel segment, walks the EDK2 firmware volume
and reports every FFS file, flagging the one Meizu15PayloadDxe looks for.

  python check_payload_fv.py <boot.img>
"""
import struct
import zlib
import sys

PAYLOAD_GUID = bytes.fromhex("229c1a3f415b0e4a9c772d618b4fe01a")  # little-endian


def guid_of(b):
    d1, d2, d3 = struct.unpack_from("<IHH", b, 0)
    return "%08X-%04X-%04X-%s-%s" % (
        d1, d2, d3, b[8:10].hex().upper(), b[10:16].hex().upper())



def ftype_of(blob, pos):
    return blob[pos + 18]


def walk_volume(blob, base, label):
    """List FFS files in one firmware volume and report the payload."""
    if blob[base + 0x28:base + 0x2C] != b"_FVH":
        print(f"{label}: no _FVH at 0x{base:x}")
        return False
    fv_len = struct.unpack_from("<Q", blob, base + 0x20)[0]
    hdr_len = struct.unpack_from("<H", blob, base + 0x30)[0]
    print(f"{label}: 0x{base:x}  length {fv_len:,}  header {hdr_len}")
    pos = base + hdr_len
    end = min(base + fv_len, len(blob))
    found = False
    while pos + 24 <= end:
        name = blob[pos:pos + 16]
        size = blob[pos + 20] | (blob[pos + 21] << 8) | (blob[pos + 22] << 16)
        if size < 24 or pos + size > end:
            break
        if name == b"\xff" * 16:
            pos += size
            continue
        ftype = blob[pos + 18]
        mark = ""
        if name == PAYLOAD_GUID:
            mark = "   <-- PAYLOAD VOLUME"
            found = True
        print(f"    {guid_of(name)}  type 0x{ftype:02x}  {size:>11,} bytes{mark}")
        pos += size
    return found

def main(path):
    data = open(path, "rb").read()
    assert data[:8] == b"ANDROID!", "not an Android boot image"
    kernel_size = struct.unpack_from("<I", data, 8)[0]
    page = struct.unpack_from("<I", data, 36)[0]
    print(f"boot image : {len(data):,} bytes, page {page}, kernel {kernel_size:,}")

    raw = data[page:page + kernel_size]
    # The segment is one or more gzip members; decompress them in sequence and
    # stop at whatever follows (edk2 pads the segment).
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
    if members:
        payload = bytes(out)
        print(f"kernel seg : {members} gzip member(s) -> {len(payload):,} bytes")
    else:
        payload = raw
        print(f"kernel seg : not gzip, using raw ({len(payload):,} bytes)")

    fv_off = payload.find(b"_FVH") - 0x28
    if fv_off < 0:
        print("no _FVH found: the kernel segment is not a firmware volume")
        return 1

    fv_len = struct.unpack_from("<Q", payload, fv_off + 0x20)[0]
    hdr_len = struct.unpack_from("<H", payload, fv_off + 0x30)[0]
    print(f"outer FV   : offset 0x{fv_off:x}, length {fv_len:,}, header {hdr_len}")

    pos = fv_off + hdr_len
    end = fv_off + fv_len
    found = False
    print("FFS files in the outer volume:")
    while pos + 24 <= end:
        name = payload[pos:pos + 16]
        if name == b"\xff" * 16:
            size = payload[pos + 20] | (payload[pos + 21] << 8) | (payload[pos + 22] << 16)
            if size < 24 or pos + size > end:
                break
            pos += size
            continue
        ftype = payload[pos + 18]
        size = payload[pos + 20] | (payload[pos + 21] << 8) | (payload[pos + 22] << 16)
        if size < 24:
            break
        g = guid_of(name)
        mark = ""
        if name == PAYLOAD_GUID:
            mark = "   <-- the payload volume"
            found = True
        print(f"  {g}  type 0x{ftype:02x}  {size:>10,} bytes{mark}")
        pos += size

    # The DXE volume is a GUIDED LZMA section inside the outer volume.
    print()
    for off, size, ftype in []:
        pass
    pos = fv_off + hdr_len
    while pos + 24 <= end:
        name = payload[pos:pos + 16]
        size = payload[pos + 20] | (payload[pos + 21] << 8) | (payload[pos + 22] << 16)
        if size < 24 or pos + size > end:
            break
        if name == b"\xff" * 16:
            pos += size
            continue
        if ftype_of(payload, pos) == 0x0B:
            sec = pos + 24
            ssize = payload[sec] | (payload[sec + 1] << 8) | (payload[sec + 2] << 16)
            inner = payload[sec + 24: sec + ssize]
            try:
                import lzma
                dec = lzma.decompress(inner, format=lzma.FORMAT_ALONE)
                dec = dec[8:] if dec[8:12] == b"_FVH" else dec
                base = dec.find(b"_FVH")
                if base >= 0:
                    if walk_volume(dec, base - 0x28, "inner DXE volume"):
                        found = True
            except Exception as exc:
                print(f"inner DXE volume: cannot decompress ({exc})")
        pos += size

    print()
    if found:
        print("RESULT: the payload FREEFORM file is present in the firmware volume")
        return 0
    print("RESULT: payload file NOT found in the outer volume")
    print("        (it may live in the inner LZMA-compressed DXE volume; that is")
    print("         expected if the payload is a DXE-phase FV entry)")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
