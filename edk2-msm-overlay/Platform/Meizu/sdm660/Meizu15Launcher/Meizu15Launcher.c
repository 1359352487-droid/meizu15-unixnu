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

//
// EFI_FILE_PROTOCOL.Read may return less than requested; 1 MiB keeps each call
// small enough for EnhancedFatDxe's cache to satisfy without falling back to
// repeated page exchanges.
//
#define CHUNK_SIZE   (1024 * 1024)

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
  UINTN              Offset;
  UINTN              Remaining;

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

  //
  // Read in 1 MiB chunks instead of asking for the whole file at once.
  //
  // EFI_FILE_PROTOCOL.Read is allowed to satisfy less than the caller asked for,
  // and EnhancedFatDxe goes through a small cache -- the "FatDiskIo: Cache Page
  // OutBound occurred!" lines on the panel are that cache being walked. A single
  // 15.79 MiB request therefore either comes back short, which the old code
  // treated as EFI_DEVICE_ERROR and returned silently, or takes so long that the
  // boot looks hung. Chunking keeps every call small and makes progress visible.
  //
  Offset    = 0;
  Remaining = *Size;
  while (Remaining > 0) {
    ReadSize = (Remaining > CHUNK_SIZE) ? CHUNK_SIZE : Remaining;
    Status   = File->Read (File, &ReadSize, (UINT8 *)*Buffer + Offset);
    if (EFI_ERROR (Status)) {
      Print (
        L"Meizu15Launcher: read failed at %u of %u bytes (%r)\r\n",
        (UINT32)Offset,
        (UINT32)*Size,
        Status
        );
      File->Close (File);
      FreePool (*Buffer);
      *Buffer = NULL;
      *Size   = 0;
      return Status;
    }

    if (ReadSize == 0) {
      Print (
        L"Meizu15Launcher: file ended at %u of %u bytes\r\n",
        (UINT32)Offset,
        (UINT32)*Size
        );
      File->Close (File);
      FreePool (*Buffer);
      *Buffer = NULL;
      *Size   = 0;
      return EFI_DEVICE_ERROR;
    }

    Offset    += ReadSize;
    Remaining -= ReadSize;

    if ((Offset & 0x003FFFFF) == 0) {
      Print (L"Meizu15Launcher: read %u of %u bytes\r\n", (UINT32)Offset, (UINT32)*Size);
    }
  }

  File->Close (File);
  return EFI_SUCCESS;
}

/**
  Find the SIMPLE_FILE_SYSTEM handle that carries KERNEL_PATH.
**/
STATIC
EFI_STATUS
FindPayloadVolume (
  OUT EFI_HANDLE  *VolumeHandle,
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
  VOID                           *Kernel;
  UINTN                          KernelSize;

  *VolumeHandle   = NULL;
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

    Kernel = NULL;
    KernelSize = 0;
    Status = ReadWholeFile (Root, KERNEL_PATH, &Kernel, &KernelSize);
    if (EFI_ERROR (Status)) {
      Print (L"Meizu15Launcher: volume %u has no kernel (%r)\r\n", (UINT32)Index, Status);
      Root->Close (Root);
      continue;
    }
    Print (L"Meizu15Launcher: volume %u carries a %u byte kernel\r\n",
           (UINT32)Index, (UINT32)KernelSize);

    FreePool (Kernel);

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
  VOID                        *DeviceTree;
  UINTN                       DeviceTreeSize;
  UNIXNU_LOADER_PROTOCOL      *Loader;
  UNIXNU_LOADER_BOOT_REQUEST  Request;

  Print (L"\r\nMeizu15Launcher: looking for %s\r\n", KERNEL_PATH);

  Status = FindPayloadVolume (&Volume, &DeviceTree, &DeviceTreeSize);
  if (EFI_ERROR (Status)) {
    Print (L"Meizu15Launcher: no volume carries the payload (%r)\r\n", Status);
    gBS->Stall (8000000);
    return Status;
  }

  Print (L"Meizu15Launcher: payload volume found, ADT %u bytes\r\n", (UINT32)DeviceTreeSize);

  Status = gBS->LocateProtocol (&gUniXNULoaderProtocolGuid, NULL, (VOID **)&Loader);
  if (EFI_ERROR (Status)) {
    Print (L"Meizu15Launcher: DarwinBoot is not in this firmware (%r)\r\n", Status);
    gBS->Stall (8000000);
    return Status;
  }

  Print (L"Meizu15Launcher: loader revision 0x%x, booting\r\n", Loader->Revision);

  ZeroMem (&Request, sizeof (Request));
  Request.Volume         = Volume;
  Request.KernelPath     = KERNEL_PATH;
  // checkpoint=46 -- before arm_set_user_tbi(), past arm_vm_prot_init().
  //
  // Progress after the PAN fix:
  //
  //   58  reason 2   the function body was entered
  //   52  reason 2   init_ptpages(cpu_tte, gVirtBase, ...) finished
  //   49  reason 0   pmap_bootstrap() was not reached
  //
  // So the page tables are built and the fault sits in the last stretch:
  //
  //   52 1978  after  init_ptpages(cpu_tte, gVirtBase, ...)   reason 2
  //   53 2103  after  arm_vm_prot_init(args)                  <- the 125 lines after 52
  //   46 2147  before arm_set_user_tbi()                      <- here
  //   47 2159  after  kva_active = TRUE
  //   48 2255  before pmap_bootstrap(dynamic_memory_begin)
  //   49 2257  after  pmap_bootstrap(...)                     reason 0
  //
  //   reason 2 at 46   everything up to arm_set_user_tbi() is fine; the fault is in
  //                    arm_set_user_tbi, kva_active = TRUE, or pmap_bootstrap
  //   reason 0 at 46   the fault is in those 125 lines between 52 and 46 -- the
  //                    arm_vm_prot_init block -- and 53 splits them
  //
  // Note what arm_vm_prot_init() does: it applies the final protections to the
  // __TEXT_EXEC / __DATA_CONST segments using the tables init_ptpages just built.
  // This SoC has no KERNEL_INTEGRITY_KTRR/CTRR, so the protection path is the one
  // Apple does not exercise on real hardware, and the segment boundaries it uses
  // come from the Mach-O rather than from a boot-monitor descriptor.
  //
  Request.BootArgs       = L"-v loader_return_after=60 checkpoint=46";
  //
  // DeviceTree must stay NULL here.
  //
  // DarwinBoot rejects a request that carries one:
  //
  //   if ((Request->KextCount != 0) || (Request->DeviceTree != NULL) ||
  //       (Request->DeviceTreeSize != 0) || (DarwinCurrentEl () != 4))
  //   {
  //     Print (L"DARWINBOOT: unsupported inputs or exception level; ...");
  //     return EFI_UNSUPPORTED;
  //   }
  //
  // That is not a bug on its side: it opens KernelPath + ".adt" from the same
  // volume itself and parses it there. Passing our own copy both duplicated the
  // work and tripped the guard, which is exactly the failure seen on the panel:
  //
  //   DARWINBOOT: unsupported kernel for os explained. firmware retained
  //   Meizu15Launcher: the loader returned Unsupported
  //
  // The read above stays, because "ADT 13996 bytes" is worth printing: it proves
  // the file the loader is about to open is present and readable.
  //
  Request.DeviceTree     = NULL;
  Request.DeviceTreeSize = 0;
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
