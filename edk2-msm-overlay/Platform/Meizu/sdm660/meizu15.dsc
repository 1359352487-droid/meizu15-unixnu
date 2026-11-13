[Defines]
  VENDOR_NAME                    = Meizu
  PLATFORM_NAME                  = meizu15
  PLATFORM_GUID                  = 7b1e5a02-3c4d-4f18-9a6e-2d5c8b0f1a37
  PLATFORM_VERSION               = 0.1
  DSC_SPECIFICATION              = 0x00010019
  OUTPUT_DIRECTORY               = Build/$(PLATFORM_NAME)
  SUPPORTED_ARCHITECTURES        = AARCH64
  BUILD_TARGETS                  = DEBUG|RELEASE
  SKUID_IDENTIFIER               = DEFAULT
  FLASH_DEFINITION               = Platform/Qualcomm/sdm660/sdm660.fdf
  DEVICE_DXE_FV_COMPONENTS       = Platform/Meizu/sdm660/meizu15.fdf.inc

!include Platform/Qualcomm/sdm660/sdm660.dsc

[BuildOptions.common]
  # -Wno-null-dereference: the ubuntu-latest runner now ships Clang 21, which
  # promotes "indirection of non-volatile null pointer will be deleted, not
  # trap" to a warning, and EDK2 builds every module with -Werror. The first
  # casualty is QcomPkg/Include/Library/FdtParserLib.h:46 inside
  # MemoryInitPeiLib, which has nothing to do with this port:
  #   error: indirection of non-volatile null pointer will be deleted,
  #          not trap [-Werror,-Wnull-dereference]
  GCC:*_*_AARCH64_CC_FLAGS = -DENABLE_SIMPLE_INIT -Wno-null-dereference

[PcdsFixedAtBuild.common]
  # Meizu 15 panel: 1080x1920 (16:9, AMOLED, 480 dpi).
  # Pinned from the live device: wm size -> 1080x1920, wm density -> 480.
  gQcomTokenSpaceGuid.PcdMipiFrameBufferWidth|1080
  gQcomTokenSpaceGuid.PcdMipiFrameBufferHeight|1920

  # Simple Init
  gSimpleInitTokenSpaceGuid.PcdGuiDefaultDPI|480

  gRenegadePkgTokenSpaceGuid.PcdDeviceVendor|"Meizu"
  gRenegadePkgTokenSpaceGuid.PcdDeviceProduct|"15"
  gRenegadePkgTokenSpaceGuid.PcdDeviceCodeName|"meizu15"

# ---------------------------------------------------------------------------
# Payload support for the UniXNU boot chain.
#
# The FAT volume that holds kernel.macho, its ADT and unixnu.toml is baked into the
# firmware volume (see meizu15.fdf.inc) and published as a ramdisk by our own
# driver, because edk2-msm's BootShim passes no initrd and UniXNU's
# UniXNURamDiskDxe therefore has nothing to work with.
#
# Fat + DiskIo + Partition are the standard stack that turns a registered
# ramdisk into a mounted filesystem; RamDiskDxe provides the protocol our driver
# calls; Meizu15PayloadDxe is the glue.
# ---------------------------------------------------------------------------
[Components]
  MdeModulePkg/Universal/Disk/DiskIoDxe/DiskIoDxe.inf
  MdeModulePkg/Universal/Disk/PartitionDxe/PartitionDxe.inf
  FatPkg/EnhancedFatDxe/Fat.inf
  Platform/Meizu/sdm660/Meizu15PayloadDxe/Meizu15PayloadDxe.inf
