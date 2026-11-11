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
  GCC:*_*_AARCH64_CC_FLAGS = -DENABLE_SIMPLE_INIT

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
