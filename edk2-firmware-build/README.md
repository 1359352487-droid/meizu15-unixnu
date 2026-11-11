# edk2-firmware-build — Meizu15Pkg

The UEFI firmware for this phone, assembled from **prebuilt DXE modules that were
carved out of the phone's own XBL** on top of an open EDK2 core.

There is no driver source here. That is intentional: these 67 modules are the
phone's stock signed binaries, already proven to drive this exact hardware. The
job of this package is to wrap them into one firmware volume that the bootloader
will accept as a "kernel".

## Layout

```
edk2-firmware-build/
└── Meizu15Pkg/
    ├── Meizu15Pkg.dec
    ├── Meizu15Pkg.dsc          [Components] 67 binary-only INFs
    ├── Meizu15Pkg.fdf          [FV.MEIZU15_FV] 5 MiB volume, 67 INFs
    └── Drivers/
        ├── DxeCore/            DxeCore.inf + DxeCore.efi
        ├── SdccDxe/            eMMC block I/O
        ├── DisplayDxe/         MDSS DSI
        ├── PmicDxe/            PM660 rails
        ├── ArmCpuDxe/ ArmGicDxe/ ArmTimerDxe/
        ├── QcomBds/            boot device selection
        └── … 67 total
```

Each `FILE_GUID` in an INF is the GUID the module was published under in the
stock volume, read from its FFS header. Nothing is guessed.

## Volume sizing

The stock inner volume was 1,792,000 bytes, but only because its `FV_IMAGE` was
gzip-compressed inside. Uncompressed, the 67 modules occupy 3,912,000 bytes of
PE32 plus FFS/section headers, so this package declares a 5 MiB volume
(`NumBlocks = 0x50` at `BlockSize = 0x10000`). The boot partition is 64 MiB, so
there is no pressure.

## Building

EDK2 needs Linux or macOS. It does **not** build on this Windows host (no WSL,
no aarch64 cross toolchain, no Docker were present at the time of writing).

### Option A — GitHub Actions (recommended, no local setup)

Push this repository and run the `build-meizu15-uefi` workflow. It:

1. installs `gcc-aarch64-linux-gnu`, `iasl`, `nasm`, `uuid-dev`
2. clones `edk2` at `edk2-stable202408`, builds BaseTools
3. copies `Meizu15Pkg` into the EDK2 tree, adds a `[Packages]` block to every
   binary-only INF
4. runs `build -a AARCH64 -t GCC5 -b RELEASE -p Meizu15Pkg/Meizu15Pkg.dsc`
5. uploads `Build/Meizu15Pkg/*/FV/*.fd`

### Option B — locally on WSL2 / Linux

```bash
sudo apt install build-essential uuid-dev iasl nasm gcc-aarch64-linux-gnu
git clone --depth 1 -b edk2-stable202408 https://github.com/tianocore/edk2
cd edk2 && git submodule update --init --depth 1 && make -C BaseTools -j$(nproc)
cp -r ../edk2-firmware-build/Meizu15Pkg .
export WORKSPACE=$PWD PACKAGES_PATH=$PWD GCC5_AARCH64_PREFIX=aarch64-linux-gnu-
export EDK_TOOLS_PATH=$PWD/BaseTools
source ./edksetup.sh BaseTools
build -a AARCH64 -t GCC5 -b RELEASE -p Meizu15Pkg/Meizu15Pkg.dsc -n $(nproc)
```

Output: `Build/Meizu15Pkg/RELEASE_GCC5/FV/MEIZU15PKG_UEFI.fd`

## Expected build problems (untested)

These are the likely failures on the first attempt, in order:

| Symptom | Cause | Fix |
|---|---|---|
| `[LibraryClasses]` unresolved for a component | binary-only INF still declares no libraries, but the `.dsc` has an empty `[LibraryClasses]` section | add the needed `LibraryClasses` entries, or add `[LibraryClasses]` stubs that map to `NULL` |
| `Module type DXE_CORE not allowed in this context` | the stock `DxeCore` binary is not the EDK2 source-built core | either drop `DxeCore.inf` and let EDK2 compile its own core from `MdeModulePkg`, or keep stock and disable the source one |
| FV overflows | unaccounted FFS padding | raise `NumBlocks` |
| `Invalid GUID` on some INF | a module's name contains characters EDK2 rejects | the generator already sanitised `BASE_NAME`, but check the `FILE_GUID` lines |

The `DxeCore` question is the real one: mixing a stock DXE core with stock
drivers is self-consistent, but mixing a stock core with EDK2-built glue is not.
The safest first build uses **only** the stock modules plus EDK2's own core, and
drops the carved `DxeCore`.

## After a successful build

```
MEIZU15PKG_UEFI.fd
   └─ wrap with an AOSP v0 boot header
        base 0, kernel_offset 0x8000, ramdisk_offset 0x1000000,
        second_offset 0xf00000, tags_offset 0x100, pagesize 4096, header v0
   └─ fastboot boot meizu15-uefi.img
```

The splash region node must be present in the boot image DTB or the stock
bootloader powers the panel off — see `board.toml`:

```toml
[[bootimg.dtb_node]]
path = "/reserved-memory/splash_region@9d400000"
props = [{ name="label", type="s", value="cont_splash_mem" },
         { name="reg",   type="x", value=["0x0","0x9d400000","0x0","0x2400000"] }]
```
