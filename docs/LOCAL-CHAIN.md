# 本地构建链（macOS 虚拟机，不依赖 CI）

> 2026-11-24 搭好，2026-11-26 首次在真机上起内核。这条链一轮 **≈13 分钟**，
> CI 链一轮 20–30 分钟（还要等 artifact + 拍屏判读）。P2 阶段建议走这条。

## 1. 这台机器的真实条件（会决定改法）

| 项 | 事实 | 后果 |
|---|---|---|
| macOS | 12.7.6，x86_64，**VMware 虚拟机**，8 核 / 8 GB | 编译够用；`-j6` 稳妥 |
| Xcode | **没装**，只有 Command Line Tools（clang 14.0.0） | 不能用 Xcode 的 `iig`；HakiOS 有替代 |
| Homebrew | 没装 | `brew install` 不可用（也不需要） |
| 网络 | `github.com` 直连基本不通 | 一切走 `gh-proxy.com` / `ghfast.top` |
| `sudo` | 可用 | 只在装系统工具时才需要 |

**不需要 Xcode.app**：`xnu-deps/setup.sh` 的 `theos_mac()` 用 `xcrun -f` 把 CLT 的
`ld nm otool strip lipo libtool nmedit ctf_insert codesign_allocate install_name_tool`
链成 theos 的样子；`iig` 用 HakiOS 自己的替身（`xnu-deps/host/bin/iig`）。

## 2. 工具链（全部装在 `/tmp`，可重装）

| 工具 | 来源 | 装到 |
|---|---|---|
| CLT（clang 14 / mig / unifdef） | Xcode Command Line Tools | 系统 |
| LLVM 15.0.7 | `clang+llvm-15.0.7-x86_64-apple-darwin21.0.tar.xz` | `/tmp/llvm` |
| GNU binutils 2.41 | `--target=aarch64-linux-gnu`，`--disable-nls --disable-werror` | `/tmp/tools/bin` |
| nasm 2.16.01 | nasm.us releasebuilds（GitHub tag 的 tarball 没有 configure） | `/tmp/tools/bin` |
| ACPICA iasl | `acpica/acpica@master`，`make iasl` | `/tmp/acpica-src/generate/unix/bin` |

```bash
export PATH=/tmp/llvm/bin:/tmp/tools/bin:/tmp/acpica-src/generate/unix/bin:$PATH
```

## 3. 内核

```bash
export DEVELOPER_DIR=/Library/Developer/CommandLineTools
export GIT_CONFIG_GLOBAL=$PWD/.gitconfig-proxy     # 把 github.com 重写到 gh-proxy.com
export THEOS=$PWD/xnu-deps/theos-mac

# 1) 源码 + 补丁（tarball 分支，约 1 分钟）
XNU_TARBALL=../tarballs/xnu-8796.121.2.tar.gz bash xnu/apply.sh

# 2) meizu15 自己的补丁
F=$(find xnu-src -path '*/osfmk/arm64/loader_return.c')
sed -i '' 's/t0sz < 25 || t0sz > 33/t0sz < 24 || t0sz > 33/' "$F"   # 让早期 checkpoint 能显示
python3 ../meizu15-unixnu-main/scripts/ci/apply_nonapple_guards.py "$PWD/xnu-src"
python3 ../meizu15-unixnu-main/scripts/ci/disable_pan.py          "$PWD/xnu-src"
python3 ../meizu15-unixnu-main/scripts/ci/install_sdm660_shim.py  "$PWD/xnu-src"

# 3) 工具链（首次约 40 分钟）
bash xnu-deps/setup.sh --stage1
bash xnu-deps/setup.sh --stage2

# 4) 编内核（增量 3–5 分钟，全量约 25 分钟）
./xnu-build.sh -j6
```

产物：

```
xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/
    mach.development.bcm2837              17,498,352 B   ← 25 驱动，LZMA 打包用这个
    mach.development.bcm2837.unstripped   22 MB
```

**两个必须知道的坑**：

1. **`check_all_exports` 一定失败，这是设计如此**（HakiOS `xnu/README.md` 原话）。
   `make` 返回 2 不代表没编出来，看产物时间戳。
2. **路径不能有空格**（XNU 的 Makefile 会炸）。

## 4. payload 打包

仓库里已经放好脚本：`scripts/local/`（`build-round.sh` / `make-esp.sh` / `build-esp.sh` /
`lzma-pack.py` / `retarget_header.py` / `watch-and-flash.sh` / `unixnu.toml.template`），
超层的 edk2 改动在 `patches/edk2/`（`edk2-top.diff`、`edk2-submodule.diff`、`simpleinit.diff`）。

```bash
# 一轮：编内核 → 打 ESP（含 LZMA 内核 + ADT + initrd）→ 编固件 → 套 boot header
scripts/local/build-round.sh <name>
# 产物：out/boot-meizu15-payload-<name>.img
```

