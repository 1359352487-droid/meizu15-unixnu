## @file
#  Meizu15Pkg platform description.
#
#  Every component is a binary-only INF. There is no driver source here: these
#  are the phone's own stock modules and must not be recompiled.
#
[Defines]
  PLATFORM_NAME                  = Meizu15Pkg
  PLATFORM_GUID                  = e71bdb9c-ed1d-58aa-a620-d102fa092887
  PLATFORM_VERSION               = 0.1
  DSC_SPECIFICATION              = 0x00010005
  OUTPUT_DIRECTORY               = Build/Meizu15Pkg
  SUPPORTED_ARCHITECTURES        = AARCH64
  BUILD_TARGETS                  = DEBUG|RELEASE
  SKUID_IDENTIFIER               = DEFAULT
  FLASH_DEFINITION               = Meizu15Pkg/Meizu15Pkg.fdf

[LibraryClasses]

[Components]
  # (intentionally empty: all modules are embedded via the FDF)
