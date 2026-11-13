/** @file
  Publish the UniXNU payload volume as a read-only block device.

  Why this driver exists: UniXNU's own UniXNURamDiskDxe takes the initrd address
  from a Mu-Silicium entry-argument HOB and from /chosen/linux,initrd-start in the
  device tree, and edk2-msm's BootShim (72 lines of assembly) does neither. It also
  depends on MdeModulePkg's RamDiskLib, which the edk2 revision edk2-msm pins does
  not have.

  So this driver does the whole job itself: it finds the FAT image that is baked
  into the firmware volume as a FREEFORM file with a compressed RAW section, keeps
  the decompressed bytes in memory, and installs an EFI_BLOCK_IO_PROTOCOL (plus a
  HardDrive device path) over them. EnhancedFatDxe then mounts it, and
  SimpleInit/DarwinBoot find /EFI/UniXNU/ on it.

  Consequences: the ESP travels inside boot.img (no ramdisk segment, no partition
  table change, nothing written to the phone's storage), and its size is bounded by
  the FFS file size limit (24-bit field, 16 MiB), which is why the section is
  compressed.

  Copyright (c) 2026, the Meizu 15 UniXNU port.
  SPDX-License-Identifier: BSD-2-Clause-Patent
**/

#include <Uefi.h>
#include <Library/BaseLib.h>
#include <Library/BaseMemoryLib.h>
#include <Library/DebugLib.h>
#include <Library/DevicePathLib.h>
#include <Library/MemoryAllocationLib.h>
#include <Library/UefiBootServicesTableLib.h>
#include <Protocol/BlockIo.h>
#include <Protocol/DevicePath.h>
#include <Protocol/FirmwareVolume2.h>

//
// Must match the FILE FREEFORM GUID that build-meizu15-uefi-payload.yml appends
// to meizu15.fdf.inc.
//
STATIC CONST EFI_GUID  mPayloadFileGuid = {
  0x3f1a9c22, 0x5b41, 0x4a0e, { 0x9c, 0x77, 0x2d, 0x61, 0x8b, 0x4f, 0xe0, 0x1a }
};

#define PAYLOAD_BLOCK_SIZE  512

typedef struct {
  EFI_BLOCK_IO_MEDIA        Media;
  EFI_BLOCK_IO_PROTOCOL     BlockIo;
  EFI_DEVICE_PATH_PROTOCOL  *DevicePath;
  UINT8                     *Data;
  UINTN                     Size;
} PAYLOAD_BLOCK_IO;

STATIC PAYLOAD_BLOCK_IO  *mPayload = NULL;

STATIC
EFI_STATUS
EFIAPI
PayloadReset (
  IN EFI_BLOCK_IO_PROTOCOL  *This,
  IN BOOLEAN                ExtendedVerification
  )
{
  return EFI_SUCCESS;
}

STATIC
EFI_STATUS
EFIAPI
PayloadReadBlocks (
  IN EFI_BLOCK_IO_PROTOCOL  *This,
  IN UINT32                 MediaId,
  IN EFI_LBA                Lba,
  IN UINTN                  BufferSize,
  OUT VOID                  *Buffer
  )
{
  PAYLOAD_BLOCK_IO  *P;
  UINT64            Offset;

  if (Buffer == NULL) {
    return EFI_INVALID_PARAMETER;
  }

  P = BASE_CR (This, PAYLOAD_BLOCK_IO, BlockIo);

  if (MediaId != P->Media.MediaId) {
    return EFI_MEDIA_CHANGED;
  }

  if ((BufferSize % PAYLOAD_BLOCK_SIZE) != 0) {
    return EFI_BAD_BUFFER_SIZE;
  }

  if (Lba > P->Media.LastBlock) {
    return EFI_INVALID_PARAMETER;
  }

  Offset = MultU64x32 (Lba, PAYLOAD_BLOCK_SIZE);
  if ((Offset + BufferSize) > P->Size) {
    return EFI_INVALID_PARAMETER;
  }

  CopyMem (Buffer, P->Data + Offset, BufferSize);
  return EFI_SUCCESS;
}

STATIC
EFI_STATUS
EFIAPI
PayloadWriteBlocks (
  IN EFI_BLOCK_IO_PROTOCOL  *This,
  IN UINT32                 MediaId,
  IN EFI_LBA                Lba,
  IN UINTN                  BufferSize,
  IN VOID                   *Buffer
  )
{
  return EFI_WRITE_PROTECTED;
}

STATIC
EFI_STATUS
EFIAPI
PayloadFlushBlocks (
  IN EFI_BLOCK_IO_PROTOCOL  *This
  )
{
  return EFI_SUCCESS;
}

