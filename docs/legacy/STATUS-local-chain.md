# 今晚的进展（2026-11-24 凌晨）

> 你在睡觉，我把"本地编译"这条链打通了。手机被拔掉，所以还没有刷机验证。

## 一句话

**这台 Mac 现在能自己编 XNU 内核了**，不用推 GitHub、不用等 CI、不用下 artifact。
第一次编出来的 `mach.development.bcm2837` 是合法的 arm64 Mach-O，我们的补丁全在里面。

## 做成了什么

| 项 | 状态 |
|---|---|
| 工具链（clang/mig/ld/SDK/两个静态库） | ✅ 不需要 Xcode.app，CLT + HakiOS 的 theos-mac 就够 |
| xnu-8796.121.2 源码（tarball 14 MB，绕开 git fetch 的 200 MB） | ✅ |
| meizu 的四个补丁（t0sz、guards、PAN、sdm660 shim） | ✅ 61 + 2 + 9 处改动全部落地 |
| 内核编译 | ✅ **17,498,352 字节，Mach-O arm64** |
| 增量重编 | 3–5 分钟（改一个文件的话） |

产物：

```
src/hakios-xnu-kernel-main/xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/
    mach.development.bcm2837              17,498,352 B
    mach.development.bcm2837.unstripped   22 MB
```

## 两个重要发现

### 1. APFS 不用移植了 —— HakiOS 自己带了一份

`bsd/miscfs/apfs/`（只读 APFS：`apfs_lib.c` 105 KB + `apfs_write.c` + `apfs_vfsops.c`），
还有 `bsd/miscfs/ramfs/`（可写 RAM 文件系统）。本地编出的内核里：

```
apfs 相关字符串 222 处
apfs_mount_common / struct apfs_mount 在符号表
ramfs_create / ramfs_reclaim 在符号表
```

这推翻了我昨天写的"APFS 做不了"（当时只查了 Apple 开源树，没查 HakiOS 自己的 `files/`）。
P2 的正确顺序因此变成：**mockfs（零硬件，先跑通）→ eMMC 驱动 → APFS（HakiOS 版）**。

### 2. 内核比 ESP 大 —— 这是下一个要解决的问题

| | 大小 |
|---|---|
| ESP 上限（FFS 24 位 section） | 16,777,215 |
| CI 现在这份内核 | 16,283,328（放得下） |
| **本地新编的** | **17,498,352（放不下，多 826 KB）** |

段对比（本地 vs CI）：`__TEXT_EXEC` 9,256,960 vs 8,863,744（+393 KB），
`__TEXT` +16 KB，其余在符号/链接段。可能是 clang 14 与 CI 上 clang 的差异，
也可能是 25 个 HakiOS 驱动默认全开。

**解法（明天挑一个）**：
1. 用 `hakios-config` 关掉 meizu 不需要的驱动（Wi-Fi/GPU/modem/audio 全部不要），大概率省 1 MB 以上
2. 把 payload 机制换成不受 16 MB 限制的形式
3. 查清 clang 14 到底多编了什么

## 环境改动（都在本地副本里，注释写明了原因）

| 文件 | 改了什么 |
|---|---|
| `xnu-deps/setup.sh:32` | macOS SDK 下载走 gh-proxy |
| `xnu-deps/setup.sh:140` | `unifdef` 指到 CLT 里的真货（`/usr/bin/unifdef` 是桩） |
| `xnu-deps/host/bin/xnu-clang` | Darwin 上保留 `-enable-trivial-auto-var-init-zero…` |
| `xnu/apply.sh` | 新增 `XNU_TARBALL=` 分支 |

git 代理写在 `src/hakios-xnu-kernel-main/.gitconfig-proxy`（用 `GIT_CONFIG_GLOBAL` 指过去），
**没有动你的 `~/.gitconfig`**。

## 正在后台跑

`edk2-msm` 的 clone（30 MB 主仓 + 约 600 MB 子模块：edk2 346 MB、edk2-platforms 290 MB），
走 gh-proxy，估计还要 1–2 小时。这是打包 payload 必需的。

## 明天手机插回来之后

```bash
# 1. 确认设备
cd /Users/mac/Documents/deepseek-harness/default-workspace
./tools/platform-tools/adb devices

# 2. 刷（镜像还没做出来，先记住命令）
./tools/platform-tools/fastboot boot out/boot-meizu15-payload-debug.img
```

当务之急是**把新内核装进能刷的 boot.img**。在那之前，今晚这份内核只是"编出来了"，
还没有被设备验证过。

## 文档

- 完整的环境与命令：[LOCAL-BUILD.md](LOCAL-BUILD.md)
- P2 调研（含 GPT 全表、eMMC 控制器参数、参照项目对比）：[P2-FINDINGS.md](P2-FINDINGS.md)
- 设备原始读数：`out/device/`（GPT、eMMC identity、SDHCI 设备树、dmesg）

---

## 补记（凌晨 2:40）

### 减配实验失败

想通过关掉 25 个 HakiOS 驱动把内核压到 16 MB 以下：

```bash
tools/hakios-config.py defconfig common    # 生成 .config，25 驱动默认全开
# 把 CONFIG_HAKIOS_*=y 全改成 not set
tools/hakios-config.py kernel --tree ...   # "0 of 25 drivers built in ... 4 objects to be built again"
```

结果 `make` 卡在 config 阶段 10 分钟：无输出、CPU 0%、日志 0 字节（`build-nodev.log`），
没有 clang 子进程，只有 `make -r -f makedefs/MakeInc.top all` 在 `S` 状态。
已 kill 并把 `.config` 删掉（树回到"25 驱动全开"的默认状态，`MASTER.hakios` 已重新生成）。

