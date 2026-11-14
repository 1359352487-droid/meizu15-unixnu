"""Print the exact compression wrapper around the payload section.

Used while working out why Fv->ReadSection(EFI_SECTION_RAW) returns
EFI_NOT_FOUND for the payload FREEFORM file: it reports the section type and,
for a COMPRESSION section, the UncompressedLength and CompressionType byte, so
the driver can be made to decode exactly what is there.
"""
import lzma
import struct
import sys
import zlib

PAYLOAD_GUID = bytes.fromhex("229c1a3f415b0e4a9c772d618b4fe01a")


def gunzip_segment(path):
    data = open(path, "rb").read()
    kernel_size = struct.unpack_from("<I", data, 8)[0]
    page = struct.unpack_from("<I", data, 36)[0]
    seg = data[page:page + kernel_size]
    out = bytearray()
    rest = seg
    while rest[:2] == b"\x1f\x8b":
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        out += d.decompress(rest)
        out += d.flush()
        rest = d.unused_data
        if not d.eof:
            break
    return bytes(out)


def fv_of(blob):
    off = blob.find(b"_FVH") - 0x28
    hdr = struct.unpack_from("<H", blob, off + 0x30)[0]
    length = struct.unpack_from("<Q", blob, off + 0x20)[0]
    return off, hdr, length


def walk(blob, base, hdr, length, want_guid):
    pos = base + hdr
    end = base + length
    while pos + 24 <= end:
        name = blob[pos:pos + 16]
        size = blob[pos + 20] | (blob[pos + 21] << 8) | (blob[pos + 22] << 16)
        if size < 24 or pos + size > end:
            return None
        if name == want_guid:
            return pos
        pos = (pos + size + 7) & ~7
    return None


def main(path):
    print("image      :", path)
    outer = gunzip_segment(path)
    o_base, o_hdr, o_len = fv_of(outer)
    print(f"outer FV   : +0x{o_base:x} len {o_len:,} hdr {o_hdr}")

    # Find the FV_IMAGE file (type 0x0b) which holds the DXE volume.
    pos = o_base + o_hdr
    end = o_base + o_len
    dxe = None
    while pos + 24 <= end:
        name = outer[pos:pos + 16]
        size = outer[pos + 20] | (outer[pos + 21] << 8) | (outer[pos + 22] << 16)
        if size < 24:
            break
        if outer[pos + 18] == 0x0B:
            s = pos + 24
            ssize = outer[s] | (outer[s + 1] << 8) | (outer[s + 2] << 16)
            body = outer[s:s + ssize]
            doff = struct.unpack_from("<H", body, 20)[0]
            raw = body[doff:]
            dxe = lzma.decompress(raw, format=lzma.FORMAT_ALONE)
            print(f"DXE volume : {len(dxe):,} bytes")
            break
        pos = (pos + size + 7) & ~7

    if dxe is None:
        print("no DXE volume found")
        return 1

    d_base, d_hdr, d_len = fv_of(dxe)
    print(f"inner FV   : +0x{d_base:x} len {d_len:,} hdr {d_hdr}")

    p = walk(dxe, d_base, d_hdr, d_len, PAYLOAD_GUID)
    if p is None:
        print("payload FFS not found")
        return 1

    size = dxe[p + 20] | (dxe[p + 21] << 8) | (dxe[p + 22] << 16)
    print(f"payload FFS: at 0x{p:x}, {size:,} bytes")

    s = p + 24
    ssize = dxe[s] | (dxe[s + 1] << 8) | (dxe[s + 2] << 16)
    stype = dxe[s + 3]
    body = dxe[s:s + ssize]
    print(f"  section 0 : type 0x{stype:02x}  size {ssize:,}  header {body[:4].hex(' ')}")

    if stype == 0x01:  # EFI_SECTION_COMPRESSION
        ulen = struct.unpack_from("<I", body, 4)[0]
        ctype = body[8]
        print(f"  UncompressedLength : {ulen:,}")
        print(f"  CompressionType    : {ctype}  "
              f"({'EFI_STANDARD/bit-Tiano' if ctype == 0 else 'LZMA' if ctype == 1 else 'Brotli' if ctype == 2 else 'unknown'})")
        print(f"  data begins        : {body[9:9 + 16].hex(' ')}")
        # Show the nested section header too.
        if ctype == 0:
            print("  -> bit-Tiano data: the DXE core needs UefiDecompressLib")
        elif ctype == 1:
            print("  -> LZMA (5-byte props + 8-byte size): needs LzmaCustomDecompressLib")
    elif stype == 0x02:  # GUID_DEFINED
        guid = body[4:20]
        doff = struct.unpack_from("<H", body, 20)[0]
        attrs = struct.unpack_from("<H", body, 22)[0]
        print(f"  guid {guid.hex(' ')}")
        print(f"  dataoff {doff} attrs 0x{attrs:04x}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
