"""Unpack boot.img all the way down to the payload FAT volume and list it.

The shipped check_payload_fv.py stops at the outer firmware volume: its inner-FFS
walk desynchronises (it reports an 828-byte DXE_CORE and a type 0x70 file, neither
of which can exist in a real DXE volume) and it never reaches the payload.  This
script walks every layer with structural validation:

    boot.img -> gzip -> outer FV -> FV_IMAGE -> GUIDED(LZMA) -> inner DXE FV
             -> FILE FREEFORM(3f1a9c22-...) -> COMPRESSION -> FAT volume

then parses the FAT volume so "is /bootaa64.efi at the volume root?" is answered
from the artefact, not from a build log.

  python verify_payload_volume.py <boot.img> [--dump-fat out.img]
"""
import lzma
import os
import struct
import sys
import zlib

PAYLOAD_GUID = bytes.fromhex("229c1a3f415b0e4a9c772d618b4fe01a")

FFS_TYPES = {
    0x01: "RAW", 0x02: "FREEFORM", 0x03: "SEC", 0x04: "PEI_CORE",
    0x05: "DXE_CORE", 0x06: "PEIM", 0x07: "DRIVER",
    0x08: "COMBINED_PEIM_DRIVER", 0x09: "APPLICATION", 0x0A: "SMM",
    0x0B: "FV_IMAGE", 0x0C: "COMBINED_SMM_DXE", 0x0D: "COMBINED_DXE",
    0x0E: "SMM_CORE", 0x0F: "MM_STANDALONE", 0x10: "MM_CORE_STANDALONE",
    0xF0: "FFS_PAD",
}

SEC_TYPES = {
    0x01: "COMPRESSION", 0x02: "GUID_DEFINED", 0x03: "DISPOSABLE",
    0x10: "PE32", 0x11: "PIC", 0x12: "TE", 0x13: "DXE_DEPEX", 0x14: "VERSION",
    0x15: "USER_INTERFACE", 0x16: "COMPATIBILITY16", 0x17: "FV_IMAGE",
    0x18: "FREEFORM_SUBTYPE_GUID", 0x19: "RAW", 0x1B: "PEI_DEPEX",
    0x1C: "SMM_DEPEX",
}

EFI_LZMA_GUID = bytes.fromhex("98584eee14395942" "9d6edc7bd79403cf")
PI_STD_GUID = bytes.fromhex("2acbdcdcdbc8474f" "8cb710a31c5c8d5e")


def guid_of(b):
    d1, d2, d3 = struct.unpack_from("<IHH", b, 0)
    return "%08X-%04X-%04X-%s-%s" % (
        d1, d2, d3, b[8:10].hex().upper(), b[10:16].hex().upper())


def ffs_size(blob, pos):
    return blob[pos + 20] | (blob[pos + 21] << 8) | (blob[pos + 22] << 16)


def find_fv(blob):
    """Return (base, length, header_len) for the first structurally sane FV."""
    start = 0
    while True:
        i = blob.find(b"_FVH", start)
        if i < 0:
            return None
        start = i + 1
        base = i - 0x28
        if base < 0:
            continue
        try:
            length = struct.unpack_from("<Q", blob, base + 0x20)[0]
            hdr_len = struct.unpack_from("<H", blob, base + 0x30)[0]
        except struct.error:
            continue
        if length <= 0 or base + length > len(blob):
            continue
        if hdr_len < 0x38 or hdr_len >= length:
            continue
        return base, length, hdr_len


def lzma_try(payload):
    starts = [0, 13]
    for i, b in enumerate(payload[:64]):
        if b in (0x5D, 0x6D, 0x5E) and i not in starts:
            starts.append(i)
    for skip in starts:
        for fmt in (lzma.FORMAT_ALONE, lzma.FORMAT_AUTO):
            try:
                r = lzma.LZMADecompressor(format=fmt).decompress(payload[skip:])
            except Exception:
                continue
            if r and len(r) > 4096:
                return skip, r
    return None, None


def efi_decompress(payload, uncompressed_len):
    """EDK2's own compressor (LZMA with a 5-byte header, or Tiano).

    Only the payload FREEFORM file needs this: it is wrapped in
    EFI_STANDARD_COMPRESSION, which is not plain .lzma and therefore cannot be
    decoded with the stdlib lzma module.  Everything above it (the outer FV, the
    GUIDED/LZMA DXE volume, every FFS GUID) decodes with the stdlib, so a missing
    uefi_firmware only costs the FAT listing.
    """
    try:
        from uefi_firmware import efi_compressor
    except Exception:
        return None, None
    for start in range(0, 8):
        try:
            out = efi_compressor.EfiDecompress(payload[start:], uncompressed_len)
        except Exception:
            continue
        if out and len(out) > 4096:
            return start, out
    return None, None


