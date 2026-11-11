# XBL firmware analysis — Meizu 15 (M8810)

Goal: determine whether the stock UEFI drivers can be carved out of this phone's
bootloader, the way the lavender port carves them out of the Redmi Note 7's
`xbl.elf`. **Answer: yes, they are there.**

All offsets are relative to the dumped partition image in `dump/`.

## 1. xbl_a (partition mmcblk0p1, LBA 131072, 7168 sectors = 3.5 MiB)

The image is a **64-bit little-endian AArch64 ELF** (`\x7fELF`), with 16 program
headers and **no section headers** (`e_shnum = 0`) — so it must be analysed via
program headers only.

| PH | type | offset | vaddr | filesz |
|---|---|---|---|---|
| 2 | LOAD | `0x3000` | `0x14015000` | 350992 |
| 9 | LOAD | `0x64170` | `0x14098000` | 107016 |
| **11** | **LOAD** | **`0x7e380`** | `0x9fc00000` | **1792000** |
| 13 | LOAD | `0x246940` | `0x85e35000` | 172704 |

Segment 11 is the UEFI firmware: a Firmware Volume of exactly `0x1b5800`
(1,792,000 bytes) mapped at `0x9fc00000`.

**FV at file offset `0x7e380`:**
- FileSystemGuid `78e58c8c-3d8a-4f1c-9935-896185c32dd3` = FFS2
- FvLength `0x1b5800`, HeaderLength 72, Attributes `0x000cfeff`

### FFS files inside that volume

| Offset | Type | Size | GUID |
|---|---|---|---|
| `0x07f368` | `PEI_CORE` | 155,672 | `8af09f13-…` |
| `0x0a5380` | `SEC` | 7,504 | `dde58710-…` (sections: `0x15` compression, `0x19`) |
| **`0x0a70d0`** | **`FV_IMAGE`** | **1,415,801** | **`9e21fd93-9c72-4c15-8c4b-e77f1db2d792` (FFS_VOLUME_TOP)** |

The first block of the volume is erased (`0xFF`) free space, so a naive walker
stops immediately — free space must be skipped in 8-byte steps, not treated as
end-of-volume.

## 2. The nested FV_IMAGE is gzip-compressed

Layout of the file payload (offset `0x0a70d0 + 24` = `0xa70e8`, 1,415,801 bytes):

```
+0x00  61 9a 15 02 e9 1f 30 1d 79 be 53 43 91 c2 d2 3b   wrapper GUID
+0x10  c9 59 ae 0c 18 00 01 00                           size fields
+0x18  1f 8b 08 00 00 00 00 00 03 …                      gzip stream
```

`1f 8b 08` is the gzip magic. Decompressing the stream from `+0x18` yields
**3,944,968 bytes**.

The decompressed buffer has 8 bytes of leading padding, then a second FV:

- `_FVH` signature at `+0x30`, so the FV header starts at **`+0x08`**
- FileSystemGuid FFS2, FvLength `0x3c3200` (3,944,960), HeaderLength 72

### FFS files in the decompressed DXE volume

| Offset | Type | Size | GUID |
|---|---|---|---|
| `0x3bcb38` | SEC | 2,845 | `a91d838e-a5fa-4138-825d-455e2303079e` |
| `0x3bd658` | SEC | 4,513 | `a1e235de-e825-4591-9623-c43175811826` |
| `0x3be800` | SEC | 1,479 | `f12a1f3d-4318-a006-5f7f-3faaf6dbb4b5` |
| `0x3bedc8` | SEC | 9,477 | `45fe4b7c-150c-45da-a021-4beb2048ec6f` |
| `0x3c12d0` | SEC | 7,954 | `f780c779-dd7c-47cd-bd1a-5eb414c51704` |

The three large leading blocks (701,137 / 2,107,690 / 29,811 bytes) do not
present valid FFS GUIDs under a naive walk — they are almost certainly further
compressed sub-volumes (`FV_IMAGE`) and need a real EDK2 tool to unpack.

Artifacts kept in `dump/`:
- `xbl_a.img`, `xbl_b.img` — raw partitions
- `xbl_dxe_fvimg.bin` — the 1,415,801-byte FV_IMAGE as stored (gzip inside)
- `xbl_dxe_fv.bin` — the 3,944,968-byte decompressed DXE volume

## 3. Other partitions checked

| Partition | Result |
|---|---|
| `abl` (p18) | one FV at `0x3000` (`0x18000`) holding a single 71,642-byte `FV_IMAGE`; its payload is LZMA-framed (`5d 00 00 00 01`, dict 16 MiB, uncompressed size `0x3e0c8` = 254,152) |
| `toolsfv` (p25) | no `_FVH`, no PE — name is misleading, it is not a driver volume |
| `devcfg` (p31) | no `_FVH`, no PE |
| `devinfo` (p37) | 4 KiB, **entirely zero** — the BL unlock flag is not stored here |
| `sec` (p28) | 16 KiB, mostly non-zero, not yet interpreted |
| `misc` (p43) | first 64 bytes zero — no bootloader control block set |

## 4. boot.img format — matches the reference board exactly

`dump/boot.img` carries the AOSP v0 header (`ANDROID!`), and every load address
matches what the lavender `[bootimg.image]` block declares:

| Field | Value |
|---|---|
| kernel_size | `0x00ac53eb` (11,293,675 B) |
| kernel_addr | **`0x00008000`** |
| ramdisk_size | `0x002d1268` (2,953,832 B) |
| ramdisk_addr | **`0x01000000`** |
| second_size | 0 |
| second_addr | **`0x00f00000`** |
| tags_addr | **`0x00000100`** |
| page_size | **`4096`** |
| header_version | **0** |

Conclusion: a boot image packaged the lavender way will be laid out the way this
bootloader expects. The open question is not the layout but whether the
bootloader accepts a non-Meizu signature.

## 5. Consequences for the port

1. The UEFI path is viable in principle — this phone's XBL does carry a full
   PEI/DXE stack, so `Meizu15Pkg` can be assembled from stock drivers like the
   lavender `RedmiNote7Pkg` was.
2. A proper EDK2/UEFIExtract tool is required to enumerate and repackage the
   DXE modules; the hand-written walker above only proves they exist.
3. `devinfo` being zeroed means the unlock state lives elsewhere (`sec` is the
   next candidate), so restoring a pre-unlock dump will not necessarily
   re-lock the bootloader by itself.
