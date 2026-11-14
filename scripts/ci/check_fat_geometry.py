#!/usr/bin/env python3
"""Fail the build when a FAT volume cannot actually store its own contents.

A FAT volume can be perfectly listable -- names, sizes, directories all present --
and still be unable to hold a large file, because the allocation chain runs past
the end of the FAT table. Nothing in a directory listing shows that, and the
firmware's own FAT driver happily mounts the volume and then walks off the end of
the chain.

That is exactly what happened on this port. `mformat -c 8` (4 KiB clusters) on a
15.5 MiB volume produced

    clusters      3,957
    FAT16 entries 3,072      (12 sectors x 512 / 2)

and kernel.macho alone spans 3,855 clusters. SimpleInit mounted the volume, read
partway through, and died with

    Data abort: Translation fault, third level

right after "Initialize SimpleInit GUI...".

This checks the geometry the volume itself declares, so it catches the problem
from the artefact rather than trusting mformat or the build log.

    check_fat_geometry.py <fat.img>
"""
import struct
import sys


def main(path):
    data = open(path, "rb").read()
    if len(data) < 512:
        print("FAIL: %s is smaller than one sector" % path, file=sys.stderr)
        return 2

    # BIOS Parameter Block at offset 0 (the boot record mformat writes is a
    # standard MBR-style jump, so the BPB starts at 11).
    bytes_per_sector = struct.unpack_from("<H", data, 11)[0]
    sectors_per_cluster = data[13]
    reserved = struct.unpack_from("<H", data, 14)[0]
    num_fats = data[16]
    root_entries = struct.unpack_from("<H", data, 17)[0]
    total16 = struct.unpack_from("<H", data, 19)[0]
    fat_sectors = struct.unpack_from("<H", data, 22)[0]
    total32 = struct.unpack_from("<I", data, 32)[0]

    total_sectors = total16 if total16 else total32
    if not all([bytes_per_sector, sectors_per_cluster, num_fats, fat_sectors, total_sectors]):
        print("FAIL: %s does not look like a FAT boot record" % path, file=sys.stderr)
        return 2

    root_sectors = (root_entries * 32 + bytes_per_sector - 1) // bytes_per_sector
    data_sectors = total_sectors - reserved - num_fats * fat_sectors - root_sectors
    if data_sectors <= 0:
        print("FAIL: %s has no data region" % path, file=sys.stderr)
        return 2

    clusters = data_sectors // sectors_per_cluster
    cluster_bytes = bytes_per_sector * sectors_per_cluster

    # A FAT type is chosen by cluster count, not by the label in the BPB.
    if clusters < 4085:
        fat_type, entry_bytes = "FAT12", 1.5
    elif clusters < 65525:
        fat_type, entry_bytes = "FAT16", 2
    else:
        fat_type, entry_bytes = "FAT32", 4

    fat_bytes = fat_sectors * bytes_per_sector
    entries = int(fat_bytes / entry_bytes)

    print("volume      : %s (%d bytes)" % (path, len(data)))
    print("geometry    : %d B/sector x %d = %d B/cluster"
          % (bytes_per_sector, sectors_per_cluster, cluster_bytes))
    print("layout      : reserved %d, %d FAT x %d sectors, root %d entries (%d sectors)"
          % (reserved, num_fats, fat_sectors, root_entries, root_sectors))
    print("clusters    : %d" % clusters)
    print("FAT type    : %s (by cluster count)" % fat_type)
    print("FAT capacity: %d bytes = %d entries" % (fat_bytes, entries))

    if entries < clusters + 2:
        print(
            "FAIL: the FAT holds %d entries but the volume has %d clusters. "
            "A file spanning more than %d clusters overruns the table, which the "
            "firmware's FAT driver turns into a fault while mounting."
            % (entries, clusters, entries - 2),
            file=sys.stderr,
        )
        print("      Raise the cluster size (-c) so the cluster count drops, "
              "or enlarge the volume.", file=sys.stderr)
        return 1

    print("OK          : %d spare entries" % (entries - clusters - 2))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: check_fat_geometry.py <fat.img>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
