## @file
#  Meizu15Pkg platform description.
#
#  Every component is a prebuilt binary INF; there is no source to compile here.
#  That is deliberate: these drivers are the phone's own signed stock modules and
#  must not be recompiled.
#
[Defines]
  PLATFORM_NAME                  = Meizu15Pkg
  PLATFORM_GUID                  = f3046e0d-5723-5e35-bc7a-8261f1c082a2
  PLATFORM_VERSION               = 0.1
  DSC_SPECIFICATION              = 0x00010005
  OUTPUT_DIRECTORY               = Build/Meizu15Pkg
  SUPPORTED_ARCHITECTURES        = AARCH64
  BUILD_TARGETS                  = DEBUG|RELEASE
  SKUID_IDENTIFIER               = DEFAULT
  FLASH_DEFINITION               = Meizu15Pkg/Meizu15Pkg.fdf

[LibraryClasses]

[Components]
  Meizu15Pkg/Drivers/DxeCore/DxeCore.inf
  Meizu15Pkg/Drivers/ArmCpuDxe/ArmCpuDxe.inf
  Meizu15Pkg/Drivers/RuntimeDxe/RuntimeDxe.inf
  Meizu15Pkg/Drivers/SecurityDxe/SecurityDxe.inf
  Meizu15Pkg/Drivers/SecRSADxe/SecRSADxe.inf
  Meizu15Pkg/Drivers/ASN1X509Dxe/ASN1X509Dxe.inf
  Meizu15Pkg/Drivers/VerifiedBootDxe/VerifiedBootDxe.inf
  Meizu15Pkg/Drivers/TzDxe/TzDxe.inf
  Meizu15Pkg/Drivers/MdtpDxe/MdtpDxe.inf
  Meizu15Pkg/Drivers/WatchdogTimer/WatchdogTimer.inf
  Meizu15Pkg/Drivers/CapsuleRuntimeDxe/CapsuleRuntimeDxe.inf
  Meizu15Pkg/Drivers/ReportStatusCodeRouterRuntimeDxe/ReportStatusCodeRouterRuntimeDxe.inf
  Meizu15Pkg/Drivers/StatusCodeHandlerRuntimeDxe/StatusCodeHandlerRuntimeDxe.inf
  Meizu15Pkg/Drivers/EmuVariableRuntimeDxe/EmuVariableRuntimeDxe.inf
  Meizu15Pkg/Drivers/EmbeddedMonotonicCounter/EmbeddedMonotonicCounter.inf
  Meizu15Pkg/Drivers/SimpleTextInOutSerial/SimpleTextInOutSerial.inf
  Meizu15Pkg/Drivers/ResetRuntimeDxe/ResetRuntimeDxe.inf
  Meizu15Pkg/Drivers/RealTimeClock/RealTimeClock.inf
  Meizu15Pkg/Drivers/MetronomeDxe/MetronomeDxe.inf
  Meizu15Pkg/Drivers/PrintDxe/PrintDxe.inf
  Meizu15Pkg/Drivers/DevicePathDxe/DevicePathDxe.inf
  Meizu15Pkg/Drivers/ConPlatformDxe/ConPlatformDxe.inf
  Meizu15Pkg/Drivers/ConSplitterDxe/ConSplitterDxe.inf
  Meizu15Pkg/Drivers/GraphicsConsoleDxe/GraphicsConsoleDxe.inf
  Meizu15Pkg/Drivers/HiiDatabase/HiiDatabase.inf
  Meizu15Pkg/Drivers/FontDxe/FontDxe.inf
  Meizu15Pkg/Drivers/EnvDxe/EnvDxe.inf
  Meizu15Pkg/Drivers/ArmGicDxe/ArmGicDxe.inf
  Meizu15Pkg/Drivers/ArmTimerDxe/ArmTimerDxe.inf
  Meizu15Pkg/Drivers/ChipInfo/ChipInfo.inf
  Meizu15Pkg/Drivers/GlinkDxe/GlinkDxe.inf
  Meizu15Pkg/Drivers/SmemDxe/SmemDxe.inf
  Meizu15Pkg/Drivers/ULogDxe/ULogDxe.inf
  Meizu15Pkg/Drivers/NpaDxe/NpaDxe.inf
  Meizu15Pkg/Drivers/DiskIoDxe/DiskIoDxe.inf
  Meizu15Pkg/Drivers/PartitionDxe/PartitionDxe.inf
  Meizu15Pkg/Drivers/Fat/Fat.inf
  Meizu15Pkg/Drivers/EnglishDxe/EnglishDxe.inf
  Meizu15Pkg/Drivers/FvSimpleFileSystem/FvSimpleFileSystem.inf
  Meizu15Pkg/Drivers/DALSys/DALSys.inf
  Meizu15Pkg/Drivers/ClockDxe/ClockDxe.inf
  Meizu15Pkg/Drivers/HWIODxeDriver/HWIODxeDriver.inf
  Meizu15Pkg/Drivers/I2C/I2C.inf
  Meizu15Pkg/Drivers/SPI/SPI.inf
  Meizu15Pkg/Drivers/SPMI/SPMI.inf
  Meizu15Pkg/Drivers/SdccDxe/SdccDxe.inf
  Meizu15Pkg/Drivers/UFSDxe/UFSDxe.inf
  Meizu15Pkg/Drivers/DALTLMM/DALTLMM.inf
  Meizu15Pkg/Drivers/DisplayDxe/DisplayDxe.inf
  Meizu15Pkg/Drivers/PlatformInfoDxeDriver/PlatformInfoDxeDriver.inf
  Meizu15Pkg/Drivers/ButtonsDxe/ButtonsDxe.inf
  Meizu15Pkg/Drivers/PmicDxe/PmicDxe.inf
  Meizu15Pkg/Drivers/ChargerExDxe/ChargerExDxe.inf
  Meizu15Pkg/Drivers/QcomChargerDxeLA/QcomChargerDxeLA.inf
  Meizu15Pkg/Drivers/AdcDxe/AdcDxe.inf
  Meizu15Pkg/Drivers/TsensDxe/TsensDxe.inf
  Meizu15Pkg/Drivers/LimitsDxe/LimitsDxe.inf
  Meizu15Pkg/Drivers/MpPowerDxe/MpPowerDxe.inf
  Meizu15Pkg/Drivers/UsbfnDwc3Dxe/UsbfnDwc3Dxe.inf
  Meizu15Pkg/Drivers/UsbMsdDxe/UsbMsdDxe.inf
  Meizu15Pkg/Drivers/UsbDeviceDxe/UsbDeviceDxe.inf
  Meizu15Pkg/Drivers/UsbConfigDxe/UsbConfigDxe.inf
  Meizu15Pkg/Drivers/HashDxe/HashDxe.inf
  Meizu15Pkg/Drivers/RngDxe/RngDxe.inf
  Meizu15Pkg/Drivers/QcomBds/QcomBds.inf
  Meizu15Pkg/Drivers/QdssDxe/QdssDxe.inf
  Meizu15Pkg/Drivers/QcomChargerApp/QcomChargerApp.inf