def walk_ffs(blob, base, length, hdr_len, label, depth=0):
    pad = "  " * depth
    end = base + length
    pos = base + hdr_len
    while pos + 24 <= end:
        name = blob[pos:pos + 16]
        size = ffs_size(blob, pos)
        if name == b"\xff" * 16:
            if size < 24 or pos + size > end:
                break
            pos += size
            continue
        if size < 24 or pos + size > end:
            print(f"{pad}! desync at 0x{pos:x} (size 0x{size:x})")
            break
        ftype = blob[pos + 18]
        tname = FFS_TYPES.get(ftype, f"0x{ftype:02x}")
        mark = "   <-- PAYLOAD" if name == PAYLOAD_GUID else ""
        print(f"{pad}FFS {guid_of(name)}  {tname:<12} {size:>12,} bytes{mark}")
        walk_sections(blob, pos + 24, pos + size, depth + 1,
                      is_payload=(name == PAYLOAD_GUID))
        pos = (pos + size + 7) & ~7


def walk_sections(blob, start, end, depth, is_payload=False):
    pad = "  " * depth
    pos = start
    while pos + 4 <= end:
        ssize = blob[pos] | (blob[pos + 1] << 8) | (blob[pos + 2] << 16)
        stype = blob[pos + 3]
        if ssize < 4 or pos + ssize > end:
            return
        body = blob[pos:pos + ssize]
        payload = body[4:]
        tname = SEC_TYPES.get(stype, f"0x{stype:02x}")
        extra = ""
        guid = None
        if stype == 0x02:
            guid = body[4:20]
            doff = struct.unpack_from("<H", body, 20)[0]
            attrs = struct.unpack_from("<H", body, 22)[0]
            extra = f" guid {guid_of(guid)} dataoff {doff} attr 0x{attrs:04x}"
            payload = body[doff:]
        print(f"{pad}section {tname:<8}{extra} {ssize:>12,} bytes")

        if stype == 0x17:
            found = find_fv(payload)
            if found:
                b, l, h = found
                print(f"{pad}  -> nested FV at +0x{b:x}, {l:,} bytes")
                walk_ffs(payload, b, l, h, "nested", depth + 2)
        elif stype == 0x02 and guid == EFI_LZMA_GUID:
            skip, dec = lzma_try(payload)
            if dec:
                print(f"{pad}  -> LZMA (skip {skip}): {len(dec):,} bytes")
                found = find_fv(dec)
                if found:
                    b, l, h = found
                    print(f"{pad}  -> FV at +0x{b:x}, {l:,} bytes")
                    walk_ffs(dec, b, l, h, "lzma", depth + 2)
        elif stype == 0x01:
            # EFI_STANDARD_COMPRESSION: u32 uncompressed length, u8 type, then data
            ulen = struct.unpack_from("<I", body, 4)[0]
            ctype = body[8]
            cname = {0: "not compressed", 1: "EFI (LZMA/Tiano)"}.get(ctype, f"0x{ctype:02x}")
            print(f"{pad}  -> COMPRESSION {cname}, uncompressed {ulen:,}")
            dec = body[9:] if ctype == 1 else None
            skip, out = efi_decompress(body[9:], ulen) if ctype == 1 else (None, None)
            if out is None and ctype == 1:
                print(f"{pad}  !! EFI compression needs the uefi_firmware module "
                      f"(pip install uefi_firmware); the FAT volume cannot be listed")
                pos = (pos + ssize + 3) & ~3
                continue
            if out is None and ctype == 1:
                skip, out = lzma_try(payload)
            if out:
                print(f"{pad}  -> inflated (skip {skip}): {len(out):,} bytes")
                # The payload FREEFORM file is a COMPRESS(PI_STD) wrapper whose
                # body starts with a 4-byte FFS-style header prologue
                # (uncompressed length, type, size, checksum) before the RAW
                # section; skip to the first FAT boot record so the volume
                # parses from offset 0.
                start = out.find(b"\xeb\x3c\x90")
                if is_payload and start > 0 and out[start + 54:start + 59] == b"FAT16":
                    print(f"{pad}  -> FAT boot record at +0x{start:x}, "
                          f"volume {len(out) - start:,} bytes")
                    out = out[start:]
                if is_payload:
                    dump_fat(out, depth + 2)
                else:
                    found = find_fv(out)
                    if found:
                        b, l, h = found
                        print(f"{pad}  -> FV at +0x{b:x}, {l:,} bytes")
                        walk_ffs(out, b, l, h, "compr", depth + 2)
        elif stype == 0x19:
            if body[58:63] == b"FAT16" or body[86:91] == b"FAT32" or \
                    body[4:7] in (b"\xeb\x3c\x90", b"\xeb\x3b\x90"):
                print(f"{pad}  -> RAW is a FAT volume, {len(payload):,} bytes")
                dump_fat(payload, depth + 2)
        # EDK2 aligns every section inside an FFS file to 4 bytes; without this
        # the walk loses sync on the first DEPEX whose length is not a multiple
        # of 4 and then reads PE32 data as a section header.
        pos = (pos + ssize + 3) & ~3