`configs/.config.full`（其实在仓库根：`.config.full`）是那次实验的备份，明天可复用。

**明天可以试**：`xnu-build.sh -j6 -k`（`-k` 让 make 忽略错误继续），或者先编全量再增量关驱动。
另一个方向是**不压内核、改 packaging**：payload 卷受 FFS section 24 位限制（16 MB），
但可以把 payload 拆成两个 FFS 文件，或者换 UniXNURamDiskDxe 那条从分区读的路径。

### edk2 子模块

`edk2-porting/edk2-msm` 主仓（147 MB）已下来；子模块在下：
`Common/edk2`（346 MB）、`Common/edk2-platforms`（290 MB）、`SimpleInit`、`Installer`。
凌晨 2:40 时 `Common` 已有 30 MB、`Platform` 22 MB。走 gh-proxy，估计还要 1 小时以上。

### 补记 2（凌晨 3:00）：减配卡死的真正原因

不是口头说的"config 阶段玄学"，是**时间戳哨兵**：

`makedefs/MakeInc.top` 用 shell 循环等自己的时间戳文件：

```sh
while [ .new -ot .timestamp ]; do sleep 1; touch .new; done
```

这台 VM 的时钟会倒退（VMware 时间同步）。编译跑到 01:0x 之后时间回到 00:43，
上一次留下的 `.symbolset.timestamp` 就落在"未来"，`touch .new` 永远追不上它 ——
循环死转，make 卡在 `__wait4`，日志 0 字节，没有子进程。

`sample <pid>` 能看到 make 卡在 `__wait4`，`ps` 里能找到那条 `while [ ... -ot ... ]` 的 sh。

**修法**：编译前删掉 `BUILD/obj/.*timestamp*`。已写进 `build-local.sh`（新加的包装脚本）。

### 减配仍然没成功，但原因变了

删掉时间戳后编译能跑，但卡在另一处：`check_all_exports` 在 `config_all` 阶段就失败
（`exported name not in import list: IOService::_RegisterService(...)` 等一大串 DriverKit 接口），
导致 `config_all` 不产出 Makefile，后面的编译根本没开始。

第一次能编成功，是因为当时是**全新构建**：config 阶段先把内核编出来、再检查符号、检查失败但产物已在。
现在树里有一个"旧配置（25 驱动）"编出来的内核，符号表和新的 exports 列表对不上，
检查在 config 阶段就把流程掐了。

**明天要试的顺序**：先删掉旧内核产物和 `BUILD/obj/*/config/*`（迫使 config 阶段重新生成），
或者干脆 `rm -rf BUILD/obj` 全量重编一次无驱动版本（25 分钟）。

已把 `.config` 删掉，树回到 25 驱动全开、内核产物可用的状态。

---

## 补记 3（11-25 凌晨 1:00）：本地 payload 打包卡在 lld

### 已经做成的

- 减配内核：**16,512,424 字节**，塞进 15.9 MiB 的 ESP 后只剩 94 KB —— 装得下
- 本地 ESP 生成脚本 `out/make-esp.sh`：用 `hdiutil` 造 FAT 卷替代 `mtools`（macOS 12 装不了 mtools）
- 本地工具链补齐：nasm 2.16.01、ACPICA iasl、`aarch64-linux-gnu-{gcc,objcopy}` 的 clang shim
- edk2-msm 全套源码 + 子模块（brotli、openssl、softfloat、freetype）

### 卡在哪

`edk2` 的 CLANG38 toolchain 目标三元组是 `aarch64-linux-gnu`，链接要 **`ld.lld`**，而这台机器上：

```
$ xcrun -f ld.lld          → 没有
$ clang --target=aarch64-linux-gnu -fuse-ld=lld  → invalid linker name
$ ar/ranlib                → "Unsupported triple for mach-o cpu type: aarch64-unknown-linux-gnu"
```

Homebrew 也不给 macOS 12（Tier 3）提供 llvm 的 bottle；LLVM 官方 macOS 包约 2 GB，
在这个网速下不现实。

### 沿途修掉的（都在本地副本，注释写了原因）

| 文件 | 问题 |
|---|---|
| `build.sh` | BSD `getopt` 没有 `-l`；bash 3.2 没有 `typeset -l/-u` |
| `SimpleInit/src/host/rootfs.c` | 缺 `<limits.h>`（PATH_MAX）；Darwin 的 `st_atim/st_mtim` 叫 `st_atimespec/st_mtimespec` |
| `SimpleInit/scripts/gen-rootfs-source.sh` | `ld -Wl,-b,binary` 在 Darwin 上不存在 → 改用 `.incbin` 汇编 |
| `tools/BootShim/Makefile` 的 `aarch64-linux-gnu-*` | 用 clang shim + python 版 objcopy 顶替 |
| `msgfmt` | 写了个空 catalog 的 shim（SimpleInit 的翻译是装饰性的） |

### 三个出路（明天挑）

1. **装 LLVM**：brew 源码编 llvm 要 1–2 小时且有依赖；官方 2 GB 包在这网速下要 5 小时以上
2. **逆向现有 boot.img**：payload 在 FV 里是 LZMA 压缩的 FREEFORM section。解出来 → 换内核 → 重新压缩 →
   重算 FFS 头。今晚做了一半：FV 头解析出来了（32 MiB、BlockMap 0x2000×0x1000），但文件区第一个
   FFS 头位置还没对上（0xD8 处是全 FF，0xE8 处才出现数据）
3. **回到 CI 只打包**：本地编内核 → 推 `inputs/kernel.macho` → 让 CI 出 boot.img（等于每次仍要等 CI）

