/** @file
  Publish the UniXNU payload volume as a read-only block device.

  Why this driver exists: UniXNU's own UniXNURamDiskDxe takes the initrd address
  from a Mu-Silicium entry-argument HOB and from /chosen/linux,initrd-start in the
  device tree, and edk2-msm's BootShim (72 lines of assembly) does neither. It
  also depends on MdeModulePkg's RamDiskLib, which the edk2 revision edk2-msm
  pins does not have.

  So this driver does the whole job itself: it finds the FAT image that is baked
  into the firmware volume as a FREEFORM file with a bare RAW section, keeps the
  bytes in memory, and installs an EFI_BLOCK_IO_PROTOCOL (plus a HardDrive device
  path) over them. EnhancedFatDxe then mounts it, and SimpleInit/DarwinBoot find
  /EFI/UniXNU/ on it.

  The payload section is deliberately UNCOMPRESSED. Both compressed forms fail on
  this platform, for different reasons, and both were measured on the phone:

    SECTION COMPRESS  GenFds writes CompressionType = 1 (LZMA), but DxeCore's
                      EFI_SECTION_COMPRESSION branch only decodes
                      EFI_STANDARD_COMPRESSION, i.e. bit-Tiano, through
                      gEfiDecompressProtocol.

    SECTION GUIDED    Needs gLzmaCustomDecompressGuid registered with
                      ExtractGuidedSectionLib. LzmaCustomDecompressLib registers
                      from a constructor, and on this build it never reaches the
                      link: DxeCore stays 286,768 bytes whether or not
                      QcomCommonDsc.inc names it.

  A bare RAW section is the one form FirmwareVolume2 returns verbatim.

  Diagnostics matter more than usual here: the phone has no serial port, so every
  verdict goes to ConOut and is followed by a Stall long enough to photograph.
  Both the DEBUG() and Print() channels are used because neither is guaranteed.

  Copyright (c) 2026, the Meizu 15 UniXNU port.
  SPDX-License-Identifier: BSD-2-Clause-Patent
**/

#include <Uefi.h>
#include <Library/BaseLib.h>
#include <Library/BaseMemoryLib.h>
#include <Library/DebugLib.h>
#include <Library/DevicePathLib.h>
#include <Library/MemoryAllocationLib.h>
#include <Library/PrintLib.h>
#include <Library/UefiBootServicesTableLib.h>
#include <Library/UefiLib.h>
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

