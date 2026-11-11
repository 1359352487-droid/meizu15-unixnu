# DXE drivers carved from this phone's XBL

Source: `dump/xbl_dxe_fv.bin` (the gzip-decompressed inner volume of the
`FV_IMAGE` found in `xbl_a`). Parsed with `uefi_firmware` 1.16 after skipping the
8-byte `EFI_GUIDED_SECTION` preamble; the volume header validates
(`valid_header = True`, FFS2, 3,944,960 bytes, block 61640×64).

**67 PE32 images extracted to `dump/dxe/`, 3.73 MB total.**
65 are DXE drivers, 1 is the DXE core, 1 is an application. The 6 remaining
files are configuration blobs, not code.

This is the raw material for `Meizu15Pkg`: the same role `xbl.elf` played for
the lavender `RedmiNote7Pkg`.

## Drivers that matter for the port

| Driver | Size | Why it matters |
|---|---|---|
| `SdccDxe` | 102,400 | **eMMC / SD controller.** Provides block I/O over `sdhci@c0c4000`, so the UEFI side can read the ESP on `userdata` without writing an eMMC driver first |
| `DisplayDxe` | 225,280 | MDSS DSI panel init — the framebuffer UniXNU needs to draw on |
| `PmicDxe` | 208,896 | PM660/PM660A regulators; every other driver's power rails come from here |
| `DALSys` | 163,840 | Qualcomm DAL infrastructure that most stock drivers sit on |
| `ClockDxe` | 122,880 | clocks for display, storage, USB |
| `QcomBds` | 77,824 | Boot Device Selection — the logic that decides what to launch |
| `ButtonsDxe` | 36,864 | power/volume keys, and the Home key GPIO is adjacent |
| `DALTLMM` | 36,864 | GPIO pin muxing |
| `I2C` / `SPI` / `SPMI` | 45,056 / 45,172 / 32,818 | buses for touch, PMIC, sensors |
| `DiskIoDxe` + `PartitionDxe` + `Fat` | 32,768 / 45,114 / 61,480 | block stack: device → partitions → FAT |
| `ArmCpuDxe` / `ArmGicDxe` / `ArmTimerDxe` | 45,016 / 32,844 / 28,752 | generic ARM64 platform, same code every port uses |
| `UFSDxe` | 90,182 | present but **unused** — this phone ships eMMC |

## Core and infrastructure

`DxeCore` (143,408), `RuntimeDxe`, `SecurityDxe`, `SecRSADxe`, `ASN1X509Dxe`,
`VerifiedBootDxe`, `TzDxe`, `MdtpDxe`, `HashDxe`, `RngDxe`,
`CapsuleRuntimeDxe`, `ReportStatusCodeRouterRuntimeDxe`,
`StatusCodeHandlerRuntimeDxe`, `ResetRuntimeDxe`, `WatchdogTimer`,
`EmbeddedMonotonicCounter`, `RealTimeClock`, `MetronomeDxe`,
`PlatformInfoDxeDriver`, `ChipInfo`, `GlinkDxe`, `SmemDxe`, `ULogDxe`,
`NpaDxe`, `LimitsDxe`, `TsensDxe`, `AdcDxe`, `MpPowerDxe`, `ChargerExDxe`,
`QcomChargerDxeLA`, `QdssDxe`, `HWIODxeDriver`.

Console/filesystem: `ConPlatformDxe`, `ConSplitterDxe`, `GraphicsConsoleDxe`,
`SimpleTextInOutSerial`, `FontDxe`, `HiiDatabase`, `DevicePathDxe`,
`EnglishDxe`, `PrintDxe`, `EmuVariableRuntimeDxe`, `EnvDxe`,
`FvSimpleFileSystem`.

USB: `UsbfnDwc3Dxe`, `UsbMsdDxe`, `UsbDeviceDxe`, `UsbConfigDxe`.

Application: `QcomChargerApp` (53,312, `APPDXE`).

## Non-code files

| GUID | Size | Content |
|---|---|---|
| `a91d838e-…` | 2,845 | `BDS_Menu.cfg` |
| `a1e235de-…` | 4,513 | `SecParti.cfg` |
| `f12a1f3d-…` | 1,479 | `tracer_event.cfg` |
| `45fe4b7c-…` | 9,477 | `QcomChargerCfg.cfg` |
| `f780c779-…` | 7,954 | `BATTERY.PROVISION` |

## Consequence: the eMMC problem is smaller than it looked

Earlier I called `NativeEmmc` (XNU side) the single biggest blocker. That is
still true **inside XNU**, but the UEFI side now has a working eMMC block driver
in binary form: `SdccDxe`. That means:

1. The UEFI firmware can mount the FAT ESP on `userdata` and load UniXNU,
   DarwinBoot, the kernel and the ADT — no new storage code required to reach
   the boot menu.
2. Only after XNU takes over does a native eMMC driver become necessary, because
   XNU cannot use a UEFI driver in its own I/O stack.

So the boot-to-menu milestone is reachable with stock binaries, and the eMMC
work is deferred to the point where iOS needs a root filesystem.

## How these get into `Meizu15Pkg`

EDK2 accepts prebuilt binaries through an INF wrapper that names a
`UEFI_IFR`/`PE32` binary and its GUID (`[Binaries] ... PE32|SdccDxe.efi`). The
lavender package was assembled the same way from its carved drivers. The pieces
needed are:

```
edk2-firmware-build/
  Meizu15Pkg/
    Meizu15Pkg.dec
    Meizu15Pkg.dsc          # [Components] lists each INF wrapper
    Meizu15Pkg.fdf          # [FV.*] lists each module for inclusion
    Drivers/
      SdccDxe/SdccDxe.inf   # Binaries -> the extracted .efi, GUID -> known
      DisplayDxe/...
      ...one directory per driver whose INF GUID is already known from the
         FFS header (see the table above)
```

Each extracted `.efi` keeps the FFS GUID it was published under, so the INF
`FILE_GUID` can be filled in exactly instead of guessed.