## 补记 4（11-25 凌晨 2:25）：edk2 本地构建卡在链接器

代理开了之后下载不再是问题：LLVM 15.0.7（694 MB，含 `ld.lld`/`llvm-ar`）3 分钟下完，
GNU binutils 2.41（26 MB）编译出 `aarch64-linux-gnu-ld`，两者都装进了 `/tmp/tools/bin`。

edk2 一路推进到 **295 MB 产物、几千个模块编译通过**，最后卡在链接器这一层，试了三种组合：

| 链接器 | 结果 |
|---|---|
| `ld.lld`（LLVM 15） | `GenFw: ERROR 3000 ... Sec.dll due to its size (> 1 MB), this module requires 4 KB section alignment` |
| GNU `ld` + `--ld-path` | `bad -plugin-opt option`（clang 当成 lld，塞 LTO 参数） |
| GNU `ld` + `-fuse-ld=bfd` | `common page size (0x10000) > maximum page size (0x1000)`，而 edk2 的 `-z common-page-size=0x20` 和我们的 `-Wl,-z,common-page-size=0x1000` 都没生效 |

沿途还修了：`-flto`（macOS 的 LLVM 包没有 LLVMgold 插件）、GCC LTO 的 `-llto-aarch64`、
`EFIAPI` 宏、`Uefi.h` 在大小写不敏感文件系统上的自包含死循环（改成 `simpleinit-uefi.h`）。

**结论**：edk2 在 macOS + CLANG38 + AARCH64 上的链接这一环，需要的是「GNU ld 的 -r 布局」这一组合，
而 macOS 上没有现成的 GNU aarch64 工具链；自己编的 binutils 能和 clang 对接，但页大小参数没落到 GNU ld 手里。

### 明天的最短路径（按可行性排）

1. **让 clang 直接调 GNU ld，跳过 clang 的链接驱动**：edk2 的 `CLANG38_AARCH64_DLINK_CMD` 改成本地脚本，
   直接把 `.o` 和 `-z` 参数喂给 `aarch64-linux-gnu-ld`（不经过 clang 的参数转换）。这是最可能一把过的。
2. 关掉 GenFw 的 1 MB/4 KB 断言（`Elf64Convert.c:1559`），让 lld 的产物直接过 —— 生成的 PE 可能不合规但大概率能跑。
3. 回 CI 只做打包（本地内核 → 推 `inputs/kernel.macho` → CI 出 boot.img）。

## 补记 5（11-25 凌晨 2:20）：今晚没有可刷的新镜像

尝试把 GenFw 的断言绕过去（`mCoffAlignment` 提到 0x1000），结果换了个错误：

```
AARCH64 small code model requires identical ELF and PE/COFF section offsets modulo 4 KB
```

因为 ELF 侧的段偏移仍是 lld 的 0x20 对齐，GenFw 要求两边 mod 4 KB 一致。方向错了：
正确做法是让 **ELF 段先 4 KB 对齐**（`llvm-objcopy --set-section-alignment`，或让 lld 在 `-r` 时对齐），
而不是在 GenFw 里假装对齐。已把 GenFw 回滚（`git checkout`），不留实验性改动。

构建推进到了 **430 MB 产物**，绝大多数模块（含 SimpleInit 的全部 libs）已经编译并链接通过，
只剩 `PrePi`/`Sec` 这类早期模块的 ELF→PE 转换。

**所以设备今晚保持原状，没有刷任何东西** —— `artifacts/boot-meizu15-payload-debug.img` 还是 CI 的旧镜像（cp 139），刷它只会复现已知状态。

### 明天从这三条里挑

1. **ELF 段先对齐**：edk2 链接后用 `llvm-objcopy --set-section-alignment .text=4096 --set-section-alignment .data=4096`
   （需要让 edk2 执行 objcopy 步骤，默认它是跳过的），再交给 GenFw。
2. **GNU ld 直连**：写一个 `CLANG38_AARCH64_DLINK_CMD` 替代脚本，把 `.o` 和 `-z common-page-size=0x20` 直接喂给
   `aarch64-linux-gnu-ld`，不经过 clang 的参数转换（clang 会吃掉 `-z`）。
3. **回 CI 只打包**：本地内核 → 推 `inputs/kernel.macho` → CI 出 boot.img（10 分钟，但要等 CI）。

## 补记 6（11-25 凌晨 3:25）：本地 payload 构建成功

**`out/boot-meizu15-payload-local.img` —— 8,359,936 字节，Android boot v0，可以 `fastboot boot`。**

这是这台 Mac 第一次完全本地产出的可刷镜像（内核 + payload + 固件引导器全部本地构建）。

### 最后一把怎么过的

两个改动一起生效：

1. **GenFw 禁用 ADRP→ADR 改写**（`BaseTools/Source/C/GenFw/Elf64Convert.c:1540` 的
   `if (mCoffAlignment < 0x1000)` 改成 `if (0)`）。原因：lld 的 `-r` 让段保持在 ELF 偏移（0x240）
   且只对齐到 32 字节，GenFw 就想把 ADRP 改写成 ADR —— 而 ADR 只够 ±1 MB。保持 ADRP 才对，
   因为 PE 段偏移此时与 ELF 一致（mod 4 KB），页偏移本来就正确。
2. **链接器换回 GNU ld，且不加任何页大小参数**（`-fuse-ld=bfd`，用 edk2 自带
   `-z common-page-size=0x20`）。之前失败是因为我自作主张加了 `-z max-page-size=0x1000`，
   导致 `common (0x10000) > max (0x1000)`。edk2 的默认值本来就是对的。