/**
  Find the FREEFORM file carrying the payload volume in one firmware volume.
**/
STATIC
EFI_STATUS
ReadPayloadFromVolume (
  IN  EFI_FIRMWARE_VOLUME2_PROTOCOL  *Fv,
  OUT VOID                           **Buffer,
  OUT UINTN                          *Size
  )
{
  EFI_STATUS               Status;
  EFI_STATUS               ReadStatus;
  UINTN                    Key;
  EFI_GUID                 NameGuid;
  EFI_FV_FILETYPE          FileType;
  EFI_FV_FILE_ATTRIBUTES   Attributes;
  UINTN                    FileSize;
  UINT32                   AuthenticationStatus;

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
    // enclosing compression section and hand back the decompressed bytes.
    //
    *Buffer            = NULL;
    *Size              = 0;
    AuthenticationStatus = 0;
    ReadStatus = Fv->ReadSection (
                       Fv,
                       &NameGuid,
                       EFI_SECTION_RAW,
                       0,
                       Buffer,
                       Size,
                       &AuthenticationStatus
                       );
    if (!EFI_ERROR (ReadStatus) && (*Buffer != NULL) && (*Size >= PAYLOAD_BLOCK_SIZE)) {
      DEBUG ((DEBUG_INFO, "Meizu15Payload: %u bytes in FV\n", (UINT32)*Size));
      return EFI_SUCCESS;
    }

    DEBUG ((DEBUG_ERROR, "Meizu15Payload: file present but unreadable: %r\n", ReadStatus));
    return EFI_NOT_FOUND;
  }
}

/**
  Build a HardDrive device path so the filesystem driver can name the volume.
**/
STATIC
EFI_DEVICE_PATH_PROTOCOL *
MakeDevicePath (
  IN UINTN  Size
  )
{
  HARDDRIVE_DEVICE_PATH  *Hd;
  EFI_DEVICE_PATH_PROTOCOL  *End;
  UINT8                  *Buffer;

  Buffer = AllocateZeroPool (sizeof (HARDDRIVE_DEVICE_PATH) + sizeof (EFI_DEVICE_PATH_PROTOCOL));
  if (Buffer == NULL) {
    return NULL;
  }

  Hd = (HARDDRIVE_DEVICE_PATH *)Buffer;
  Hd->Header.Type    = MEDIA_DEVICE_PATH;
  Hd->Header.SubType = MEDIA_HARDDRIVE_DP;
  Hd->Header.Length  = sizeof (HARDDRIVE_DEVICE_PATH);
  Hd->PartitionNumber = 1;
  Hd->SignatureType  = SIGNATURE_TYPE_NONE;
  Hd->MBRType        = MBR_TYPE_EFI_PARTITION_TABLE_HEADER;
  // 512-byte sectors, capped to 32 bits like every other HardDrive node.
  Hd->PartitionSize = (Size / PAYLOAD_BLOCK_SIZE) > 0xFFFFFFFFULL
                        ? 0xFFFFFFFFULL
                        : (Size / PAYLOAD_BLOCK_SIZE);

  End = (EFI_DEVICE_PATH_PROTOCOL *)(Buffer + sizeof (HARDDRIVE_DEVICE_PATH));
  End->Type    = END_DEVICE_PATH_TYPE;
  End->SubType = END_ENTIRE_DEVICE_PATH_SUBTYPE;
  End->Length  = sizeof (EFI_DEVICE_PATH_PROTOCOL);
  return (EFI_DEVICE_PATH_PROTOCOL *)Buffer;
}

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
  EFI_HANDLE                     Handle;

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

  mPayload = AllocateZeroPool (sizeof (PAYLOAD_BLOCK_IO));
  if (mPayload == NULL) {
    return EFI_OUT_OF_RESOURCES;
  }

  mPayload->Data = Payload;
  mPayload->Size = PayloadSize;

  mPayload->Media.MediaId          = 0x4D5A3135;   // "MZ15"
  mPayload->Media.RemovableMedia   = FALSE;
  mPayload->Media.MediaPresent     = TRUE;
  mPayload->Media.LogicalPartition = FALSE;
  mPayload->Media.ReadOnly         = TRUE;
  mPayload->Media.WriteCaching     = FALSE;
  mPayload->Media.BlockSize        = PAYLOAD_BLOCK_SIZE;
  mPayload->Media.IoAlign          = 0;
  mPayload->Media.LastBlock        = (PayloadSize / PAYLOAD_BLOCK_SIZE) - 1;

  mPayload->BlockIo.Revision    = EFI_BLOCK_IO_PROTOCOL_REVISION;
  mPayload->BlockIo.Media       = &mPayload->Media;
  mPayload->BlockIo.Reset       = PayloadReset;
  mPayload->BlockIo.ReadBlocks  = PayloadReadBlocks;
  mPayload->BlockIo.WriteBlocks = PayloadWriteBlocks;
  mPayload->BlockIo.FlushBlocks = PayloadFlushBlocks;

  mPayload->DevicePath = MakeDevicePath (PayloadSize);

  Handle = NULL;
  Status = gBS->InstallMultipleProtocolInterfaces (
                  &Handle,
                  &gEfiBlockIoProtocolGuid,
                  &mPayload->BlockIo,
                  &gEfiDevicePathProtocolGuid,
                  mPayload->DevicePath,
                  NULL
                  );
  if (EFI_ERROR (Status)) {
    DEBUG ((DEBUG_ERROR, "Meizu15Payload: cannot publish the block device: %r\n", Status));
  } else {
    DEBUG ((
      DEBUG_INFO,
      "Meizu15Payload: %u bytes, %u blocks of %u, published\n",
      (UINT32)PayloadSize,
      (UINT32)(mPayload->Media.LastBlock + 1),
      (UINT32)PAYLOAD_BLOCK_SIZE
      ));
  }

  //
  // Nothing is freed on the way out: the volume is backed by this memory for the
  // rest of the boot.
  //
  return Status;
}
