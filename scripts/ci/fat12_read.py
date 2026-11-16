"""Read files out of the payload FAT, which is FAT12 despite its type string.

The volume announces itself as FAT12 in the extended BPB but fat_ls.py decides
between 12/16/32 by looking at the root-entry and FAT-size fields, which puts it
on FAT16. That reads the wrong nibble boundaries: cluster 6 comes back as 0xA009
instead of 7, and the chain dies after a few clusters -- which is how a 15.8 MB
kernel got reported as an 8 KB file.

This module reads FAT12 chains correctly and is used for the "what kernel is
really inside the image" check.

    python fat12_read.py <fat.img> <first_cluster> <size> <out>
"""
import hashlib
import os
import struct
import sys


class Fat12:
    def __init__(self, data):
        self.d = data
        self.bps = struct.unpack_from("<H", data, 11)[0]
        self.spc = data[13]
        self.reserved = struct.unpack_from("<H", data, 14)[0]
        self.nfats = data[16]
        self.root_entries = struct.unpack_from("<H", data, 17)[0]
        self.fatsz = struct.unpack_from("<H", data, 22)[0] or struct.unpack_from("<I", data, 36)[0]
        self.cluster_bytes = self.spc * self.bps
        root_sectors = (self.root_entries * 32 + self.bps - 1) // self.bps
        self.first_data = self.reserved + self.nfats * self.fatsz + root_sectors
        self.fat_off = self.reserved * self.bps

    def entry(self, cluster):
        """FAT12 packs two 12-bit entries into three bytes."""
        base = self.fat_off + cluster + cluster // 2
        value = struct.unpack_from("<H", self.d, base)[0]
        return (value >> 4) if (cluster & 1) else (value & 0x0FFF)

    def chain(self, cluster, limit=200000):
        out = []
        seen = set()
        while 2 <= cluster < 0xFF8 and len(out) < limit and cluster not in seen:
            out.append(cluster)
            seen.add(cluster)
            cluster = self.entry(cluster)
        return out

    def offset(self, cluster):
        return (self.first_data + (cluster - 2) * self.spc) * self.bps

    def read(self, first, size):
        clusters = self.chain(first)
        blob = b"".join(
            self.d[self.offset(c):self.offset(c) + self.cluster_bytes] for c in clusters
        )
        return blob[:size], len(clusters)

    def entries(self, raw):
        out = []
        lfn = []
        for i in range(0, len(raw), 32):
            e = raw[i:i + 32]
            if len(e) < 32 or e[0] == 0x00:
                break
            if e[0] == 0xE5:
                lfn = []
                continue
            attr = e[11]
            if attr == 0x0F:
                lfn.insert(0, e[1:11] + e[14:26] + e[28:32])
                continue
            name = e[0:8].decode("latin-1").rstrip()
            ext = e[8:11].decode("latin-1").rstrip()
            long_name = ""
            if lfn:
                try:
                    long_name = b"".join(lfn).decode("utf-16-le").split("\x00")[0]
                except Exception:
                    long_name = ""
                lfn = []
            if name in (".", ".."):
                continue
            out.append({
                "long": long_name,
                "name": name,
                "ext": ext,
                "attr": attr,
                "cluster": struct.unpack_from("<H", e, 26)[0],
                "size": struct.unpack_from("<I", e, 28)[0],
            })
        return out

    def root(self):
        off = (self.reserved + self.nfats * self.fatsz) * self.bps
        return self.entries(self.d[off:off + self.root_entries * 32])

    def subdir(self, cluster):
        clusters = self.chain(cluster)
        raw = b"".join(
            self.d[self.offset(c):self.offset(c) + self.cluster_bytes] for c in clusters
        )
        return self.entries(raw)


def walk(volume, entries, prefix, out):
    for ent in entries:
        if ent["attr"] & 0x08 and not (ent["attr"] & 0x10):
            continue
        name = ent["long"] or (ent["name"] + ("." + ent["ext"] if ent["ext"] else ""))
        path = prefix + "/" + name
        if ent["attr"] & 0x10:
            if ent["cluster"] >= 2:
                walk(volume, volume.subdir(ent["cluster"]), path, out)
        else:
            blob, clusters = volume.read(ent["cluster"], ent["size"])
            out.append((path, ent["size"], clusters, hashlib.sha256(blob).hexdigest(), blob))


def main(argv):
    data = open(argv[1], "rb").read()
    volume = Fat12(data)
    files = []
    walk(volume, volume.root(), "", files)

    print("%d files" % len(files))
    print()
    for path, size, clusters, digest, _ in files:
        print("%-46s %10d  %5d cl  %s" % (path, size, clusters, digest))

    target = os.path.join(os.path.dirname(argv[1]), "kernel.from-image.macho")
    for path, size, clusters, digest, blob in files:
        if path.upper().endswith("KERNEL.MACHO") and size > 1000000:
            open(target, "wb").write(blob)
            print()
            print("kernel -> %s" % target)
            print("  size    %d" % size)
            print("  clusters %d" % clusters)
            print("  sha256  %s" % digest)
            if len(blob) > 0x11E0:
                print("  @0x11D0 %s" % blob[0x11D0:0x11E0].hex())
            break
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