之前的 lld 路线失败在 `AARCH64 relative relocations require identical ELF and PE/COFF section offsets`
（SimpleInitMain.dll）——lld 的段太紧凑，GenFw 无法在不重排的情况下对齐。

### 产物

```
out/boot-meizu15-payload-local.img   8,359,936 B   kernel=8,355,041  page=4096  ramdisk=0
```

内含：本地编的 16,512,424 B 内核（无驱动版）+ ADT + 本地生成的 15.9 MiB ESP + Meizu15Pkg 固件。

### 刷机（设备现在不在 USB 上，睡前被拔了）

```bash
cd /Users/mac/Documents/deepseek-harness/default-workspace
./tools/platform-tools/adb -s 881QAESM95THY reboot bootloader
./tools/platform-tools/fastboot -s 881QAESM95THY boot out/boot-meizu15-payload-local.img
```

`fastboot boot` 只写内存，不碰分区。刷完屏幕应当停在 `last cp N`（或 panic 屏），
这就是这一轮的读数。若黑屏无输出，先用 `fastboot reboot` 恢复 Android 再查。

### 还没验证的

镜像**没有被设备验证过**——构建成功只说明工具链通了，不说明内核能启动。
无驱动内核少了 25 个 HakiOS 驱动（全是别的 SoC 的），理论上不影响到 `bsd_init` 的路径，
但 `hakios.platform`/`hakios.stubs` 之外的初始化是否依赖某个被关掉的驱动，只有刷了才知道。

## 补记 7（11-25 凌晨 4:35）：镜像结构已逐层验证

设备不在 USB 上，所以从镜像本身取证。

**boot.img 的完整链条，每一层都解出来看过了**：

```
boot.img                        8,359,936 B   ANDROID! v0, page 4096
  └─ kernel (gzip)              8,355,041 B
      └─ FD                    33,554,576 B   BootShim(0x90) + FVMAIN_COMPACT(32 MiB)
          └─ LZMA section       7,961,080 B   gLzmaCustomDecompressGuid, uncompressed 41,641,736
              └─ FVMAIN        41,641,736 B
                  └─ ESP       16,711,680 B   FAT16, 1 KiB clusters, 32640 sectors, @0x17c66ec
                      └─ EFI/UniXNU/Payloads/kernel.macho   16,512,424 B  ← 本地编的那个内核
                         EFI/UniXNU/Payloads/kernel.macho.adt   13,996 B
                         EFI/UniXNU/unixnu.toml                    273 B
```

ESP 的目录树用 FAT16 解析器逐项读过，三个文件的长文件名（LFN）都在，
内核字节数与 `mach.development.bcm2837` 完全一致。

**所以镜像本身是完整的**，不是"构建成功但 payload 没打进去"。

### 已经挂上的东西

- `out/watch-and-flash.sh` 在后台跑（每 20 秒查一次 USB）：设备一旦以 adb 出现，
  自动 `reboot bootloader` → `fastboot boot out/boot-meizu15-payload-local.img`，
  之后 15/30/60/120 秒各记一次 USB 状态到 `out/flash-log.txt`。
  **USB 重新枚举与否是唯一不依赖摄像头的判据**：内核挂住 → 设备保持"已启动"（USB 消失或保持）；
  内核崩了自动重启 → 会重新出现 fastboot/adb。屏幕上的 `last cp N` 仍需要人看。
- B 方案已就绪：`artifacts/boot-meizu15-payload-debug.img`（CI 的全驱动内核，已知能到 cp 139）。
  A 失败就刷它做对照，能把"减配导致的问题"和"本地工具链导致的问题"分开。

## 补记 8（11-25 凌晨 2:50）：多做了一版 P2 镜像，等设备

设备一直没插回来。这段时间做了两件事，都不是白等。

### 1. 确认 mockfs 已经在核心里

用 `llvm-nm` 查减配版内核（`out/kernel-nodev.macho`，16,512,424 B）：

```
_mockfs_fsnode_create / _mockfs_fsnode_adopt / _mockfs_blockmap / _mockfs_mountroot
_apfs_mount / _ramfs_create / _vfs_mountroot / _bsd_init / _loader_checkpoint
```

**`_mockfs_mountroot` 在里面** —— mockfs 早就编进内核了（HakiOS 的 `bsd/conf/files` 里
`optional mockfs` 被它们的默认配置带上）。也就是 `rd=md0` 这条路不需要重编内核，只差引导参数和 initrd。

### 2. 第二版镜像：`out/boot-meizu15-payload-rd0.img`

在 A 版（`boot-meizu15-payload-local.img`，cp 139，无根卷）之外，又做了 B 版：

| 改动 | 位置 |
|---|---|
| `Request.BootArgs` 加 `rd=md0` | `Meizu15Launcher.c` |
| ESP 里加 `Payloads/launchd.macho` | `out/make-esp.sh`（新增 `INITRD=` 变量） |
| `unixnu.toml` 加 `initrd = "Payloads/launchd.macho"` | `out/unixnu.toml` |

`launchd.macho` 是一个 **16,472 字节的静态 arm64 Mach-O**：往 stdout 写 `M15OK` 然后 `exit(0)`，
用 `clang --target=arm64-apple-ios16.5 -nostdlib -static` 编的，`codesign --sign - --pagesize 16384`
签过（CDHash `ed060819…`，ad-hoc）。它是 mockfs legacy 模式下的整个设备内容，也就是 `/sbin/launchd`。
签名的 trust cache 也生成了（`/tmp/m15root/trustcache.bundle`，57 字节），但当前 payload 链路没有
传 trust cache 的字段，先留着。

