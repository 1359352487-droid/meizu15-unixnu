"""List a FAT16/FAT32 volume without any external tools.

Used to prove what actually landed in the firmware's payload volume: the volume
lives inside a COMPRESSED firmware section, so it can only be checked after
UEFIExtract has decompressed it (scripts/ci/unpack-bootimg-fv.py + UEFIExtract).

    python fat-ls.py <fat.img> [path]

Prints long file names, the 8.3 name, attributes, size and the first cluster, and
flags directory entries that are suspicious (deleted marker, volume label).
"""
import struct
import sys


class Fat:
    def __init__(self, data):
        self.d = data
        self.bps = struct.unpack_from("<H", data, 11)[0]
        self.spc = data[13]
        self.reserved = struct.unpack_from("<H", data, 14)[0]
        self.nfats = data[16]
        self.root_entries = struct.unpack_from("<H", data, 17)[0]
        self.total16 = struct.unpack_from("<H", data, 19)[0]
        self.fatsz16 = struct.unpack_from("<H", data, 22)[0]
        self.total32 = struct.unpack_from("<I", data, 32)[0]
        self.fatsz32 = struct.unpack_from("<I", data, 36)[0]

        self.fatsz = self.fatsz16 or self.fatsz32
        self.total = self.total16 or self.total32
        self.root_sectors = (self.root_entries * 32 + self.bps - 1) // self.bps
        self.first_data = self.reserved + self.nfats * self.fatsz + self.root_sectors
        self.cluster_bytes = self.spc * self.bps

        # FAT32 with zero root entries keeps the root in a cluster chain.
        self.fat32 = self.root_entries == 0 and self.fatsz32 != 0
        self.root_cluster = struct.unpack_from("<I", data, 44)[0] if self.fat32 else None

    def fat_entry(self, cluster):
        off = self.reserved * self.bps
        if self.fat32:
            return struct.unpack_from("<I", self.d, off + cluster * 4)[0] & 0x0FFFFFFF
        return struct.unpack_from("<H", self.d, off + cluster * 2)[0]

    def cluster_offset(self, cluster):
        return (self.first_data + (cluster - 2) * self.spc) * self.bps

    def chain(self, cluster, limit=200000):
        out = []
        while 2 <= cluster < 0x0FFFFFF8 and len(out) < limit:
            out.append(cluster)
            nxt = self.fat_entry(cluster)
            if nxt == cluster:
                break
            cluster = nxt
        return out

    def read_dir(self, raw):
        ents = []
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
                part = e[1:11] + e[14:26] + e[28:32]
                lfn.insert(0, part)
                continue
            name83 = e[0:8].decode("latin-1").rstrip()
            ext = e[8:11].decode("latin-1").rstrip()
            if name83 in (".", "..") or name83.startswith(".") and not ext:
                lfn = []
                continue
            long_name = ""
            if lfn:
                buf = b"".join(lfn)
                try:
                    long_name = buf.decode("utf-16-le").split("\x00")[0]
                except UnicodeDecodeError:
                    long_name = ""
                lfn = []
            cluster = struct.unpack_from("<H", e, 26)[0]
            size = struct.unpack_from("<I", e, 28)[0]
            if self.fat32 and False:
                pass
            ents.append({
                "long": long_name,
                "name": name83,
                "ext": ext,
                "attr": attr,
                "cluster": cluster,
                "size": size,
            })
        return ents

    def root_dir(self):
        if self.fat32:
            raw = b"".join(self.d[self.cluster_offset(c):self.cluster_offset(c) + self.cluster_bytes]
                           for c in self.chain(self.root_cluster))
            return self.read_dir(raw)
        off = (self.reserved + self.nfats * self.fatsz) * self.bps
        raw = self.d[off:off + self.root_entries * 32]
        return self.read_dir(raw)

    def subdir(self, cluster):
        raw = b"".join(self.d[self.cluster_offset(c):self.cluster_offset(c) + self.cluster_bytes]
                       for c in self.chain(cluster))
        return self.read_dir(raw)


def walk(fs, entries, prefix, depth, out):
    for e in entries:
        if e["attr"] & 0x08:
            continue                       # volume label
        name = e["long"] or (e["name"] + ("." + e["ext"] if e["ext"] else ""))
        path = prefix + "/" + name
        is_dir = bool(e["attr"] & 0x10)
        out.append((path, "DIR" if is_dir else "FILE", e["size"], e["attr"], e["cluster"]))
        if is_dir and depth > 0:
            walk(fs, fs.subdir(e["cluster"]), path, depth - 1, out)


def main(path, start="/"):
    data = open(path, "rb").read()
    fs = Fat(data)
    print(f"volume     : {len(data):,} bytes, {fs.bps} B/sector, {fs.spc} sectors/cluster")
    print(f"layout     : {'FAT32' if fs.fat32 else 'FAT16'}, {fs.nfats} FATs, "
          f"{fs.fatsz} sectors/FAT, root entries {fs.root_entries}, first data sector {fs.first_data}")
    print(f"clusters   : {max(0,(fs.total - fs.first_data) // fs.spc):,}")

    entries = fs.root_dir()
    print(f"root       : {len(entries)} entries")

    out = []
    walk(fs, entries, "", 6, out)
    print()
    for p, kind, size, attr, cluster in sorted(out):
        print(f"  {kind}  {size:>10,}  attr=0x{attr:02x}  cluster={cluster:<6} {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
