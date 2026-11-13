/** @file
  Publish the UniXNU payload volume as a read-only block device.

  Why this driver exists: UniXNU's own UniXNURamDiskDxe takes the initrd address
  from a Mu-Silicium entry-argument HOB and from /chosen/linux,initrd-start in the
  device tree.  edk2-msm's BootShim is 72 lines of assembly that does neither, so
  on this platform the FAT payload never reaches the firmware and SimpleInit has
  nothing to boot.

  Instead, the FAT image is baked into the firmware volume as a FREEFORM file with
  a compressed RAW section (see meizu15.fdl.inc).  This driver walks every FV,
  finds that file, asks FirmwareVolume2 to hand back the *decompressed* RAW
  section, and registers the bytes with RamDiskLib.  FatDxe then mounts it and
  SimpleInit/DarwinBoot find /EFI/UniXNU/ on it.

  Consequences: the ESP travels inside boot.img (no ramdisk segment, no partition
  table change, nothing written to the phone's storage) and its size is bounded by
  the FFS file size limit (24-bit field, 16 MiB), which is why the section is
  compressed -- a 32 MiB FAT volume gzips to well under that.

  Copyright (c) 2026, the Meizu 15 UniXNU port.
  SPDX-License-Identifier: BSD-2-Clause-Patent
**/

#include <Uefi.h>
#include <Library/BaseMemoryLib.h>
#include <Library/DebugLib.h>
#include <Library/MemoryAllocationLib.h>
#include <Library/RamDiskLib.h>
#include <Library/UefiBootServicesTableLib.h>
#include <Protocol/FirmwareVolume2.h>

//
// Must match the FILE FREEFORM GUID in meizu15.fdl.inc.
//
STATIC CONST EFI_GUID  mPayloadFileGuid = {
  0x3f1a9c22, 0x5b41, 0x4a0e, { 0x9c, 0x77, 0x2d, 0x61, 0x8b, 0x4f, 0xe0, 0x1a }
};

// A stable GUID so re-registration on a warm reset replaces the volume instead of
// stacking a second one on top of it.
STATIC CONST EFI_GUID  mPayloadDiskGuid = {
  0x9b2d7c14, 0xe0a3, 0x4f68, { 0xb5, 0x22, 0x7d, 0x91, 0x3c, 0x08, 0xa6, 0x54 }
};

#define PAYLOAD_FILE_NAME  L"UniXNU payload volume"

/**
  Find the FREEFORM file carrying the payload volume in one firmware volume.

  @param[in]  Fv        Firmware volume to search.
  @param[out] Buffer    Receives the decompressed RAW section (pool allocated).
  @param[out] Size      Receives its size in bytes.

  @retval EFI_SUCCESS   The payload was found and read.
  @retval EFI_NOT_FOUND No FREEFORM file with our GUID in this volume.
**/
STATIC
EFI_STATUS
ReadPayloadFromVolume (
  IN  EFI_FIRMWARE_VOLUME2_PROTOCOL  *Fv,
  OUT VOID                           **Buffer,
  OUT UINTN                          *Size
  )
{
  EFI_STATUS  Status;
  EFI_STATUS  ReadStatus;
  UINTN       Key;
  EFI_GUID    NameGuid;
  EFI_FV_FILETYPE  FileType;
  EFI_FV_FILE_ATTRIBUTES  Attributes;
  UINTN       FileSize;

  Key = 0;
  while (TRUE) {
    Status = Fv->GetNextFile (Fv, &Key, &FileType, &NameGuid, &Attributes, &FileSize);
    if (EFI_ERROR (Status)) {
      return EFI_NOT_FOUND;
    }

    if ((FileType != EFI_FV_FILETYPE_FREEFORM) || !CompareGuid (&NameGuid, &mPayloadFileGuid)) {
      continue;
    }

    //
    // Asking for EFI_SECTION_RAW makes FirmwareVolume2 descend through the
    // enclosing compression section and hand back the decompressed bytes; that is
    // the whole reason the payload is stored as COMPRESS { RAW { ... } }.
    //
    *Buffer = NULL;
    *Size   = 0;
    ReadStatus = Fv->ReadSection (
                       Fv,
                       &NameGuid,
                       EFI_SECTION_RAW,
                       0,
                       Buffer,
                       Size,
                       &ReadStatus
                       );
    if (!EFI_ERROR (ReadStatus) && (*Buffer != NULL) && (*Size > 0)) {
      DEBUG ((DEBUG_INFO, "Meizu15Payload: found %u bytes in FV %g\n", (UINT32)*Size, &NameGuid));
      return EFI_SUCCESS;
    }

    DEBUG ((DEBUG_ERROR, "Meizu15Payload: file present but unreadable: %r\n", ReadStatus));
    return EFI_NOT_FOUND;
  }
}

/**
  Entry point: locate the payload, register it as a ramdisk, let FatDxe mount it.
**/
EFI_STATUS
EFIAPI
Meizu15PayloadDxeEntry (
  IN EFI_HANDLE        ImageHandle,
  IN EFI_SYSTEM_TABLE  *SystemTable
  )
{
  EFI_STATUS                     Status;
  EFI_HANDLE                     *Handles;
  UINTN                          HandleCount;
  UINTN                          Index;
  EFI_FIRMWARE_VOLUME2_PROTOCOL  *Fv;
  VOID                           *Payload;
  UINTN                          PayloadSize;

  Handles     = NULL;
  HandleCount = 0;
  Payload     = NULL;
  PayloadSize = 0;

  Status = gBS->LocateHandleBuffer (
                  ByProtocol,
                  &gEfiFirmwareVolume2ProtocolGuid,
                  NULL,
                  &HandleCount,
                  &Handles
                  );
  if (EFI_ERROR (Status)) {
    DEBUG ((DEBUG_ERROR, "Meizu15Payload: no firmware volumes: %r\n", Status));
    return Status;
  }

  for (Index = 0; Index < HandleCount; Index++) {
    Status = gBS->HandleProtocol (Handles[Index], &gEfiFirmwareVolume2ProtocolGuid, (VOID **)&Fv);
    if (EFI_ERROR (Status)) {
      continue;
    }

    if (!EFI_ERROR (ReadPayloadFromVolume (Fv, &Payload, &PayloadSize))) {
      break;
    }
  }

  FreePool (Handles);

  if (Payload == NULL) {
    DEBUG ((DEBUG_ERROR, "Meizu15Payload: the payload volume is not in any FV\n"));
    return EFI_NOT_FOUND;
  }

  //
  // RamDiskRegister copies into a ramdisk the block layer can read; the volume is
  // read-only and FAT, which is exactly what the payload contains.
  //
  Status = RamDiskRegister (Payload, PayloadSize, 0, NULL, (EFI_GUID *)&mPayloadDiskGuid);
  if (EFI_ERROR (Status)) {
    DEBUG ((DEBUG_ERROR, "Meizu15Payload: RamDiskRegister failed: %r\n", Status));
    FreePool (Payload);
    return Status;
  }

  DEBUG ((DEBUG_INFO, "Meizu15Payload: %u byte volume registered\n", (UINT32)PayloadSize));
  //
  // Deliberately not freed: the ramdisk is backed by this buffer for the lifetime
  // of the boot.
  //
  return EFI_SUCCESS;
}