验证过 B 版镜像：解出 FVMAIN 后能看到 `rd=md0`、`launchd`、`M15OK` 三个字符串都在。

### 3. 监视脚本已切到 B 版

`out/watch-and-flash.sh` 现在刷 `boot-meizu15-payload-rd0.img`。设备一插上（adb 可见）：

1. `reboot bootloader`
2. `fastboot boot` B 版
3. 15/30/60/120 秒各记一次 USB 状态到 `out/flash-log.txt`

**期望的读数**：`last cp 139` + 屏幕上出现 mockfs 挂载相关的打印，或者 `reason 0` 说明
`rd=md0` 这条路在挂载阶段炸了。两者都是 P2 的信息。

如果 B 版连 cp 139 都到不了，再刷 A 版（`local.img`）区分"是 P2 改动的问题"还是"本地构建的问题"。

## 补记 9（11-25 凌晨 3:15）：第三版镜像，带 140/141/142 探针

设备还是没有。这一轮把"第一次刷就能拿到最多信息"这件事做完了。

### 探针插在 `bsd_init()` 里

`bsd/kern/bsd_init.c`（本地副本，不在 `apply_nonapple_guards.py` 里 —— 那是仓库的补丁脚本，这次的
探针还没回写进去）：

```c
extern void loader_checkpoint(uint64_t);
loader_checkpoint(140);          /* vfs_mountroot 之前 */
if (0 == (err = vfs_mountroot())) { ... }
loader_checkpoint(141);          /* vfs_mountroot 返回后 */
...
loader_checkpoint(142);          /* load_init_program 之前 */
```

反汇编确认：`_bsd_init` 里有 2 处 `bl loader_checkpoint`（141 可能在分支里被合并），全内核 134 处。

### 三版镜像

| 镜像 | 内容 | 用途 |
|---|---|---|
| `boot-meizu15-payload-local.img` | cp 139，**无** rd=md0 | 只验证本地构建链 |
| `boot-meizu15-payload-rd0.img` | cp 139 + rd=md0 + initrd | 验证 mockfs 根 |
| `boot-meizu15-payload-probes.img` | 上面 + 140/141/142 探针 | **当前监视脚本刷的这版** |

C 版验证过：解出 FVMAIN 后 `rd=md0`、`M15OK`、内核目录项都在。

### 读数表（C 版）

| `last cp` | 含义 |
|---|---|
| 139 | 卡在 `vfs_mountroot()` 内部（140 都没到）——挂载路径的第一句就没过 |
| 140 | 进了 `vfs_mountroot()`，没返回 —— 挂在查找根设备或 mockfs 挂载里 |
| 141 | `vfs_mountroot()` 返回了 —— mockfs 有动作，结果看 panic 文本或 142 |
| 142 | 挂载成功，正在 exec init —— **P2 达成**，剩下的是 AMFI/launchd |

## 补记 10（11-25 凌晨 3:55）：探针位置修对了，并写回补丁脚本

### 上一版探针是错的

`loader_checkpoint(141)` 被我插在 `bsd_init_kprintf("vfs_mountroot\n")` 之后、**`vfs_mountroot()` 调用之前**,
所以它记录的是"即将调用"，和 140 重复，读不出挂载结果。已改成：

```c
loader_checkpoint(140);                        /* 调用前 */
if (0 == (err = vfs_mountroot())) {
    loader_checkpoint(141);                    /* 挂载成功 */
    break;
}
loader_checkpoint(143);                        /* 挂载失败 */
```

142 仍在 `load_init_program(p)` 之前（挂上之后、exec 之前）。

新镜像 `out/boot-meizu15-payload-probes2.img`（8,364,032 B），反汇编确认 `_bsd_init` 里 3 处
`bl loader_checkpoint`。监视脚本已切到这版。

### 读数表（probes2）

| `last cp` | 含义 |
|---|---|
| 139 | 连 `bsd_init()` 里的 140 都没到 |
| 140 | 进了 `vfs_mountroot()` 调用，没回来 |
| **141** | **mockfs 挂载成功** |
| 143 | 挂载失败（`rootdevice[0]='\0'` 重试路径） |
| 142 | 挂载成功且走到 exec init —— P2 达成 |

### 探针写回了 `apply_nonapple_guards.py`

新增 `BSD_INIT_PROBES` 表 + `add_bsd_init_probes()`，在 `main()` 里 `find(root, "bsd/kern/bsd_init.c")`
之后调用。做了单测（`/tmp/bsdtest/`）：第一次插入四条、第二次全部 "already present"，幂等；
插入的缩进跟随 needle 自身的缩进。这样 `xnu/apply.sh` 重跑时探针不会丢。

### 一键构建

`out/build-round.sh <name>`：删旧内核 → 编内核 → 生成 ESP（带 initrd）→ 编固件 → retarget，
产出 `out/boot-meizu15-payload-<name>.img`。probes2 就是用这个跑的，全程约 13 分钟。

## 补记 11（11-25 凌晨 3:25）：镜像内容逐字节确认

`probes2` 这一版，从 ESP 里把文件读出来跟树里的产物比对过：

| 文件 | ESP 里 | 树里 | 结论 |
|---|---|---|---|
| `kernel.macho` | 16,512,424 B, magic `cffaedfe`, sha256 `0a8352c6…` | 同 size / 同 magic / **同 sha256** | 一致 |
| `launchd.macho` | 35,040 B, magic `cffaedfe` | 35,040 B（codesign 后从 16,472 涨到 35,040，签名页） | 一致 |
| `kernel.macho.adt` | 13,996 B | 13,996 B | 大小一致 |

