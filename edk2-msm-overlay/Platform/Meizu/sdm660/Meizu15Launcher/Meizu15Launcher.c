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
  //
  // -v, because XNU is silent without it. loader_return_after asks hakios-xnu to
  // hand control back to the loader after that many seconds whether or not
  // anything went wrong, which is the only way to read a log from a board with
  // no serial port.
  //
  // The command line lands in boot_args.CommandLine, which is 1024 bytes at
  // offset 0x6c and matches XNU's pexpert/pexpert/arm64/boot.h layout (Revision,
  // Version, four uint64s, a 48-byte Boot_Video of six unsigned long, then
  // machineType, deviceTreeP, deviceTreeLength, CommandLine). DarwinBoot's
  // BootArgs.h asserts those offsets, so both arguments reach
  // PE_parse_boot_argn intact.
  //
  //
  // loader_return_after is a bring-up aid, not a boot argument anyone ships.
  //
  // It asks hakios-xnu to hand control back after that many seconds whatever the
  // kernel is doing, which is the only way to read a log from a board with no
  // serial port. Fifteen proved the channel works: the panel showed
  //
  //     kernel returned: reason 0, log 192 bytes
  //     --- kernel message buffer ---
  //     [  65.892252]: vm_cache_geometry_colors: 16
  //
  // so the next question is what the kernel does when nothing interrupts it. A
  // longer window is the cheapest way to ask: the timer fires only if the kernel
  // is still alive at that point, and if it panics instead the panic path appends
  // the panic buffer to the same log and returns anyway.
  //
  //   still returns, log longer   the kernel ran on and got further
  //   still returns, log same     it reached the same place and waited
  //   nothing at all              it died without reaching either path -- the
  //                               panel then shows only the bands, which is
  //                               itself the answer
  //
  // Sixty seconds is comfortably past the point where a working early-boot would
  // have moved on, and short enough to keep each attempt tolerable.
  //
  //
  // checkpoint=17, not 10, and the reason is a hard constraint in the kernel.
  //
  // loader_jump() refuses to hand control back unless the translation regime can
  // reach the stub:
  //
  //     tcr  = TCR_EL1;
  //     t0sz = tcr & 0x3f;
  //     if (t0sz < 25 || t0sz > 33 || (stub >> (64 - t0sz)) != 0) return;
  //
  // The panel prints TCR_EL1 = 0x280803518 on the way in, so t0sz is 24 -- below
  // the floor. Every checkpoint that fires before arm_vm_init() rebuilds TCR_EL1
  // is therefore accepted by loader_checkpoint() and then silently dropped by
  // loader_jump(). That is why checkpoint=10 produced a boot with all three bands
  // and no report at all.
  //
  // 17 sits immediately after arm_vm_init(), by which point the tables and TCR are
  // the kernel's own, so the check passes. It is the first checkpoint in the series
  // that can actually return.
  //
  //   17 fires   arm_vm_init() completed; whatever is wrong is later
  //   17 silent  the kernel dies between arm_init's start and arm_vm_init, which
  //              narrows it to the window that already shows do_cacheid()
  //
  //
  // Back to no checkpoint, with the diagnostics that make a silent stall speak.
  //
  // checkpoint=17 came back as reason 0, i.e. that checkpoint never fired, which
  // places the kernel before arm_vm_init(). Every useful checkpoint in that half
  // of arm_init (10, 11, 30..34) is unreachable for a different reason:
  // loader_jump() refuses to return while TCR_EL1's t0sz is below 25, and the
  // kernel only raises it to 25 inside arm_vm_init(). The panel prints
  // TCR_EL1 = 0x280803518 on the way in, so t0sz is 24 -- every early checkpoint
  // is accepted by loader_checkpoint() and then dropped.
  //
  // Rather than rebuild the kernel to relax that floor, first check whether the
  // kernel is stalling on purpose. arm_init calls
  // platform_stall_panic_or_spin(PLATFORM_STALL_XNU_LOCATION_ARM_INIT) inside
  // #if (DEVELOPMENT || DEBUG), and this kernel is a DEVELOPMENT build -- if it
  // takes the spin branch the machine goes quiet at exactly the observed point,
  // with no log and no panic.
  //
  //   xnu-stall=no    take the other branch
  //   debug=0x14e    turn on the debug flags that make arm_init chatter
  //
  // Both are harmless if the names mean nothing to this kernel: unknown boot-args
  // are ignored, so a boot that behaves exactly as before still answers the
  // question by elimination.
  //
  //
  // checkpoint=11, the first probe after the point where output stops.
  //
  // The last boot settled two things: the kernel inside the image really is the
  // one with loader_jump's t0sz floor lowered (sha256 167066da... read back out
  // of the payload FAT), and it still stops right after do_cacheid() with
  // exactly 192 bytes of log. So relaxing the floor did not, by itself, make the
  // early probes work -- that boot carried no checkpoint= at all, which is why.
  //
  // Number choice matters here:
  //
  //   10  sits before cpu_data_init(), and the panel already proves do_cacheid()
  //       (inside cpu_data_init) ran. 10 would fire no matter what, so it says
  //       almost nothing.
  //   11  sits after PE_init_platform(), and the log shows nothing at all after
  //       do_cacheid(). Whether 11 fires splits that gap in two.
  //
  //   reason 2 + checkpoint 11   PE_init_platform() completed; the fault is later
  //   reason 0                   it never got there; the fault is between
  //                              cpu_data_init() and PE_init_platform()
  //
  Request.BootArgs       = L"-v loader_return_after=60 checkpoint=11";
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
