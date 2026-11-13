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