`sdm660_shim` 的符号也确认在减配内核里：`_sdm660_shim_start`、`_sdm660_gic_probe`、
`_sdm660_timer_count`、`_sdm660_log`。

### 早上插上手机之后

`out/watch-and-flash.sh` 会自动刷 `boot-meizu15-payload-probes2.img`。如果它没自动跑起来（比如
Mac 睡过），手动两条：

```bash
cd /Users/mac/Documents/deepseek-harness/default-workspace
./tools/platform-tools/adb -s 881QAESM95THY reboot bootloader
./tools/platform-tools/fastboot -s 881QAESM95THY boot out/boot-meizu15-payload-probes2.img
```

### 决策树

| 你看到的 | 下一步 |
|---|---|
| `last cp 139` | 连 `bsd_init()` 里的 140 都没到 → 减配或本地构建有问题，刷 `…-local.img` 对照 |
| `last cp 140` | 进了 `vfs_mountroot()` 没回来 → mockfs 挂载里挂住，下一步在 `mockfs_mountroot` 里插探针 |
| `last cp 141` | **mockfs 挂载成功** → P2 达成，下一步是 142（exec init），看 AMFI 拦不拦 |
| `last cp 143` | 挂载返回失败 → 看 `errno`，多半是 md0 没建立 |
| `last cp 142` | 挂载 + exec 都走到了 → launchd 该跑起来了，看 stdout 有没有 `M15OK` |
| 黑屏无输出 | 减配砍到了必需路径，回退 25 驱动版（需要先解决 16 MB 容量） |

## 补记 12（11-25 凌晨 4:15）：`Meizu15Launcher.c` 是死代码，boot-args 走 toml

这一轮想做一个干净的对照组（同批参数、只差 `rd=md0`），结果发现改 launcher **改了等于没改**：

- `Meizu15Launcher.inf` 只在 `meizu15.dsc` 的 Components 里（会被编译），**不在任何 FDF 的 INF 段**里
  （`meizu15.fdf.inc` 里只有 `DarwinBoot.inf` 和 payload 的 `FILE FREEFORM`）
- 所以 `Meizu15Launcher.efi` 编出来了（32 KB），但没有进固件
- 证据：`local2`（launcher 里删掉 rd=md0）和 `probes3`（launcher 里有 rd=md0）解出来的 FVMAIN
  **只差 70 字节**，而且差异全部落在 ESP 的 FAT 引导扇区（时间戳），launcher 模块那边一个字节都没动

真正生效的是 **ESP 里 `unixnu.toml` 的 `args`**：入口是 `sdm660.fdf` 里的 `SimpleInitMain`
（`INF src/main/SimpleInitMain.inf`），它读 payload 的 toml，把 `args` 交给固件里的 `DarwinBoot`。

所以：

| 文件 | 是否影响启动参数 |
|---|---|
| `unixnu.toml` 的 `args` | **是**（`rd=md0` 在这里） |
| `Meizu15Launcher.c` 的 `Request.BootArgs` | 否（模块不在固件里） |

### 现在的最终镜像

`out/boot-meizu15-payload-probes3.img`（8,359,936 B）= 探针 140/141/143/142 + `rd=md0`（toml）+
initrd（launchd）+ 减配内核。监视脚本刷的就是它。

**对照组的正确做法**（如果以后需要）：改 `out/unixnu.toml` 的 `args` 去掉 `rd=md0`，重新生成 ESP，
而不是动 launcher。

## 补记 13（11-25 凌晨 4:10）：减配风险评估 + 容量方案的实测数字

设备仍然不在，这一轮做的是"如果减配失败，退路有多少"的量化。

### 减配砍掉的驱动在启动路径上都有 stub

`bsd_init()` 里直接调用这些（`bsd/kern/bsd_init.c:991-998`）：

```c
hakios_version_init();      /* standard, kept */
hakios_input_init();        /* driver INPUT, disabled */
hakios_audio_init();        /* driver AUDIO/ADSP/SPEAKER/SOUND, disabled */
hakios_virtio_blk_init();   /* driver VIRTIO_BLK, disabled */
hakios_sparse_init();       /* standard, kept */
hakios_apfs_check_init();   /* standard, kept */
```

被关的那三个由 `bsd/dev/hakios.stubs.c` 提供空实现——**构建链接通过本身就是证据**
（符号有定义，否则链接会失败）。所以减配不会因为"缺函数"而在 `bsd_init` 里炸。

### 25 驱动版真能塞进 payload，只要压缩

实测压缩率（当前 16.5 MB 内核）：

| 方式 | 压缩后 | 比率 |
|---|---|---|
| gzip -9 | 5,657,401 B | 2.9:1 |
| lzma preset 6 | 4,055,332 B | 4.1:1 |

25 驱动版 17,498,352 B 按同样比率约 4.3 MB（lzma），**远远小于 16 MiB**。

**但没有做**，原因是代价不成比例：DarwinBoot 现在只用 `ReadFile()` 读内核，加解压要
①在 `DarwinBoot.inf` 里接一个解压库（edk2 树里没有现成的 zlib/LZMA 解压库，只有
`BaseTools` 的压缩工具），②改 `ReadFile` 的调用点，③重编固件并祈祷不破坏现在能用的镜像。
而它只有在"减配版失败"时才有用。

**所以顺序是**：先看 probes3 刷出来的 `last cp`。如果是 139/140/143 这类"减配之外的原因"，
容量方案根本用不上；只有确认"是少驱动导致的"才值得做。方案和数字都记在这里，随时可以捡起来。

