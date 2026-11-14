/** @file
  Publish the UniXNU payload volume as a read-only block device.

  Why this driver exists: UniXNU's own UniXNURamDiskDxe takes the initrd address
  from a Mu-Silicium entry-argument HOB and from /chosen/linux,initrd-start in the
  device tree, and edk2-msm's BootShim (72 lines of assembly) does neither. It
  also depends on MdeModulePkg's RamDiskLib, which the edk2 revision edk2-msm
  pins does not have.

  So this driver does the whole job itself: it finds the FAT image baked into the
  firmware volume as a FREEFORM file with a bare RAW section, keeps the bytes in
  memory, and installs an EFI_BLOCK_IO_PROTOCOL (plus a HardDrive device path)
  over them. EnhancedFatDxe then mounts it, and SimpleInit/DarwinBoot find
  /EFI/UniXNU/ on it.

------------------------------------------------------------------------------
  Diagnostics

  This phone has no serial port, so the panel is the only place a verdict can
  appear, and getting text onto it from a DXE driver turned out to be the hard
  part of this port.

  What was measured, in order:

    Say() via ConOut->OutputString   nothing appeared
    Print() from UefiLib             nothing appeared
    DEBUG ((DEBUG_ERROR, "..."))     appeared, but with a bare \n the next
                                     line started where this one stopped:
                                     "Meizu15Payload: entry8B3E019, Size 334..."

  Two conclusions, both acted on here:

    1. The console is not usable at driver-dispatch time on this firmware, but
       the DEBUG() log buffer is. So every message goes through DEBUG at ERROR
       level, with an "MZ15>> " prefix so it can be found among the hundreds of
       lines BDS prints while dispatching everything else.
    2. The renderer does not return the carriage, so lines end with \r\n.

  Colour and formatting are given up deliberately: this is a debugging channel,
  not a UI.

------------------------------------------------------------------------------

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
#include <Protocol/LoadedImage.h>

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
  Put one line in the firmware's log buffer.

  ASCII only, "MZ15>> " prefix, \r\n terminator. See the file header for why this
  is DEBUG and not Print.
**/
STATIC
VOID
SayA (
  IN CONST CHAR8  *Text
  )
{
  DEBUG ((DEBUG_ERROR, "MZ15>> %a\r\n", Text));
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

  //
  // Log every access attempt with its caller's parameters. Nothing mounts this
  // volume even though Media is correct (id 0x4d5a3135, LastBlock 31743, 512 B
  // blocks), and FatDiskIo never lists it. Either no consumer ever reads it, or
  // one reads and is refused; this tells those apart.
  //
  DEBUG ((
    DEBUG_ERROR,
    "MZ15>> ReadBlocks lba %u size %u mediaid 0x%08x\r\n",
    (UINT32)Lba,
    (UINT32)BufferSize,
    MediaId
    ));

  if (MediaId != P->Media.MediaId) {
    DEBUG ((DEBUG_ERROR, "MZ15>> ReadBlocks: media id mismatch, refusing\r\n"));
    return EFI_MEDIA_CHANGED;
  }

  if ((BufferSize % PAYLOAD_BLOCK_SIZE) != 0) {
    DEBUG ((DEBUG_ERROR, "MZ15>> ReadBlocks: bad buffer size\r\n"));
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

  The walk count is reported because "not in any FV" on the panel cannot
  distinguish "this volume does not have it" from "GetNextFile gave up partway".
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

  *Buffer = NULL;
  *Size   = 0;
  Key     = 0;
  Seen    = 0;

  while (TRUE) {
    Status = Fv->GetNextFile (Fv, &Key, &FileType, &NameGuid, &Attributes, &FileSize);
    if (EFI_ERROR (Status)) {
      //
      // EFI_NOT_FOUND is the normal end of the list. Anything else means the
      // header walk desynchronised.
      //
      DEBUG ((DEBUG_ERROR, "MZ15>> walk stopped: %u files seen, status %r\r\n", (UINT32)Seen, Status));
      return EFI_NOT_FOUND;
    }

    Seen++;

    if ((FileType != EFI_FV_FILETYPE_FREEFORM) || !CompareGuid (&NameGuid, &mPayloadFileGuid)) {
      continue;
    }

    DEBUG ((
      DEBUG_ERROR,
      "MZ15>> payload FFS is file %u of this FV, %u bytes\r\n",
      (UINT32)Seen,
      (UINT32)FileSize
      ));

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
      DEBUG ((DEBUG_ERROR, "MZ15>> ReadSection(RAW) failed: %r\r\n", ReadStatus));
      *Buffer = NULL;
      *Size   = 0;
      return EFI_NOT_FOUND;
    }

    DEBUG ((DEBUG_ERROR, "MZ15>> got %u bytes from EFI_SECTION_RAW\r\n", (UINT32)*Size));
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

  End            = (EFI_DEVICE_PATH_PROTOCOL *)(Buffer + sizeof (HARDDRIVE_DEVICE_PATH));
  End->Type      = END_DEVICE_PATH_TYPE;
  End->SubType   = END_ENTIRE_DEVICE_PATH_SUBTYPE;
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
  EFI_LOADED_IMAGE_PROTOCOL      *LoadedImage;

  Handles     = NULL;
  HandleCount = 0;
  LoadedImage = NULL;
  Payload     = NULL;
  PayloadSize = 0;

  SayA ("=== Meizu15PayloadDxe ENTER ===");

  //
  // Look in the volume this driver was loaded FROM first.
  //
  // LocateHandleBuffer for gEfiFirmwareVolume2ProtocolGuid reported only two
  // volumes on the phone, and the walk stopped after one and zero files
  // respectively. Neither can be the 71-file DXE volume that this driver itself
  // was just dispatched out of. The handle list is simply incomplete this early,
  // whereas LoadedImage->DeviceHandle is by definition correct: it names the
  // volume the image was read from.
  //
  Status = gBS->HandleProtocol (
                  ImageHandle,
                  &gEfiLoadedImageProtocolGuid,
                  (VOID **)&LoadedImage
                  );
  if (!EFI_ERROR (Status) && (LoadedImage != NULL) && (LoadedImage->DeviceHandle != NULL)) {
    DEBUG ((DEBUG_ERROR, "MZ15>> own volume is at handle %p\r\n", LoadedImage->DeviceHandle));
    Status = gBS->HandleProtocol (
                    LoadedImage->DeviceHandle,
                    &gEfiFirmwareVolume2ProtocolGuid,
                    (VOID **)&Fv
                    );
    if (!EFI_ERROR (Status)) {
      if (!EFI_ERROR (ReadPayloadFromVolume (Fv, &Payload, &PayloadSize))) {
        goto Publish;
      }
    } else {
      DEBUG ((DEBUG_ERROR, "MZ15>> own volume has no FV protocol: %r\r\n", Status));
    }
  } else {
    DEBUG ((DEBUG_ERROR, "MZ15>> no LoadedImage: %r\r\n", Status));
  }

  //
  // Fall back to every volume the DXE core knows about.
  //
  Status = gBS->LocateHandleBuffer (
                  ByProtocol,
                  &gEfiFirmwareVolume2ProtocolGuid,
                  NULL,
                  &HandleCount,
                  &Handles
                  );
  if (EFI_ERROR (Status)) {
    DEBUG ((DEBUG_ERROR, "MZ15>> no firmware volume handles: %r\r\n", Status));
    return Status;
  }

  DEBUG ((DEBUG_ERROR, "MZ15>> %u firmware volume handle(s)\r\n", (UINT32)HandleCount));

  for (Index = 0; Index < HandleCount; Index++) {
    Status = gBS->HandleProtocol (Handles[Index], &gEfiFirmwareVolume2ProtocolGuid, (VOID **)&Fv);
    if (EFI_ERROR (Status)) {
      continue;
    }

    DEBUG ((DEBUG_ERROR, "MZ15>> scanning FV %u of %u\r\n", (UINT32)(Index + 1), (UINT32)HandleCount));

    if (!EFI_ERROR (ReadPayloadFromVolume (Fv, &Payload, &PayloadSize))) {
      break;
    }
  }

  FreePool (Handles);

  if (Payload == NULL) {
    SayA ("NO PAYLOAD IN ANY FV");
    //
    // Hold the panel. Everything above is printed while BDS is still loading
    // drivers and BDS prints hundreds of lines afterwards, so without this the
    // text scrolls away before SimpleInit draws. 20 s is enough to photograph.
    //
    gBS->Stall (20000000);
    return EFI_NOT_FOUND;
  }

Publish:
  DEBUG ((DEBUG_ERROR, "MZ15>> publishing %u byte volume\r\n", (UINT32)PayloadSize));

  mPayload = AllocateZeroPool (sizeof (PAYLOAD_BLOCK_IO));
  if (mPayload == NULL) {
    return EFI_OUT_OF_RESOURCES;
  }

  mPayload->Data = Payload;
  mPayload->Size = PayloadSize;

  mPayload->Media.MediaId          = 0x4D5A3135;   // "MZ15"
  mPayload->Media.RemovableMedia   = FALSE;
  mPayload->Media.MediaPresent     = TRUE;
  //
  // TRUE, so PartitionDxe leaves this alone.
  //
  // LogicalPartition = FALSE tells PartitionDxe the device is a whole disk, and
  // it therefore reads offset 0 looking for an MBR. A FAT volume has a boot
  // record there, not a partition table, so the 446-byte bootstrap area gets
  // decoded as four partition entries. The panel showed the result:
  //
  //   BlockSize : 512
  //   LastBlock : 7BFF
  //   PartitionValidMbr: Bad MBR partition size EndingLBA(7FFF) > LastLBA(7BFF)
  //
  // A bogus child device was created from that, and SimpleInit then walked it.
  // The volume is already a filesystem, not a partitioned disk, so say so.
  //
  mPayload->Media.LogicalPartition = TRUE;
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
    DEBUG ((DEBUG_ERROR, "MZ15>> install failed: %r\r\n", Status));
  } else {
    DEBUG ((
      DEBUG_ERROR,
      "MZ15>> BLOCK DEVICE PUBLISHED, %u blocks of %u\r\n",
      (UINT32)(mPayload->Media.LastBlock + 1),
      (UINT32)PAYLOAD_BLOCK_SIZE
      ));
    //
    // Dump every field the FAT driver will look at. The panel showed a
    // "LastBlock : FF" line right after two "Cache Page OutBound occurred"
    // messages, and FF is 256 blocks -- 128 KiB -- not this volume's 31,744.
    // Either that line belongs to another device, or Media is being read wrong;
    // printing the values this driver actually published settles which.
    //
    DEBUG ((
      DEBUG_ERROR,
      "MZ15>> media: id 0x%08x last %u bs %u ro %u present %u logical %u align %u\r\n",
      mPayload->Media.MediaId,
      (UINT32)mPayload->Media.LastBlock,
      (UINT32)mPayload->Media.BlockSize,
      (UINT32)mPayload->Media.ReadOnly,
      (UINT32)mPayload->Media.MediaPresent,
      (UINT32)mPayload->Media.LogicalPartition,
      (UINT32)mPayload->Media.IoAlign
      ));
    DEBUG ((
      DEBUG_ERROR,
      "MZ15>> blockio rev 0x%08x media %p handle %p\r\n",
      mPayload->BlockIo.Revision,
      mPayload->BlockIo.Media,
      Handle
      ));
    //
    // MediaId is what FatDiskIo prints alongside the geometry. If the panel
    // shows 4D5A3135 against a LastBlock of 0x7BFF, the volume is being seen
    // correctly and the FF line belongs to something else.
    //
    DEBUG ((DEBUG_ERROR, "MZ15>> media id literal: MZ15 (0x4D5A3135)\r\n"));

    //
    // Wake the disk stack up.
    //
    // Fat.inf declares gEfiBlockIoProtocolGuid as TO_START, so FatDxe scans for
    // block devices once, during its own dispatch, and never again. This driver
    // is dispatched after that scan (Media comes out correct here, yet not one
    // of PartitionDxe's "BlockSize : 512 / LastBlock : ..." lines ever mentions
    // 0x7BFF), so without an explicit connect nothing downstream will ever look
    // at this handle.
    //
    // ConnectController runs the DriverBindingSupported/Start chain for every
    // driver whose protocols now match, which is how a device that appears after
    // the initial scan is supposed to be picked up.
    //
    //
    // Three passes, ignoring the return value: ConnectController reports the
    // status of the last DriverBinding it ran, and a driver that declines the
    // handle is normal. What matters is whether a later pass produced readers,
    // which shows up as ReadBlocks calls below.
    //
    for (UINTN Pass = 0; Pass < 3; Pass++) {
      EFI_STATUS  ConnectStatus;

      ConnectStatus = gBS->ConnectController (Handle, NULL, NULL, TRUE);
      DEBUG ((
        DEBUG_ERROR,
        "MZ15>> ConnectController pass %u: %r\r\n",
        (UINT32)(Pass + 1),
        ConnectStatus
        ));
    }
  }

  //
  // Hold the panel so the verdict above can be photographed, then let BDS carry
  // on to SimpleInit.
  //
  gBS->Stall (20000000);

  //
  // Nothing is freed: the volume is backed by this memory for the rest of boot.
  //
  return Status;
}
