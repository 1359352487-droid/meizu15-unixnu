// Bakes an already-built UniXNU ESP into the firmware volume.
//
// This is the piece the local build chain includes; CI never does, because it has
// no payload image to bake and the FILE would fail to resolve. Include it from a
// copied FDF, not from meizu15.fdf.inc:
//
//   !include Platform/Meizu/sdm660/meizu15-local-payload.fdf.inc
//
// The ESP has to stay under 16 MiB (a larger one pushes the FV to FFS2, which the
// EDK2 link step here cannot emit), which is why the 17.5 MB 25-driver kernel is
// LZMA-packed before it goes in -- see scripts/local/lzma-pack.py and DarwinBoot's
// 0x5D branch.
//
// The file below must exist before the build runs:
//   Platform/Meizu/sdm660/payload/unixnu-esp.img
//
FILE FREEFORM = 3f1a9c22-5b41-4a0e-9c77-2d618b4fe01a {
  SECTION RAW = Platform/Meizu/sdm660/payload/unixnu-esp.img
}