## 补记 14（11-25 凌晨 3:50）：ESP 的 FAT 结构过了 fsck

之前只用自己写的解析器读过 ESP，这一轮用 Darwin 自带的 `fsck_msdos` 验了一遍：

```
$ /sbin/fsck_msdos -n .../unixnu-esp.img
** Phase 1 - Preparing FAT
** Phase 2 - Checking Directories
** Phase 3 - Checking for Orphan Clusters
7 files, 60 KiB free (60 clusters)
rc=0
```

**rc=0，没有孤儿簇、没有目录错误。** 这条重要，因为 ESP 是 `hdiutil` 造的（不是 build-esp.sh 的
mformat），我一直不太确定它的 FAT16 布局会不会被 EDK2 的 FatPkg 接受。fsck 通过说明结构合法；
7 个条目 = `EFI/UNIXNU/PAYLOADS` 三个目录 + `kernel.macho` / `kernel.macho.adt` / `unixnu.toml` /
`launchd.macho` 四个文件，**没有隐藏文件**（`.fseventsd`、`._*` 都没有，`COPYFILE_DISABLE=1` 生效了）。

60 KiB 空闲 —— 离满还有余量，但不多。以后往 ESP 里加东西（比如 trust cache）要注意。

### 到此为止的静态证据清单

| 已经证明的 | 用什么证明 |
|---|---|
| 内核是合法 arm64 Mach-O | `file` + 段布局 |
| 补丁进内核 | `llvm-nm`（shim/mockfs/apfs/ramfs 符号）+ 反汇编（探针） |
| ESP 里就是这版内核 | sha256 比对 |
| ESP 的 FAT 结构合法 | `fsck_msdos` |
| payload 在固件里 | 解 FD → FVMAIN → LAT/LZMA → ESP 四层 |
| 镜像头是 stock boot header | `retarget_header.py` + 头部字段核对 |

**没有证明的只有一件事**：这堆东西从 eMMC 读出来、经 UEFI 交给内核之后，能不能跑到 `bsd_init`。
那需要设备。

## 补记 15（11-25 凌晨 4:05）：内核没有未定义符号

```
$ llvm-nm -u out/kernel-nodev.macho | wc -l
0
```

内核是完整静态链接的，没有未解析符号 —— 这一条排掉了"链接时漏了某个 stub 导致加载失败"的可能。
到这一步，静态检查能做的都做完了：

- 镜像逐层结构 ✅
- ESP 的 FAT 合法（fsck）✅
- ESP 里的内核与树里产物 sha256 一致 ✅
- 内核符号完整（无 undefined）✅
- 探针/驱动/sh 文件系统符号都在 ✅

**唯一无法静态回答的问题**：这套东西在真机上能不能跑到 `bsd_init`。

### 如果手机一直插不回来

`out/watch-and-flash.sh` 会一直等（每 20 秒查一次），随时可以插。手动两条也行：

```bash
cd /Users/mac/Documents/deepseek-harness/default-workspace
./tools/platform-tools/adb -s 881QAESM95THY reboot bootloader
./tools/platform-tools/fastboot -s 881QAESM95THY boot out/boot-meizu15-payload-probes3.img
```

## 补记 16（11-25 凌晨 4:20）：16 MB 的限制被拆掉了 —— 25 驱动版也做出来了

这一轮把上一轮"只有数字、没动手"的容量方案真正落地了。

### 做法：DarwinBoot 解 LZMA 内核

1. `DarwinBoot.inf` 加 `LzmaCustomDecompressLib`；`meizu15.dsc` 补它的实例行
   （DSC 里原本没有 `[LibraryClasses]` 段，加在 `[Components]` 前面才生效）
2. `DarwinBoot.c` 在读完内核后判断首字节：`0x5D` 就当成 EFI LZMA（5 字节 props + 8 字节 size + 流）
   解压，其它情况原样使用——**向后兼容**，旧的不压缩内核照样能引导
3. `out/lzma-pack.py` 负责打包（`lzma.FORMAT_RAW`，lc=3 lp=0 pb=2，dict 16 MiB，前面加 13 字节头）
4. `out/make-esp.sh` 的内核 magic 检查现在接受 `cffaedfe`（Mach-O）或 `5d000000`（LZMA）

解压函数是库私有的 `LzmaDecompressLibInternal.h` 里的声明，所以 DarwinBoot 自己写了函数原型
（注释里写明来源），不 include 私有头。

### 结果

```
17,498,352 B 内核（25 驱动全开）→ 4,368,146 B LZMA → 装进 16 MiB ESP
out/boot-meizu15-payload-full25.img   8,732,672 B
```

验证：解 FD → FVMAIN → ESP → 找到 `5d 00 00 00 01` → 解压 → **17,498,352 B，magic cffaedfe**。

### 现在的镜像谱系

| 镜像 | 内核 | 探针 | rd=md0 | initrd |
|---|---|---|---|---|
| `…-local.img` | 减配 16.5 MB | — | — | — |
| `…-local2.img` | 减配 16.5 MB | ✓ | — | — |
| `…-probes3.img` | 减配 16.5 MB | ✓ | ✓ | ✓ |
| `…-lzma.img` | 减配 16.5 MB（压缩） | ✓ | ✓ | ✓ |
| **`…-full25.img`** | **25 驱动 17.5 MB（压缩）** | ✓ | ✓ | ✓ |

监视脚本现在刷 **full25**：驱动最全、有探针、有根卷路径。**这一版能启动的话，减配这个变量就彻底不存在了。**

## 补记 17（11-26 凌晨 0:00）：真机验证了，卡在"固件进 Shell 而不是启动内核"

