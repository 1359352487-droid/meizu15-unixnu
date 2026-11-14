/** @file
  Hand the UniXNU payload to DarwinBoot.

  UniXNU ships a full menu application (UniXNUPkg/Application/UniXNU) that reads
  unixnu.toml, draws a UI and resolves entries. That application pulls in
  UniXNULib, which needs MdePkg's FdtLib and gFdtTableGuid -- neither exists in the
  edk2 revision edk2-msm pins. This launcher is the small equivalent: it finds the
  volume that carries the payload, reads the ADT beside the kernel, and calls the
  loader protocol DarwinBoot installs.

  Deliberately dependency-free apart from MdePkg: SimpleFileSystem, LoadedImage,
  and the two UniXNU definitions the loader protocol needs.

  Copyright (c) 2026, the Meizu 15 UniXNU port.
  SPDX-License-Identifier: BSD-2-Clause-Patent
**/

#include <Uefi.h>
#include <Guid/FileInfo.h>
#include <Library/BaseMemoryLib.h>
#include <Library/DevicePathLib.h>
#include <Library/MemoryAllocationLib.h>
#include <Library/PrintLib.h>
#include <Library/UefiApplicationEntryPoint.h>
#include <Library/UefiBootServicesTableLib.h>
#include <Library/UefiLib.h>
#include <Protocol/LoadedImage.h>
#include <Protocol/SimpleFileSystem.h>
#include <Protocol/UniXNULoader.h>

#define KERNEL_PATH  L"\\EFI\\UniXNU\\Payloads\\kernel.macho"
#define ADT_PATH     L"\\EFI\\UniXNU\\Payloads\\kernel.macho.adt"

/**
  Read a whole file into a pool buffer.

  @retval EFI_NOT_FOUND  The file is not on this volume.
**/
STATIC
EFI_STATUS
ReadWholeFile (
  IN  EFI_FILE_PROTOCOL  *Root,
  IN  CONST CHAR16       *Path,
  OUT VOID               **Buffer,
  OUT UINTN              *Size
  )
{
  EFI_STATUS         Status;
  EFI_FILE_PROTOCOL  *File;
  EFI_FILE_INFO      *Info;
  UINTN              InfoSize;
  UINTN              ReadSize;

  *Buffer = NULL;
  *Size   = 0;

  Status = Root->Open (Root, &File, (CHAR16 *)Path, EFI_FILE_MODE_READ, 0);
  if (EFI_ERROR (Status)) {
    return EFI_NOT_FOUND;
  }

  InfoSize = SIZE_OF_EFI_FILE_INFO + 512;
  Info     = AllocatePool (InfoSize);
  if (Info == NULL) {
    File->Close (File);
    return EFI_OUT_OF_RESOURCES;
  }

  Status = File->GetInfo (File, &gEfiFileInfoGuid, &InfoSize, Info);
  if (EFI_ERROR (Status)) {
    FreePool (Info);
    File->Close (File);
    return Status;
  }

  *Size   = (UINTN)Info->FileSize;
  *Buffer = AllocatePool (*Size);
  FreePool (Info);
  if (*Buffer == NULL) {
    File->Close (File);
    return EFI_OUT_OF_RESOURCES;
  }

  ReadSize = *Size;
  Status   = File->Read (File, &ReadSize, *Buffer);
  File->Close (File);
  if (EFI_ERROR (Status) || (ReadSize != *Size)) {
    FreePool (*Buffer);
    *Buffer = NULL;
    *Size   = 0;
    return EFI_DEVICE_ERROR;
  }

  return EFI_SUCCESS;
}

/**
  Check that a file is on this volume and report its size.

  Probing with GetInfo instead of reading the file matters: the kernel is
  15.8 MB and every volume on the system gets offered to us, so reading it once
  per volume just to test for existence would put 15.8 MB through the FAT stack
  before anything useful happens.

  @retval EFI_NOT_FOUND  The file is not on this volume.
**/
STATIC
EFI_STATUS
FileSize (
  IN  EFI_FILE_PROTOCOL  *Root,
  IN  CONST CHAR16       *Path,
  OUT UINTN              *Size
  )
{
  EFI_STATUS         Status;
  EFI_FILE_PROTOCOL  *File;
  EFI_FILE_INFO      *Info;
  UINTN              InfoSize;

  *Size  = 0;
  Status = Root->Open (Root, &File, (CHAR16 *)Path, EFI_FILE_MODE_READ, 0);
  if (EFI_ERROR (Status)) {
    return EFI_NOT_FOUND;
  }

  InfoSize = SIZE_OF_EFI_FILE_INFO + 512;
  Info     = AllocatePool (InfoSize);
  if (Info == NULL) {
    File->Close (File);
    return EFI_OUT_OF_RESOURCES;
  }

  Status = File->GetInfo (File, &gEfiFileInfoGuid, &InfoSize, Info);
  File->Close (File);
  if (EFI_ERROR (Status)) {
    FreePool (Info);
    return Status;
  }

  *Size = (UINTN)Info->FileSize;
  FreePool (Info);
  return EFI_SUCCESS;
}