/**
  Print a line to the console and let it sit there.

  ConOut is the only channel that survives a kernel fault on this phone, so it is
  used for every verdict rather than DEBUG() alone.
**/
STATIC
VOID
Say (
  IN CONST CHAR16  *Text
  )
{
  //
  // Three channels, because on this platform each has failed at least once:
  //   DEBUG()  reaches the firmware log but the log is drawn with the text
  //            renderer and gets overwritten by whatever BDS prints next;
  //   Print()  goes through the same ConOut but formats, and it is what the
  //            DXE core's own diagnostics use;
  //   ConOut   directly, in case Print's buffer is the thing failing.
  // The previous build emitted "Meizu15Payload: entry" from DEBUG and nothing
  // else, which says gST->ConOut was not usable at dispatch time.
  //
  //
  // \r\n, not \n. This firmware's text renderer does not return the carriage, so
  // a bare \n starts the next line where this one stopped: the panel showed
  //   Meizu15Payload: entry8B3E019, Size 334229d be loaded, defaulting to ACPI
  // with the Device Tree message glued onto the end of ours.
  //
  Print (L"Meizu15Payload: %s\r\n", Text);
  if ((gST != NULL) && (gST->ConOut != NULL) && (gST->ConOut->OutputString != NULL)) {
    gST->ConOut->OutputString (gST->ConOut, (CHAR16 *)Text);
    gST->ConOut->OutputString (gST->ConOut, L"\r\n");
  } else {
    //
    // If this line is the only thing that appears, ConOut was not usable at
    // dispatch time and Print() is the only channel that works.
    //
    DEBUG ((DEBUG_ERROR, "Meizu15Payload: gST->ConOut unusable at dispatch\n"));
  }
}

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

  Reports how far the walk got, because "not in any FV" on the panel does not
  distinguish "this volume does not have it" from "GetNextFile gave up partway".
  The phone has no serial port, so the count and the failing status are the only
  way to tell those apart after the fact.
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
  UINTN                    Seen;
  CHAR16                   Line[128];

  *Buffer = NULL;
  *Size   = 0;
  Key     = 0;
  Seen    = 0;

  while (TRUE) {
    Status = Fv->GetNextFile (Fv, &Key, &FileType, &NameGuid, &Attributes, &FileSize);
    if (EFI_ERROR (Status)) {
      //
      // EFI_NOT_FOUND here means the walk reached the end of this volume. Any
      // other status means it desynchronised, which is what a mis-sized FFS
      // header does.
      //
      if (Seen != 0) {
        UnicodeSPrint (
          Line,
          sizeof (Line),
          L"Meizu15Payload: FV walk ended after %u files, status %r",
          (UINT32)Seen,
          Status
          );
        Say (Line);
      }

      return EFI_NOT_FOUND;
    }

    Seen++;

    if ((FileType != EFI_FV_FILETYPE_FREEFORM) || !CompareGuid (&NameGuid, &mPayloadFileGuid)) {
      continue;
    }

    UnicodeSPrint (
      Line,
      sizeof (Line),
      L"Meizu15Payload: payload FFS found as file %u of this FV, %u bytes",
      (UINT32)Seen,
      (UINT32)FileSize
      );
    Say (Line);

    //
    // A bare EFI_SECTION_RAW is returned verbatim; that is the whole story now
    // that the payload is stored uncompressed.
    //
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
    if (EFI_ERROR (ReadStatus) || (*Buffer == NULL) || (*Size < PAYLOAD_BLOCK_SIZE)) {
      UnicodeSPrint (
        Line,
        sizeof (Line),
        L"Meizu15Payload: ReadSection(RAW) failed: %r",
        ReadStatus
        );
      Say (Line);
      *Buffer = NULL;
      *Size   = 0;
      return EFI_NOT_FOUND;
    }

    UnicodeSPrint (
      Line,
      sizeof (Line),
      L"Meizu15Payload: %u bytes via EFI_SECTION_RAW",
      (UINT32)*Size
      );
    Say (Line);
    return EFI_SUCCESS;
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
  HARDDRIVE_DEVICE_PATH     *Hd;
  EFI_DEVICE_PATH_PROTOCOL  *End;
  UINT8                     *Buffer;

  Buffer = AllocateZeroPool (sizeof (HARDDRIVE_DEVICE_PATH) + sizeof (EFI_DEVICE_PATH_PROTOCOL));
  if (Buffer == NULL) {
    return NULL;
  }

  Hd = (HARDDRIVE_DEVICE_PATH *)Buffer;
  Hd->Header.Type    = MEDIA_DEVICE_PATH;
  Hd->Header.SubType = MEDIA_HARDDRIVE_DP;
  // EFI_DEVICE_PATH_PROTOCOL.Length is UINT8[2]: a two-byte little-endian size.
  Hd->Header.Length[0] = (UINT8)(sizeof (HARDDRIVE_DEVICE_PATH) & 0xFF);
  Hd->Header.Length[1] = (UINT8)((sizeof (HARDDRIVE_DEVICE_PATH) >> 8) & 0xFF);
  Hd->PartitionNumber = 1;
  Hd->SignatureType   = 0;   // SIGNATURE_TYPE_NONE
  Hd->MBRType         = MBR_TYPE_EFI_PARTITION_TABLE_HEADER;
  // 512-byte sectors, capped to 32 bits like every other HardDrive node.
  Hd->PartitionSize = (Size / PAYLOAD_BLOCK_SIZE) > 0xFFFFFFFFULL
                        ? 0xFFFFFFFFULL
                        : (Size / PAYLOAD_BLOCK_SIZE);

  End          = (EFI_DEVICE_PATH_PROTOCOL *)(Buffer + sizeof (HARDDRIVE_DEVICE_PATH));
  End->Type    = END_DEVICE_PATH_TYPE;
  End->SubType = END_ENTIRE_DEVICE_PATH_SUBTYPE;
  End->Length[0] = (UINT8)sizeof (EFI_DEVICE_PATH_PROTOCOL);
  End->Length[1] = 0;
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
  CHAR16                         Line[128];

  Handles     = NULL;
  HandleCount = 0;
  Payload     = NULL;
  PayloadSize = 0;

  Say (L"");
  Say (L"=== Meizu15PayloadDxe ENTER ===");
  DEBUG ((DEBUG_ERROR, "Meizu15Payload: entry\r\n"));

  Status = gBS->LocateHandleBuffer (
                  ByProtocol,
                  &gEfiFirmwareVolume2ProtocolGuid,
                  NULL,
                  &HandleCount,
                  &Handles
                  );
  if (EFI_ERROR (Status)) {
    Say (L"Meizu15Payload: no firmware volumes at all");
    return Status;
  }

  UnicodeSPrint (Line, sizeof (Line), L"Meizu15Payload: %u firmware volume handle(s)", (UINT32)HandleCount);
  Say (Line);

  for (Index = 0; Index < HandleCount; Index++) {
    Status = gBS->HandleProtocol (Handles[Index], &gEfiFirmwareVolume2ProtocolGuid, (VOID **)&Fv);
    if (EFI_ERROR (Status)) {
      continue;
    }

    UnicodeSPrint (Line, sizeof (Line), L"Meizu15Payload: scanning volume %u", (UINT32)(Index + 1));
    Say (Line);

    if (!EFI_ERROR (ReadPayloadFromVolume (Fv, &Payload, &PayloadSize))) {
      break;
    }
  }

  FreePool (Handles);

  if (Payload == NULL) {
    Say (L"Meizu15Payload: NO PAYLOAD IN ANY FV");
    gBS->Stall (20000000);
    return EFI_NOT_FOUND;
  }

  UnicodeSPrint (Line, sizeof (Line), L"Meizu15Payload: publishing %u byte volume", (UINT32)PayloadSize);
  Say (Line);
  gBS->Stall (5000000);

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
    UnicodeSPrint (Line, sizeof (Line), L"Meizu15Payload: install failed: %r", Status);
    Say (Line);
  } else {
    UnicodeSPrint (
      Line,
      sizeof (Line),
      L"Meizu15Payload: BLOCK DEVICE PUBLISHED, %u blocks",
      (UINT32)(mPayload->Media.LastBlock + 1)
      );
    Say (Line);
  }

  gBS->Stall (20000000);

  //
  // Nothing is freed on the way out: the volume is backed by this memory for the
  // rest of the boot.
  //
  return Status;
}