设备 22:06 第一次插回来，之后又刷了六版。**每一版都停在 SimpleInit 的 `Shell>` 提示符**，
内核一次都没启动。这一轮的价值是把"为什么"从猜测变成了可以逐步排除的链条。

### 刷过的版本与结果

| 版本 | 改动 | 结果 |
|---|---|---|
| `…-full25.img` | 25 驱动 + LZMA + 探针 + rd=md0 | `Shell>` |
| `…-launcher.img` | 把 `Meizu15Launcher` 加进 FDF | `Shell>` |
| `…-startup.img` | ESP 加 `startup.nsh` 指向 launcher | `Shell>` |
| `…-bootefi.img` | launcher 复制到 `EFI/BOOT/BOOTAA64.EFI` | `Shell>` |
| `…-fat12.img` | ESP 改 FAT12/4 KiB 簇（对齐 CI 的 mformat） | `Shell>` |
| `…-nopayload.img` | 找不到 payload 时永久停屏 | 没有那行 → **payload 被找到了** |
| `…-blockprobe.img` | 自检 BlockIo 第 0 块并停屏 | 已刷，屏幕输出待确认 |

### 已经排除的原因

- **不是 launcher 没编进去**：`MODULE_TYPE = UEFI_APPLICATION`，改 FDF 也不会自动执行；
  `startup.nsh` 在 ESP 根目录、`EFI/BOOT/BOOTAA64.EFI` 也放了，BDS/shell 都没用它
- **不是 FAT16 的锅**：换成 FAT12、4 KiB 簇（和 CI 的 `mformat -c 8` 同构）仍然一样
- **不是 payload 找不到**：`…-nopayload.img` 会在找不到时永久停屏并刷屏，屏幕上没有那行，
  说明 `Meizu15PayloadDxe` 走进了 `Publish` 分支
- **不是 payload 越界**：解出来的 FVMAIN 里，`_FVH` 在 0x8，`FvLength = 0x26a8740`，
  payload FFS 落在 `0x17d0710..0x26a872c`，**在 FV 范围内**

### 还没排除的

1. `Meizu15PayloadDxe` 发布了 BlockIo，但 `FatDxe` 没绑上或挂载失败 → 没有 `SimpleFileSystem`
2. BDS 有 `SimpleFileSystem` 却没把 `EFI/BOOT/BOOTAA64.EFI` 当启动项
3. `Media.LogicalPartition = TRUE`（原注释说这是为了不让 PartitionDxe 把 FAT 引导扇区当 MBR 解码）
   是否反而让 FatDxe 不认这个设备

`…-blockprobe.img` 就是为了区分 1 和 2/3：它读 BlockIo 第 0 块并打印 OEM/类型字段后停屏。
如果那行显示 `oem=MSWIN4.1 type=FAT12`，说明 BlockIo 正常、问题在下游；如果读失败或内容不对，
问题就在 `Meizu15PayloadDxe` 自己。

## 补记 18（11-26 凌晨 0:15）：真机诊断把问题钉在了 DiskIo 这一层

### `…-blockprobe.img` 的屏幕输出（关键证据）

```
MZ15>> === Meizu15PayloadDxe ENTER ===
MZ15>> own volume is at handle C8C0DA98
MZ15>> payload FFS is file 3 of this FV, 15564804 bytes
MZ15>> got 15564800 bytes from EFI_SECTION_RAW
MZ15>> publishing 15564800 byte volume
MZ15>> ReadBlocks lba 0 size 512
```

**payload 找到了、卷发布了、BlockIo 被读了**。这一屏把"payload 没进固件""卷没发布"这两条
彻底排掉。我上一版自检里用的 `Print()` 在 DXE 这个阶段没有 console 可写（日志走的是 DEBUG→串口/控制台），
所以只看到 `ReadBlocks` 而没有 oem/type 那行——改成 DEBUG 后就是这个效果，不是读失败。

### 读 EDK2 源码找到的因果链

```c
// FatPkg/EnhancedFatDxe/Fat.c: FatDriverBindingSupported()
Status = gBS->OpenProtocol (ControllerHandle, &gEfiDiskIoProtocolGuid, ...);
if (EFI_ERROR (Status)) { return Status; }      // ← 没有 DiskIo 就直接不支持
```

`Meizu15PayloadDxe` 只安装了 **BlockIo + DevicePath**，没有 DiskIo。DiskIoDxe 在 FDF 里
（`INF MdeModulePkg/Universal/Disk/DiskIoDxe/DiskIoDxe.inf`，sdm660.fdf:145），它负责在 BlockIo
之上装 DiskIo——但原代码里 `Media.LogicalPartition = TRUE` 的注释写明这是为了让 PartitionDxe
不要把 FAT 引导扇区当 MBR 解码。**TRUE 的副作用是 DiskIoDxe/PartitionDxe 都把它当"分区"而不是整盘**，
于是 FatDxe 的 Supported 拿不到 DiskIo，`SimpleFileSystem` 从未出现，BDS 只能掉进 shell。

### 刚刷的版本

`out/boot-meizu15-payload-logical.img`：把 `LogicalPartition` 改回 `FALSE`（整盘语义），
并在源码注释里写清了为什么。这是"FatDxe 能不能绑上"的一次直接实验。

### 如果这次还是 Shell

下一步不再是猜，而是把 DXE 的绑定结果打出来：在 PayloadDxe 里延迟 1 s 后
`LocateHandleBuffer(ByProtocol, &gEfiSimpleFileSystemProtocolGuid)`，数一数有几个文件系统、
以及我们发布的那个 handle 上到底挂了 DiskIo / SimpleFileSystem 没有。
