// per-device BSP DXEs for the Meizu 15 (M8810, SDM660)

// ACPI Tables
// The SDM660 tables in Silicon/Qualcomm/sdm660/AcpiTables are SoC-level and are
// reused verbatim; nothing here is device-specific yet. When the port reaches the
// point of a working ACPI OS, replace DSDT.aml with a Meizu15-derived one.
FILE FREEFORM = 7E374E25-8E01-4FEE-87F2-390C23C606CD {
  SECTION RAW = Silicon/Qualcomm/sdm660/AcpiTables/DSDT/DSDT.aml
  SECTION RAW = Silicon/Qualcomm/sdm660/AcpiTables/BuiltIn/CSRT.aml
  SECTION RAW = Silicon/Qualcomm/sdm660/AcpiTables/BuiltIn/DBG2.aml
  SECTION RAW = Silicon/Qualcomm/sdm660/AcpiTables/BuiltIn/FACS.aml
  SECTION RAW = Silicon/Qualcomm/sdm660/AcpiTables/BuiltIn/FADT.aml
  SECTION RAW = Silicon/Qualcomm/sdm660/AcpiTables/BuiltIn/GTDT.aml
  SECTION RAW = Silicon/Qualcomm/sdm660/AcpiTables/BuiltIn/MADT.aml
  SECTION RAW = Silicon/Qualcomm/sdm660/AcpiTables/BuiltIn/MCFG.aml
  SECTION UI = "AcpiTables"
}

// Mainline device tree blob
// Intentionally empty: the bootloader hands us the live FDT in x0/cmdline and
// UniXNU's DarwinBoot reads it from there. Add a FILE FREEFORM entry here only
// if a device-specific DTB has to be baked into the volume.
// ---------------------------------------------------------------------------
// The payload chain, in dependency order.
//
// [Components] in the DSC only decides what gets *compiled*; this file decides
// what gets *packed into the volume*. A module listed in [Components] but not
// here shows up as "Building ... .inf" in the log and lands in Build/, yet is
// absent from the FV -- which is exactly how Meizu15PayloadDxe went missing and
// silently broke the whole chain:
//
//   no PayloadDxe -> no EFI_BLOCK_IO_PROTOCOL -> FatDxe has nothing to mount
//   -> prober never sees /bootaa64.efi -> the menu stays at "1 items found"
//
// CI asserts on the unpacked image now, not on the build directory, so this
// omission cannot come back green.
// ---------------------------------------------------------------------------
INF Platform/Meizu/sdm660/Meizu15PayloadDxe/Meizu15PayloadDxe.inf

// UniXNU's Darwin loader lives in the firmware so that UniXNU.efi only has to
// carry the menu; the kernel and its ADT stay on the payload volume.
INF DarwinBootPkg/Driver/DarwinBoot.inf