def dump_fat(img, depth):
    pad = "  " * depth
    bps = struct.unpack_from("<H", img, 11)[0]
    spc = img[13]
    rsvd = struct.unpack_from("<H", img, 14)[0]
    nfats = img[16]
    root_ents = struct.unpack_from("<H", img, 17)[0]
    total = struct.unpack_from("<H", img, 19)[0] or struct.unpack_from("<I", img, 32)[0]
    fatsz = struct.unpack_from("<H", img, 22)[0] or struct.unpack_from("<I", img, 36)[0]

    fat_off = rsvd * bps
    root_off = (rsvd + nfats * fatsz) * bps
    data_off = root_off + root_ents * 32
    clus = bps * spc

    label = img[43:54].decode("ascii", "replace").strip()
    label = "".join(c if 32 <= ord(c) < 127 else "?" for c in label)
    print(f"{pad}FAT: {bps} B/sector x {spc} = {clus} B/cluster, {total * bps:,} bytes, "
          f"{nfats} FAT(s) x {fatsz} sectors, {root_ents} root entries, "
          f"label {label!r}")

    def nxt(n):
        o = fat_off + n * 2
        return struct.unpack_from("<H", img, o)[0] if o + 2 <= len(img) else 0xFFFF

    def chain(n):
        out = b""
        seen = set()
        while 2 <= n < 0xFFF8 and n not in seen and len(seen) < (1 << 16):
            seen.add(n)
            out += img[data_off + (n - 2) * clus: data_off + (n - 1) * clus]
            n = nxt(n)
        return out

    def listing(entries, tag, d, level=0):
        p = "  " * d
        lfn = []
        for i in range(len(entries) // 32):
            e = entries[i * 32:(i + 1) * 32]
            if len(e) < 32 or e[0] == 0x00:
                return
            if e[0] == 0xE5:
                lfn = []
                continue
            if e[11] == 0x0F:
                chars = e[1:11] + e[14:26] + e[28:32]
                text = chars.decode("utf-16-le", "replace")
                text = text.split("\x00")[0]
                text = "".join(c for c in text if c >= " " and c != "\uffff")
                lfn.append((e[0] & 0x1F, text))
                continue
            base = e[:8].decode("ascii", "replace").rstrip()
            ext = e[8:11].decode("ascii", "replace").rstrip()
            if base in (".", ".."):
                lfn = []
                continue
            attr = e[11]
            first = struct.unpack_from("<H", e, 26)[0]
            size = struct.unpack_from("<I", e, 28)[0]
            long_name = "".join(s for _, s in sorted(lfn)) if lfn else ""
            lfn = []
            if attr & 0x08 and not attr & 0x10:
                # volume label in the root directory: not a file
                print(f"{p}{tag}/[{long_name or base}]  <- volume label")
                continue
            name = long_name or (base + ("." + ext if ext else ""))
            if attr & 0x10:
                print(f"{p}{tag}/{name}/")
                if level < 6:
                    listing(chain(first), tag + "/" + name, d + 1, level + 1)
            else:
                hint = ""
                if first >= 2:
                    head = chain(first)[:8]
                    if head[:4] == b"\xcf\xfa\xed\xfe":
                        hint = "   <- Mach-O arm64"
                    elif head[:2] == b"MZ":
                        hint = "   <- PE32 EFI"
                print(f"{p}{tag}/{name:<28} {size:>12,} bytes{hint}")

    listing(img[root_off:root_off + root_ents * 32], "", depth + 1)


def main(argv):
    path = argv[1]
    dump = argv[argv.index("--dump-fat") + 1] if "--dump-fat" in argv else None

    data = open(path, "rb").read()
    assert data[:8] == b"ANDROID!", "not an Android boot image"
    page = struct.unpack_from("<I", data, 36)[0]
    ksize = struct.unpack_from("<I", data, 8)[0]
    print(f"boot.img   : {len(data):,} bytes, page {page}, kernel segment {ksize:,}")

    seg = data[page:page + ksize]
    dec = zlib.decompressobj(16 + zlib.MAX_WBITS)
    fv = dec.decompress(seg) + dec.flush()
    print(f"kernel seg : gzip -> {len(fv):,} bytes")

    found = find_fv(fv)
    if not found:
        print("no sane FV found")
        return 1
    b, l, h = found
    print(f"outer FV   : +0x{b:x}, {l:,} bytes, header 0x{h:x}\n")
    walk_ffs(fv, b, l, h, "outer")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))