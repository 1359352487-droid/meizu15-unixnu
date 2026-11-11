# meizu15 — UniXNU port workspace for the Meizu 15 (M8810, SDM660)

A from-scratch device port of [UniXNU](https://github.com/UniXNU/UniXNU) to the
Meizu 15. Derived in structure from
[UniXNU-Lavender](https://github.com/Chromitsune/UniXNU-Lavender) — the only
SDM660 XNU board definition in existence — with every phone-specific value
re-pinned from this device.

## Status

| Piece | State |
|---|---|
| **Bootloader capability** | ✅ **`fastboot boot` works, unsigned images accepted** — `docs/bootloader.md` |
| Partition backups (12 partitions, 145 MB) | ✅ `dump/` |
| Hardware facts (partitions, DRAM, GIC, reserved-memory, storage) | ✅ `docs/hardware.md` |
| XBL firmware structure (UEFI FVs located and decompressed) | ✅ `docs/xbl-firmware.md` |
| **DXE drivers carved out (67 PE32, 65 named)** | ✅ **`docs/dxe-drivers.md`, `dump/dxe/`** |
| iPhone 8 Plus identity (1080x1920 exact match + Home button) | ✅ `docs/iphone-identity.md` |
| `board.toml` | ✅ pinned: userdata LBA, dram_size, splash region |
| `configs/meizu15_defconfig` | ✅ ARMv8.0-safe (PAN=n) |
| `scripts/make-meizu15-adt.py` | ✅ **runs**: emits a 13984-byte ADT, all fields verified |
| `package/unixnu.toml` | ✅ boot menu |
| UEFI firmware (Meizu15Pkg) | DONE - 67 stock drivers packed into a valid 5 MiB volume (`build-meizu15-uefi`) |
| **Bootable UEFI image (edk2-msm)** | **DONE** - `artifacts/boot-meizu15.img`, 6,225,920 B, header matches stock field for field (`build-meizu15-edk2msm`) |
| XNU kernel build | ❌ needs macOS + Xcode (or GitHub Actions) |
| Native eMMC driver | ❌ **does not exist anywhere**, but only the XNU side needs it (`SdccDxe` covers UEFI) |

> **Start here: [HANDOVER.md](HANDOVER.md)** -- complete state, the seven CI failures
> and their causes, both firmware routes, and the exact test/recovery procedure.
> `test-boot.ps1` runs the `fastboot boot` test without flashing anything.

## What differs from the Redmi Note 7 reference board

| Item | Reference (lavender) | This phone | Consequence |
|---|---|---|---|
| DRAM span | `0xfe4c0000` (6 GB SKU) | **`0xcc7e0000`** (4 GB SKU) | `dram_size` changed; generator guard lowered |
| splash region | `0x9d400000` / `0x2300000` | `0x9d400000` / **`0x2400000`** | address same, size differs (1080x1920 vs 1080x2340) |
| userdata | first `0xEC0100` | first **`0xCC0000`**, last `0xE8F5FDE` | `[userdata]` rewritten |
| panel | NT36672A 1080x2340 | MDSS DSI **1080x1920 AMOLED** | `artwork-device-subtype` 2436 -> 1920 |
| touch | Novatek @ 0x62 | **Synaptics DSX @ i2c-4 0x20** | no driver exists |
| bootloader | Xiaomi ABL | Meizu (unverified) | `fastboot boot` untested |
| board compatible | `xiaomi,lavender` | `meizu,meizu15` | rewritten |

## What is identical (SoC facts, shared by every SDM660)

GICv3 dist `0x17a00000`/`0x10000`, redist `0x17b00000`/`0x100000` stride `0x20000`;
APSS window `0x17000000`/`0x1c00000`; timebase `19200000 Hz`;
eMMC host `qcom,sdhci-msm-v5 @ 0xc0c4000`/`0x1000`;
CPU `0,0x1,0x2,0x3,0x100,0x101,0x102,0x103`; PSCI `smc`;
bootimg geometry (base 0, kernel `0x8000`, ramdisk `0x1000000`, second `0xf00000`,
tags `0x100`, pagesize 4096, header v0).

## Regenerate the ADT

```bash
python3 scripts/make-meizu15-adt.py \
  --dram-size 0xCC7E0000 \
  --owned-memory-size 0x30000000 \
  --cpu-regs "0,0x1,0x2,0x3,0x100,0x101,0x102,0x103" \
  --iphone-identity \
  --chip-id 0x8015 \
  --unique-chip-id <YOUR_PRIVATE_64BIT_ID> \
  --output out/meizu15.adt
```

`--unique-chip-id` is the user's private platform seed. Never commit a real one;
the placeholder used during bring-up validation is not a substitute.

## Next actions

Phase 0 is closed: `fastboot boot` works and unsigned images are accepted, so
nothing has to be flashed. Remaining work is pure engineering.

1. Unpack the DXE modules out of `dump/xbl_dxe_fv.bin` with an EDK2/UEFIExtract
   tool (the hand-written walker only proves they are present).
2. Assemble `edk2-firmware-build/Meizu15Pkg/` from those stock drivers plus the
   open EDK2 core, as `RedmiNote7Pkg` was assembled for lavender.
3. Build XNU with the ARMv8.0 profile on macOS/CI.
4. Write `NativeEmmc` (qcom,sdhci-msm-v5) — the one piece no existing project has.