/**
  Find the SIMPLE_FILE_SYSTEM handle that carries KERNEL_PATH.
**/
STATIC
EFI_STATUS
FindPayloadVolume (
  OUT EFI_HANDLE  *VolumeHandle,
  OUT UINTN       *KernelSize,
  OUT VOID        **DeviceTree,
  OUT UINTN       *DeviceTreeSize
  )
{
  EFI_STATUS                     Status;
  EFI_HANDLE                     *Handles;
  UINTN                          Count;
  UINTN                          Index;
  EFI_SIMPLE_FILE_SYSTEM_PROTOCOL *Fs;
  EFI_FILE_PROTOCOL              *Root;
  UINTN                          KernelBytes;

  *VolumeHandle   = NULL;
  *KernelSize     = 0;
  *DeviceTree     = NULL;
  *DeviceTreeSize = 0;

  Status = gBS->LocateHandleBuffer (
                  ByProtocol,
                  &gEfiSimpleFileSystemProtocolGuid,
                  NULL,
                  &Count,
                  &Handles
                  );
  if (EFI_ERROR (Status)) {
    return Status;
  }

  for (Index = 0; Index < Count; Index++) {
    Status = gBS->HandleProtocol (Handles[Index], &gEfiSimpleFileSystemProtocolGuid, (VOID **)&Fs);
    if (EFI_ERROR (Status)) {
      continue;
    }

    Root = NULL;
    if (EFI_ERROR (Fs->OpenVolume (Fs, &Root)) || (Root == NULL)) {
      continue;
    }

    KernelBytes = 0;
    if (EFI_ERROR (FileSize (Root, KERNEL_PATH, &KernelBytes))) {
      Root->Close (Root);
      continue;
    }

    *KernelSize = KernelBytes;

    if (EFI_ERROR (ReadWholeFile (Root, ADT_PATH, DeviceTree, DeviceTreeSize))) {
      //
      // No ADT: DarwinBoot can still take the firmware's own device tree, which
      // it reads from the configuration table by itself.
      //
      *DeviceTree     = NULL;
      *DeviceTreeSize = 0;
    }

    Root->Close (Root);
    *VolumeHandle = Handles[Index];
    FreePool (Handles);
    return EFI_SUCCESS;
  }

  FreePool (Handles);
  return EFI_NOT_FOUND;
}

/**
  Entry point: find the payload, then let DarwinBoot boot it.
**/
EFI_STATUS
EFIAPI
Meizu15LauncherEntry (
  IN EFI_HANDLE        ImageHandle,
  IN EFI_SYSTEM_TABLE  *SystemTable
  )
{
  EFI_STATUS                  Status;
  EFI_HANDLE                  Volume;
  UINTN                       KernelSize;
  VOID                        *DeviceTree;
  UINTN                       DeviceTreeSize;
  UNIXNU_LOADER_PROTOCOL      *Loader;
  UNIXNU_LOADER_BOOT_REQUEST  Request;

  Print (L"\r\nMeizu15Launcher: looking for %s\r\n", KERNEL_PATH);

  //
  // Hold the first frame for ten seconds. Whether this launcher is reached at
  // all is the open question for the boot menu, and the panel is the only way to
  // answer it: with this stall the screen says "the launcher ran" even if
  // somebody only glances at the phone afterwards.
  //
  gBS->Stall (10000000);

  Status = FindPayloadVolume (&Volume, &KernelSize, &DeviceTree, &DeviceTreeSize);
  if (EFI_ERROR (Status)) {
    Print (L"Meizu15Launcher: no volume carries the payload (%r)\r\n", Status);
    gBS->Stall (8000000);
    return Status;
  }

  Print (
    L"Meizu15Launcher: payload volume found, kernel %u bytes, ADT %u bytes\r\n",
    (UINT32)KernelSize,
    (UINT32)DeviceTreeSize
    );

  Status = gBS->LocateProtocol (&gUniXNULoaderProtocolGuid, NULL, (VOID **)&Loader);
  if (EFI_ERROR (Status)) {
    Print (L"Meizu15Launcher: DarwinBoot is not in this firmware (%r)\r\n", Status);
    gBS->Stall (8000000);
    return Status;
  }

  Print (L"Meizu15Launcher: loader revision 0x%x, booting\r\n", Loader->Revision);

  //
  // The phone has no serial port and DarwinBoot may reset the machine. Whatever
  // is on the panel when the reset happens is the only record we get, so hold
  // the frame long enough to be read.
  //
  gBS->Stall (6000000);

  ZeroMem (&Request, sizeof (Request));
  Request.Volume         = Volume;
  Request.KernelPath     = KERNEL_PATH;
  Request.BootArgs       = L"";
  Request.DeviceTree     = DeviceTree;
  Request.DeviceTreeSize = DeviceTreeSize;
  Request.Ramdisk        = NULL;
  Request.RamdiskSize    = 0;
  Request.Kexts          = NULL;
  Request.KextCount      = 0;

  Status = Loader->Boot (Loader, &Request);

  //
  // Boot only returns on failure.
  //
  Print (L"Meizu15Launcher: the loader returned %r\r\n", Status);
  gBS->Stall (10000000);
  return Status;
}