ESP 必须 **< 16 MiB**（否则 GenFds 把 FV 提升为 FFS3，`Meizu15PayloadDxe` 的 `GetNextFile` 失步）。
17.5 MB 的 25 驱动内核靠 **LZMA** 进得去：`DarwinBoot.c` 判断首字节 `0x5D` 就按 EFI LZMA
（5 字节 props + 8 字节 size + 流）解压，其它情况原样使用。ESL 侧 `unixnu.toml` 里
`args = "-v keepsyms=1 debug=0x8 rd=md0"`、`initrd = "Payloads/launchd.macho"`。

### 4.1 为 macOS 改的九处（都在本地副本，`patches/edk2/top.diff` 记录）

| # | 文件 | 问题 | 改法 |
|---|---|---|---|
| 1 | `build.sh` | BSD `getopt` 没有 `-l` | 手写 `while [ $# -gt 0 ]` |
| 2 | `build.sh` | bash 3.2 没有 `typeset -l/-u` | 用 `tr` 折叠大小写 |
| 3 | `SimpleInit/src/host/rootfs.c` | 缺 `PATH_MAX`；Darwin 的 `st_atim/st_mtim` 名为 `st_atimespec/st_mtimespec` | `<limits.h>` + `#ifdef __APPLE__` |
| 4 | `SimpleInit/scripts/gen-rootfs-source.sh` | `ld -b binary` 在 Darwin 不存在 | `.incbin`，符号名保持 `_binary_rootfs_bin_start/_end` |
| 5 | `SimpleInit/include/uefi.h` | 大小写不敏感文件系统上 `#include <Uefi.h>` 命中自己 | 改名 `simpleinit-uefi.h`，61 处引用跟着改 |
| 6 | `tools/BootShim/Makefile` | 没有 `aarch64-linux-gnu-*` | `clang --target=aarch64-none-elf`、`llvm-objcopy` |
| 7 | `tools/tools_def.txt` | AArch64 没有 `-DEFIAPI=`；`-flto` 需要 LLVMgold（macOS 包没有） | 加 `-DEFIAPI=`，删 `-flto` 与 `-llto-aarch64` |
| 8 | `tools/tools_def.txt` | 链接器 | `-fuse-ld=bfd`（GNU ld），**不加任何 `-z max-page-size`** |
| 9 | `BaseTools/Source/C/GenFw/Elf64Convert.c` | lld `-r` 段只对齐 32 字节，GenFw 想把 ADRP 改 ADR | `if (mCoffAlignment < 0x1000)` → `if (0)`，保持 ADRP |

第 9 条是最后一块拼图：保持 ADRP 后 PE 段偏移与 ELF 一致（mod 4 KB），
GenFw 的 `relative relocations require identical offsets` 检查也过了。

## 5. 固件构建与头文件

```bash
cd edk2-msm
export WORKSPACE=$PWD PACKAGES_PATH=$PWD/Common/edk2:$PWD/Common/edk2-platforms \
       EDK_TOOLS_PATH=$PWD/Common/edk2/BaseTools CONF_PATH=$PWD/Common/edk2/Conf
./build.sh -d meizu15 -r RELEASE          # 约 8 分钟
python3 scripts/local/retarget_header.py edk2-msm/boot-meizu15.img \
  edk2-msm-overlay/boot-header-1632.bin out/boot-meizu15-payload-<name>.img
```

`meizu15.fdf.inc` 里**不含** payload 的 `FILE FREEFORM`：本地构建把
`meizu15-local-payload.fdf.inc` include 进来（见该文件头注释），CI 因为拿不到
`payload/unixnu-esp.img` 所以不 include，两边都能编。

## 6. 刷机与判读

```bash
tools/platform-tools/adb -s 881QAESM95THY reboot bootloader
tools/platform-tools/fastboot -s 881QAESM95THY boot out/boot-meizu15-payload-<name>.img
# 或者插上就自动刷：
scripts/local/watch-and-flash.sh
```

屏上判读（顶部）：

```
kernel returned: reason 2, log 1307 bytes     ← reason 2 = 命中；0 = 崩了/超时
checkpoint 139 / last cp 139                  ← last cp 最可靠
v 18446744005119469344                        ← loader_probe_values[]
WIN <addr> HIGH <n> RUN <n> @ <n>             ← AllocateWindow 的读数（窗口落点）
LOAD <addr> USED <n> WANT <n>                 ← 窗口地址 / 已用 / 需要
```

## 7. 坑的记录

- **`Meizu15Launcher` 现在在固件里**（`meizu15.fdf.inc` 有 `INF`），所以 `Request.BootArgs`
  与环境 `unixnu.toml` 的 `args` **两份都生效**；改一处不生效时先看另一处。
- **payload 被 LZMA 压缩在 `FVMAIN_COMPACT` 里**，在 FD 明文搜 `FAT16` 永远搜不到。
- **`GenFds` 省略 payload 时没有明显报错**，必须解 FD 验证（`scripts/ci/verify-payload-image.py`）。
- **虚拟机时钟会跳**，`BUILD/obj/.*timestamp*` 落在未来会让 `make` 永久挂起，构建前删掉。
- **microSD/ESP 用 `hdiutil` 造**（`make-esp.sh`）：mtools 在 macOS 12 上装不了；
  hdiutil 出 FAT16/1 KiB 簇，mformat 出 FAT12/4 KiB 簇，EDK2 两种都认。
