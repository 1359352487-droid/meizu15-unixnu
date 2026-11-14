# 真机测试记录

## 2026-11-12 23:05 — 第一次 `fastboot boot` 自定义 UEFI 镜像（hdrfix）

命令：

```powershell
adb reboot bootloader                       # 4 s 后进入 fastboot
fastboot boot meizu15\artifacts\boot-meizu15-hdrfix.img
```

应答（原样）：

```
Sending 'boot.img' (6080 KB)                       OKAY [  0.129s]
Booting                                            OKAY [  0.016s]
Finished. Total time: 0.164s
```

主机侧观察：`boot` 返回后 150 s 内 adb 不出现、fastboot 不出现、Windows USB 枚举里没有任何 `05C6`/`18D1` 设备。

## 屏幕现场（22:58 照片，关键证据）

**固件跑起来了**：屏幕显示的是 EDK2 的异常处理器 dump，说明
`BootShim → PEI → DxeCore → BDS → SimpleInit` 全部执行到，GOP 帧缓冲与面板初始化正常。

### 第一条非致命信息

```
SetupAPSScpuPerfLevels: Failed to get the maximum performance level for CPU 0, Status: Invalid Parameter
```

CPU 性能档位表没读到（SDM660 的 RPM/DVFS 与 edk2-msm 假定不符），不致命。

### backtrace

```
PC 0x0000C4951F3C  (0x0000C48E2000 + 0x6FF3C)   [0] SimpleInitMain.dll
PC 0x0000C48E3EF0  (0x0000C48E2000 + 0x1EF0)    [0] SimpleInitMain.dll
PC 0x0000C4E6BEEC  (0x0000C4E6C000 + 0x5EEC)    [1] DxeCore.dll
PC 0x0000C4FF31BC  (0x0000C4DF3000 + 0x1C1BC)   [2] BdsDxe.dll
PC 0x0000C4C952B4  (0x0000C4C93000 + 0x32B4)    [2] BdsDxe.dll
PC 0x0000C4C6B1B4  (0x0000C4C6B000 + 0x2B14)    [3] DxeCore.dll
PC 0x0000C4FF3114  (0x0000C4DF3000 + 0x10114)   [3] DxeCore.dll
PC 0x0000C4C6BA014 (0x0000C4C6B000 + 0x101014)  [3] DxeCore.dll
PC 0x0000CE0026B0
PC 0x0000CE008150
```

模块路径全部是 `.../Build/meizu15/RELEASE_CLANG38/AARCH64/...`：`SimpleInitMain`、`DxeCore`、`BdsDxe`。

### 异常类型

```
ESR : EC 0x25  IL 0x1  ISS 0x00000004
Data abort: Translation fault, zeroth level
```

`EC=0x25` = 同 EL 数据中止；`ISS&0x3F = 0x04` = **translation fault, level 0**（页表里该地址压根没映射，不是权限问题）。

### 寄存器（照片）

```
X0  0x10                 X1  0xC4A?5953          X3  0xFFFFFFFFFFFFFFF0
X19 0x0                  LR  0x0000C4997C5C  (= 0xC48E2000 + 0xB5C5C)
FP  0x00000009FFCE250
```

`X3 = 0xFFFFFFFFFFFFFFF0` = `0 - 0x10`，`LR = SimpleInitMain + 0xB5C5C` —— 正好落在下面那条指令上。

## 反汇编定位（本地从 boot 镜像里解出来的 SimpleInitMain，8,962,048 B PE32）

### 崩溃点 A：`0xB5C24` 函数里的空指针解引用

```
0B5C24  stp x29, x30, [sp, #-0x30]!
...
0B5C34  mov x19, x0                  ; 参数 = 要释放/校验的指针
0B5C38  sub x20, x0, #0x20           ; 块头 = ptr - 0x20
0B5C50  mov w21, #0x7043
0B5C54  movk w21, #0x6468, lsl #16   ; magic "Cphd"
0B5C58  bl #0x1264                   ; FreePool 跳转桩
0B5C5C  ldur w3, [x19, #-0x10]       ; ★ x19 = 0 时访问 0xFFFFFFFFFFFFFFF0
0B5C60  cmp w3, w21                  ; 校验 magic
```

这就是 17:28 交接里记的「`SimpleInitMain` FreePool NULL 检查崩溃，RVA `0xB5C24`」——**同一个故障**，那次只记了函数入口，这次拿到了寄存器现场。

### 崩溃点 B：`0x6FF3C` 的未初始化全局写

```
06FF2C  adrp x19, #0x81f000
06FF30  ldr x0, [x19, #0xf08]        ; 懒初始化的全局指针
06FF34  cbnz x0, #0x6ff44
06FF38  bl #0x30238                  ; 分配器（自身正确保存 x19~x22）
06FF3C  str x0, [x19, #0xf08]        ; ★ 写入未映射地址
06FF40  cbz x0, #0x6ff48
```

`0x30238` 是合规 callee（序言 `stp x29,x30` / `stp x22,x21` / `stp x20,x19`），所以不是它 clobber；更可能是 `x19` 在该路径上被改写或该静态区未按 `SizeOfImage=0x88C000` 完整映射。

## 结论

| 项 | 判定 |
|---|---|
| BL 接受非签名镜像 | ✅ `Booting OKAY` |
| BootShim 搬运 + 跳转 | ✅ |
| PEI / DxeCore / 驱动派发 | ✅（DxeCore 出现在 backtrace 里） |
| 显示（GOP + 1080×1920 面板） | ✅ 屏幕能画出整屏文字 |
| BDS 加载应用 | ✅ `SimpleInitMain` 已 LoadImage 并执行 |
| SimpleInit 自身 | ❌ 空指针 / 未映射写，数据中止 |

**不是硬件问题，是 SimpleInit 这一层的软件 bug。**

## 下一步

1. 长按电源 12 s 重启回 Flyme（设备现在停在异常界面）。
2. 让 SimpleInit 崩溃可定位到源码行：CI 改 `-r DEBUG`（当前 `RELEASE_CLANG38` 只有模块名）。
3. 给 `0xB5C24` 对应的释放函数加 `if (!ptr) return;`，或修掉调用处传 NULL 的来源。
4. 复测：`fastboot boot` 同一镜像（改完重编）。

## 2026-11-12 23:03 — 补丁尝试 1：重压 LZMA（`boot-meizu15-nullpatch.img`）

改动：把内层 `SimpleInitMain` 的 `0xB5C24` 从 `fd7bbda9`（`stp x29,x30`）改成 `c0035fd6`（`ret`），
用 Python `lzma.compress(FORMAT_ALONE, dict=16MiB, lc3/lp0/pb2)` 重压，重建 FFS 文件与 FV，再 gzip。

结果：`Sending OKAY / Booting OKAY`，但 **6 秒后设备回到 fastboot**（没有进 UEFI）。
判定：重压出的 LZMA 流或重建的 FV 结构不被接受 —— 这是打包问题，不是补丁内容问题。

## 2026-11-12 23:07 — 补丁尝试 2：不压缩内层卷（`boot-meizu15-patch-uncompressed.img`）

思路：把 GUIDED+LZMA section 换成未压缩的 `FV_IMAGE`(0x17) section，直接放 29,671,488 B 的内层卷。

结果：**不可行**。FFS 文件头 size 字段只有 **24 位**（上限 0xFFFFFF = 16 MiB），
`0x1C4C660` 被截断成 `0xC4C660`（12,895,840），STRUCT 里写出来的文件长度错误。
这也解释了官方构建为什么一定要压缩。

结论：改 SimpleInit 必须解决 LZMA 重压的兼容性，或者走 CI 重新编译。

## 2026-11-13 00:12 — DEBUG 镜像：根因确认（free(NULL)）

`fastboot boot boot-meizu15-debug-hdrfix.img`（原厂头 + DEBUG payload，6,279,168 B）：
屏幕出 `Initialize SimpleInit GUI...`，随后异常。关键寄存器：

```
PC  0xC482FEFC  (0xC482E000 + 0x1EFC)      -- 不可信（cbnz，不访存）
ELR 0xC48E3D0C  (0xC482E000 + 0xB5D0C)     -- 真凶
FAR 0xFFFFFFFFFFFFFFF0                     -- == NULL - 0x10
ESR 0x25 / ISS 0x4                         -- translation fault, level 0
```

DEBUG 版 `SimpleInitMain` 在 0xB5D0C 的指令是：

```
0B5CE4  mov  x19, x0                     ; Ptr
0B5CE8  sub  x20, x0, #0x20              ; Head = Ptr - 0x20
0B5D08  bl   #0x14c0                     ; DEBUG("free(%p): Head: %p\n", Ptr, Head)
0B5D0C  ldur w3, [x19, #-0x10]           ; ★ Head->Signature，x19 = 0
0B5D10  cmp  w3, w21                     ; w21 = 0x64687043 = "Cphd"
```

### 源码

`BigfootACA/simple-init` → `libs/compatible/malloc.c`

```c
#define CPOOL_HEAD_SIGNATURE   SIGNATURE_32('C','p','h','d')

weak_decl void free(void *Ptr)
{
	CPOOL_HEAD   *Head;
	Head = BASE_CR(Ptr, CPOOL_HEAD, Data);   // NULL - 0x20，不是 NULL
	assert(Head != NULL);                    // 恒为真，拦不住
	DEBUG((DEBUG_POOL, "free(%p): Head: %p\n", Ptr, Head));
	if(Ptr != NULL) {
		if (Head->Signature == CPOOL_HEAD_SIGNATURE) { ... }
```

NULL 判断放在指针运算**之后**，编译器把 `Head->Signature` 的读取提前了，于是 `free(NULL)` 直接读 `0xFFFFFFFFFFFFFFF0`。

### 修复（已提交）

- `scripts/ci/patch_simpleinit_free.py`：在 `free()` 里 `BASE_CR` 之前插入 `if (Ptr == NULL) return;`，幂等、打印 diff、找不到就 fail。
- `scripts/ci/retarget_header.py`：把 CI 产出的镜像换成原厂 1632 字节头（`edk2-msm-overlay/boot-header-1632.bin`）。
- `.github/workflows/build-meizu15-fix.yml`：RELEASE + patch + 移植头部，一条龙。

另外 `build-meizu15-nosimpleinit` 的失败原因已定位并修掉：
runner 的 Clang 21 把 `-Wnull-dereference` 变成错误，EDK2 全程 `-Werror`，
第一个倒下的是 `QcomPkg/.../FdtParserLib.h:46`（`MemoryInitPeiLib`），与本移植无关。
`meizu15.dsc` 已加 `-Wno-null-dereference`。

## 2026-11-13 00:35 — Phase 3 完成：真机进 SimpleInit 菜单 + UEFI Shell

镜像：`boot-meizu15-stockhdr.img`（CI run 43257940331，patch 过 `free(NULL)`）
`fastboot boot` 后屏幕依次出现：

1. **SimpleInit 启动菜单**（带倒计时）；
2. 倒计时结束 → 落到 **UEFI Shell**：

```
VenHw(B615F1F5-5088-43CD-809C-A16E52487D00)/HD(50.GPT.A5D24706-15F6-1595-7DE3-AFC0CC1F3EB5,0x120000,0x20000)
BLK49: Alias(s):
... BLK50 / BLK51 / BLK52 / BLK53 / BLK54 / BLK55 ...
Press ESC in 1 seconds to skip startup.nsh or any other key to continue.
Shell>
```

`BLK49..BLK55` 就是本机的 50..57 号 GPT 分区（boot/recovery/system/.../userdata），说明：

| 环节 | 状态 |
|---|---|
| BootShim → PEI → DXE → BDS | ✅ |
| GOP + 1080×1920 面板 | ✅ |
| SimpleInit GUI + 启动菜单 | ✅ |
| eMMC（qcom,sdhci-msm-v5）驱动 | ✅ 分区枚举成功 |
| GPT 分区表解析 | ✅ |
| UEFI Shell | ✅ |

菜单里没有可启动项（预期，ESP 上还没有 UniXNU 载荷），所以倒计时后进 Shell。

**Phase 3 收官。** 下一步是 Phase 4：把 XNU kernel.macho + ADT + DarwinBoot 放进 userdata 的 FAT 卷，让菜单能列出并启动它。

## 2026-11-13 02:00 — Phase 4 调研：UniXNU 版本与 iOS 16 的错位

### 事实（全部来自 CI 日志与仓库）

| 事实 | 证据 |
|---|---|
| device repo 被 unixnu 接受 | `status --json` 全字段解析，`configured: true` |
| IPSW 身份正确 | `ipsw info` 列出 d21ap 全套组件 |
| kernelcache 可取可解 | 51,019,776 B，sha256 `ae36144e…` |
| **补丁解析为 0** | `release --check` → `check done: 0 patches, configuration None, OS out` |
| schema 1 不支持补丁 | `patches.plan()` 只在 `schema_version == 2` 时调用 |
| 最新 UniXNU 无 20H392 profile | `profiles/os/` 只有 ipados-17.7.11 / 26.5.2 / 27.0.1 / macos-27.3 |
| 2026-10-19 的 UniXNU 也没有 profiles/os | 该版本 ls 只到 `patches/`，且 xnu pin 仍是 d8b80295 |
| `profiles/soc/` 为空 | 没有 sdm660 profile |
| `profiles/arch/arm64/` 有 series | `patches/xnu/series` 存在 |
| lavender 的 lock 里 xnu = 1b191cb5 | 与 UniXNU 任何 feeds.toml 版本都不同 → 该 device repo 自己覆盖过 |

### 结论

要拿到可用的内核，必须**自己补一个 iOS 16.7.16 (20H392) 的 OS profile**，结构照
`profiles/os/ipados-17.7.11-21H461/profile.toml`，把其中的 ipsw / kernel.prelink /
darwin 换成我们实测的值：

```toml
["os"]
name       = "ios-16.7.16-20H392"
darwin     = "22.6.0"
xnu_version = "8796.…"          # 待确认（iOS 16 = xnu-8796 系列）
["ipsw"]
product    = "iPhone10,2"
device_class = "d21ap"
chip_id    = 32789               # 0x8015
board_id   = 4                   # 0x04, measured
["kernel"."prelink"]
kernelcache_sha256 = "ae36144edbd1aebce562b662ed0c19053c95026d0950eeab5c9e695fe72e2f71"
slide = "-0x1000000"
roots  = <31 项，与 board.toml 一致>
```

之后 board.toml 升到 `schema_version = 2`，用
`patch_series = ["arch:patches/xnu/series", "device:patches/xnu/series"]`（soc 层暂缺）。

### 未决

- iOS 16 对应的 XNU tag/commit（`xnu-8796.101.5`? `xnu-8796.141.3`?）—— 待查
  apple-oss-distributions/xnu 的 tag 与 UniXNU 历史里的对应关系。
- 该 XNU 版本能否套用 UniXNU 现有的 arch/device 补丁（版本跨度大，可能 patch 全部失败）。

## 2026-11-13 02:45 — schema 2 也不通：没有 sdm660 的 SoC 模块

顺着 schema 2 试下去，`unixnu defconfig` 一步步要东西：

```
schema_version = 2 requires [board] soc_source and os_profile
  -> 加了 soc_source = "sdm660"
feeds.toml needs a source with kind = "soc" and name = "sdm660"
```

而 UniXNU 组织里只有 `UniXNU-sm8550-common`（SM8550 / sheng）：

```
UniXNU-sdm660-common  404
UniXNU-sdm845-common  404
```

也就是说 **schema 2 的路线要求存在一个 SDM660 的 SoC 仓库**（configs + patches +
drivers），而 UniXNU 从来没有做过 SDM660 的 SoC 模块 —— 现有 SDM660 作品（lavender）
是 schema 1 时代的产物。

### 结论

| 路线 | 状态 |
|---|---|
| 最新 UniXNU + 我们的 board.toml | ❌ 没有 20H392 的 OS profile |
| 最新 UniXNU + 自造 OS profile | ❌ 还缺 SDM660 的 SoC 仓库（schema 2 硬性要求） |
| 2026-10 的 UniXNU（schema 1） | 可行方向：与 lavender 同代，xnu pin 可指向 8796.141.3 |

已把 iOS 16.7.16 的 OS profile 写好留在
`profiles/os/ios-16.7.16-20H392/profile.toml`（含实测 board_id 0x04、kernelcache
sha256、31 个 prelink roots、xnu_revision 1b191cb5 = tag xnu-8796.141.3），
board.toml 回滚到 schema 1（`board.v1.toml` 保留 schema-2 草稿）。

### 下一步

1. 用 2026-10 版 UniXNU（7b52e796）跑 `status` 和 `release --check`，看它的补丁机制
   （那版的 `patches/` 只有 `libpthread`，XNU 补丁可能由 `scripts/` 里的脚本应用）。
2. 按那版的约定把补丁挂上，xnu pin 覆盖成 1b191cb5。
3. 只有补丁能落地，macOS 编译才有意义。

## 2026-11-13 02:50 — 路线修正：照 SM8150/SM7150 的公开做法编 XNU 8796

用户提示看 HakiOS、红米 Note 7、红米 K20 Pro 三个项目。K20 Pro（raphael）的
`.github/workflows/build-raphael.yml` 给出了完整答案：

```
job kernel:  runs-on macos-14
  env: MACOS_VERSION=13.5  MACHINE_CONFIG=SWEET  KERNEL_CONFIG=DEVELOPMENT
  1. dipa-studio/ios-sweet              XNU 8796 构建脚本 + 平台补丁（36+ 个）
  2. blacktop/darwin-xnu-build @6639065  macOS 构建驱动
  3. 把 ios-sweet/xnu/patches/*.patch 覆盖进 dxb/patches/
  4. brew install jq gum bash cmake ninja; stub xcodes
  5. bash build.sh
  6. 从产物里挑 magic = cffaedfe 的 Mach-O 当 kernel.macho
```

关键点：**它们不经过 UniXNU 的 kernel 步骤**。raphael 的 board.toml 注释写得很直白：
"raphael reuses guacamole's exact kernel build → NO per-device kernel defconfig is required."

ios-sweet 的补丁集（节选）正好覆盖我们要的东西：

| 补丁 | 作用 |
|---|---|
| 0005 non-pac-build-fixes | 非 PAC 构建（ARMv8.0 设备需要） |
| 0006 open-source-asm-pmap | 开源 asm pmap |
| 0016 device-tree-platform-expert | 设备树平台专家 |
| 0018 boot-cpu-driver | 启动 CPU |
| 0019 builtin-pthread | 内置 pthread |
| 0020 portable-aes-cbc | 便携 AES（无 Apple 硬件） |
| 0026 builtin-hfs | HFS 支持 |
| 0028 ufs-root-device | **UFS 根设备**（我们是 eMMC，缺这个） |
| 0035 amfi-entitlements-platform-binaries | AMFI 放行平台二进制 |

### 对新路线的影响

- UniXNU 的 schema 2 / SoC 模块问题**绕过去了**：kernel 用 ios-sweet 编，
  固件继续用我们已跑通的 edk2-msm（Phase 3），payload 我们自己打包。
- 新增两个必须解决的差异：
  1. **ARMv8.0**（Kryo 260 无 PAN/LSE），SWEET 面向 ARMv8.2，需补 no-LSE/no-PAN；
  2. **eMMC**：ios-sweet 只有 UFS 根设备驱动，我们没有对应实现。

已加 `.github/workflows/build-meizu15-xnu-sweet.yml`（照 raphael 的 kernel job 写，
产物 kernel.macho 上传）。等 macOS runner 排到即可验证能否编出 XNU 8796。

## 2026-11-13 03:10 — 决定性发现：HakiOS 在 Linux 上编 XNU 8796，且用 BCM2837（ARMv8.0）

三个参考项目对比：

| 项目 | 内核怎么来 | 平台配置 | 宿主要求 |
|---|---|---|---|
| 红米 K20 Pro (raphael) | ios-sweet + darwin-xnu-build | `MACHINE_CONFIG=SWEET`（ARMv8.2） | macOS + Xcode 15 |
| 红米 Note 7 (lavender) | 未公开 kernel 步骤（board.toml 是 schema 1） | 未知 | 未知 |
| **HakiOS** | **自带 xnu-deps + xnu-build.sh** | **`MACHINE_CONFIGS=BCM2837`（Cortex-A53，ARMv8.0）** | **Linux 即可** |

HakiOS 的关键文件：

```
xnu-env.sh        XNU_SRC 默认 = xnu-src/xnu-8796.121.2   ← iOS 16！
                  THEOS = ~/theos（Linux toolchain + iPhoneOS16.5.SDK）
xnu-deps/setup.sh 拉 Apple 开源（AvailabilityVersions/libdispatch/libpthread）+
                  mig/unifdef/tcsh + MacOSX13.3.sdk 的 Kernel.framework + SDK 拼装
xnu-build.sh      make SDKROOT=iphoneos ARCH_CONFIGS=ARM64
                       MACHINE_CONFIGS=BCM2837 KERNEL_CONFIGS=DEVELOPMENT
                       BUILD_WERROR=0 BUILD_LTO=0 DO_CTFMERGE=0
                       CC=xnu-clang CXX=xnu-clang++
                  Linux 上用 xnu-deps/hostinc/hostcompat.h 兼容层编 SETUP 工具
```

**BCM2837 = 树莓派 3 = Cortex-A53 = ARMv8.0**，与 SDM660 的 Kryo 260（A73/A53）
同一代 ISA，没有 PAN/LSE —— 这正好解释了为什么 UniXNU 的 kernel 产物路径是
`DEVELOPMENT_ARM64_BCM2837`。

### 为什么这条路线能绕开前面所有阻塞

| 前面的阻塞 | 被绕开的方式 |
|---|---|
| UniXNU 没有 20H392 的 OS profile | 不用它的 kernel 步骤 |
| UniXNU schema 2 要 SDM660 SoC 仓库（不存在） | 同上 |
| macOS runner 三次 queued→cancelled | **HakiOS 在 Linux 上编** |
| ARMv8.0（无 PAN/LSE） | BCM2837 就是 ARMv8.0 配置 |

### 下一步

ubuntu runner：
1. `~/theos`（Linux toolchain + iPhoneOS16.5.sdk）
2. HakiOS 的 `xnu-deps/setup.sh`
3. `xnu-8796.121.2` + ios-sweet 的 84 个平台补丁
4. `xnu-build.sh`（MACHINE_CONFIGS=BCM2837）→ mach.development.bcm2837.unstripped

产物再交给我们的 payload 打包（kernel.macho + meizu15 ADT + ESP）与已跑通的 edk2-msm 固件。

## 2026-11-13 19:25 — 内核编出来了：XNU 8796 / BCM2837 / ARMv8.0

`build-meizu15-xnu-hakios-v2`（run 43403528891）**11 个步骤全部 success**：

```
1 Set up job                                    success
2 actions/checkout@v4                           success
3 Host dependencies                             success
4 Toolchain (UniXNU Linux setup)                success   ← clang+cctools+mig+iig 从源码编成
5 Point theos at that toolchain + 16.5 SDK      success   ← 软链伪造 theos，SDK 走 theos/sdks
6 Clone HakiOS                                  success
7 Apply the kernel patch (xnu/apply.sh)         success   ← hakios.patch 打到 xnu-8796.121.2
8 Kernel dependencies (xnu-deps/setup.sh)       success
9 Build the kernel                              success   ★
10 Collect                                      success
11 Upload                                       success
```

产物（日志原文）：

```
./xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped
./xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.dSYM
./xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.link
```

Artifact `meizu15-xnu-hakios-v2`：149,031,755 字节（含 dSYM），
artifact id `14027678719`。

### 这个组合是怎么成立的

| 部分 | 来源 | 关键点 |
|---|---|---|
| 工具链 | UniXNU `scripts/linux/setup-toolchain.py` | clang 带 `__builtin_xnu_type_*`、cctools-port `ld64-956.6`、`mig`、`iig`、`lzfse` → `/opt/unixnu-linux` |
| 源码 | apple-oss-distributions/xnu tag `xnu-8796.121.2` | iOS 16 代际（Darwin 22.x） |
| 补丁 | HakiOS `xnu/hakios.patch` | 它自己的 CI 用 `git apply --check` 保证对这个 tag 可应用 |
| SDK | `theos/sdks` 的 `iPhoneOS16.5.sdk`（sparse clone） | HakiOS 的 `xnu-deps/setup.sh` 要这个路径 |
| 平台配置 | HakiOS `xnu-build.sh` 的 `MACHINE_CONFIGS=BCM2837` | Cortex-A53 = ARMv8.0，与 Kryo 260 同代 ISA |

**theos 的 404 是这样绕过的**：`xnu-deps/setup.sh` 只检查
`$THEOS/toolchain/linux/iphone/bin/ld` 存在，所以把那个目录软链到
`/opt/unixnu-linux/CommandLineTools/usr/bin/*` 即可。

### 注意：`hakios-kernel` 没有生成

日志里 `=== artefacts ===` 之后是 `(no hakios-kernel)` —— 那是 `make kernel`
包装脚本的复制步骤（`tools/make-kernel.sh`），我们是直接调 `xnu-build.sh`，
所以只在 `BUILD/obj/` 下产出。不是失败。

## 2026-11-13 23:35 — 带 payload 的固件编出来了

run 43447057145（`build-meizu15-uefi-payload.yml`）13 个步骤全绿。产物：

```
boot-meizu15-payload.img   12,398,592 bytes (11.82 MiB)
  magic        ANDROID!
  kernel_size  12,393,165
  kernel_addr  0x8000     tags_addr 0x100     page_size 4096
  header_ver   0          os_version 0xe040924   ← 原厂值，未变
```

对比 Phase 3 的 6,230,016 字节：**多了 6.17 MB**，就是压缩后的 payload。

离线验证（`scripts/check_payload_fv.py`）：

```
kernel seg : 1 gzip member -> 33,554,576 bytes
outer FV   : 0x90  length 33,554,432  header 72
  8AF09F13-44C5-96EC-1437-DD899CB5EE5D  type 0x03        43,288 bytes   (SEC)
  9E21FD93-9C72-4C15-8C4B-E77F1DB2D792  type 0x0b    12,010,382 bytes   (DXE, LZMA)
inner DXE  : length 35,905,408 bytes
```

Phase 3 的内层 DXE 是 29,673,032 字节 → 现在 35,905,408，**差 6,232,376 字节**，
正是 payload。payload 确实被编进固件了。

### 这一路修掉的四个问题

| 现象 | 根因 | 修法 |
|---|---|---|
| workflow 名显示成文件名、job 不创建 | `$GITHUB` 的 YAML 解析器被混合 CRLF/LF 噎住 | 统一 LF |
| `- name: …        run: |` 同一行 | here-string 结尾没换行 | 拆行 |
| `error 000E: RamDiskLib.inf is not found` | edk2-msm 的 edk2 版本没有 RamDiskLib | 驱动自己实现 EFI_BLOCK_IO_PROTOCOL，不再依赖 MdeModulePkg 的 ramdisk |
| `llvm-ar: not found` | CLANG38 用 llvm-ar 链接，payload job 没装 llvm | apt clang lld llvm |
| `array type 'UINT8[2]' is not assignable` | `EFI_DEVICE_PATH_PROTOCOL.Length` 是两字节数组，`SIGNATURE_TYPE_NONE` 不存在 | `Length[0]/[1]` 赋值，SignatureType = 0 |
| `Disk full` | `mformat -T` 以 **512 字节**扇区计，我把 32 MiB 算成了 8 MiB | sectors = SIZE_MB × 2048，卷改 48 MiB |

### 下一步（需要手机）

```
fastboot boot boot-meizu15-payload.img
```

预期：SimpleInit 菜单里出现 `iOS 16.7.16 (Meizu 15)` 一项。
若出现，说明「固件 → payload 卷 → 菜单」这条链通了，剩下的是 DarwinBoot 与内核。

## 2026-11-14 02:00 — 完整 payload 链编出来了（run 43476581739 全绿）

两个 job 都成功，产物：

```
meizu15-uefi-payload-debug  11.62 MB
meizu15-uefi-payload        23.12 MB
boot-meizu15-payload-debug.img  12,472,320 bytes
```

离线拆包（scripts/check_payload_fv.py）：

```
kernel seg : 1 gzip member -> 33,554,576 bytes
outer FV   : 0x90  length 33,554,432  header 72
  8AF09F13-...  type 0x03        49,336 bytes   (SEC)
  9E21FD93-...  type 0x0b    12,083,085 bytes   (DXE, LZMA)
inner DXE  : 36,216,000 bytes
```

DXE 卷 29,673,032（Phase 3）→ 35,905,408（只有 payload）→ **36,216,000**（payload + DarwinBoot + Launcher）。最后这 310 KB 就是 loader 和启动器。

### 这一阶段的六个障碍

| 现象 | 根因 | 修法 |
|---|---|---|
| `error 4000: gFdtTableGuid is not found` | edk2 pin 的版本没有 `MdePkg/Include/Guid/Fdt.h` | 放弃 UniXNU 菜单应用（它依赖 FdtLib），自己写 403 行的 `Meizu15Launcher` |
| `'AsmMacroLib.h' file not found` | 同上，老 edk2 只有 `AsmMacroIoLib.h` | 在 `UniXNUPkg/Include/AsmMacroLib.h` 提供最小实现（Packages 的 Include 自动进搜索路径） |
| `build: command not found` | `edksetup.sh` 的 PATH 随脚本结束消失 | 改为让 `build.sh` 自己编（DSC 里加应用） |
| `no Meizu15Launcher.efi was produced` | `build.sh` 输出在 `workspace/Build/`，我搜的是 `Build/` | `find . -name` |
| `mmd` exit 1 | `/EFI` 已存在，mtools 拒绝 | 只建 `/EFI/BOOT` 并容忍已存在 |
| workflow 名显示成文件名 | YAML 里 CRLF/LF 混用 + here-string 尾部换行 | 统一 LF、拆行 |

### 现在的完整链条

```
boot.img (12.47 MB)
  └ gzip → FV 33.5 MB
      ├ SEC 49 KB（BootShim 之后的第一段）
      └ DXE 36.2 MB (LZMA)
          ├ Meizu15PayloadDxe   FV 里的 FAT → EFI_BLOCK_IO_PROTOCOL
          ├ EnglishDxe          gEfiUnicodeCollation2ProtocolGuid
          ├ DiskIoDxe / PartitionDxe / Fat
          ├ DarwinBoot          gUniXNULoaderProtocolGuid
          ├ Meizu15Launcher     找卷、读 ADT、调 loader
          └ FILE FREEFORM (压缩)
              └ 48 MiB FAT
                  ├ /EFI/UniXNU/unixnu.toml
                  ├ /EFI/UniXNU/Payloads/kernel.macho       15,791,328 B
                  ├ /EFI/UniXNU/Payloads/kernel.macho.adt       13,996 B
                  └ /EFI/BOOT/BOOTAA64.EFI                      20,480 B
```

### 待真机验证

`fastboot boot boot-meizu15-payload-debug.img`，预期屏幕：

```
Meizu15Launcher: looking for \EFI\UniXNU\Payloads\kernel.macho
Meizu15Launcher: payload volume found, ADT 13996 bytes
Meizu15Launcher: loader revision 0x20000, booting
```

然后 DarwinBoot 接手 kernel.macho。


## 2026-11-14 09:51 真机 `fastboot boot` 58ae92b 镜像

镜像：`boot-meizu15-payload-debug.img`，12,480,512 B，
SHA256 `599D892A640D7B65268DD43E60BC8AD5219486705906CB93E198111992B0063C`。
只 `fastboot boot`，boot 分区未动。

时间线（本地日志 `boot-run.log`）：

```
09:51:38  adb reboot bootloader
09:51:42  fastboot 就绪
09:53:36  Sending 'boot.img' (12188 KB) 完成，上传耗时 114 s ≈ 110 KB/s
t+15s     设备退出 fastboot —— BootShim 已执行，UEFI 接管
t+60s     又出现在 fastboot —— UEFI 复位，屏幕回到 bootloader
之后      保持 fastboot，adb 不出现
```

判读：镜像被 bootloader 接受并真的执行过，不是"没刷进去"，也不是上传失败。
UEFI 起来后自己复位，屏幕最终停在 fastboot 界面。

按可能性排序的可疑点：

1. SimpleInit 菜单倒计时到点后自动启动默认项，启动项（Shell 或 launcher）
   出错导致复位。`startup.nsh` 在 58ae92b 里改成 `fs0:\bootaa64.efi`，
   Shell 一旦自动执行它就直进 launcher。
2. prober 未修好时菜单项数仍是 1，默认项是 Shell，Shell 加载 launcher，
   launcher 在卷布局未验证的情况下把控制权交给 DarwinBoot 后复位。
3. USB 太慢（110 KB/s，像是 USB 2.0 口或线材），bootloader 侧等待超时。

本轮已做的工程改动：

- 新增 `scripts/ci/patch_simpleinit_prober.py`：修 `src/boot/prober.c` 的内层
  循环变量遮蔽（内层 `d` 改名 `d_inner`，连同内层循环体里的引用一起改）。
  用 fixture 验过两种形态：单行嵌套 `for(...) for(...){...}` 和多行带花括号
  嵌套；无关循环不动；二次运行幂等；括号数变化时会拒绝写盘。
- workflow 增加 `prober_fix` 开关（workflow_dispatch，默认关）。
- workflow 增加 `Assert the payload volume layout`：用 `mdir` 断言卷根必须有
  `bootaa64.efi`、`startup.nsh`，`/EFI/UniXNU` 下必须有 `kernel.macho`、
  `kernel.macho.adt`、`unixnu.toml`；缺一个就红，不再拿真机时间去发现。
- workflow 增加 `tiny_payload` 开关：`scripts/ci/make-fake-payload.py` 造
  8 KB 假 Mach-O（magic `cffaedfe`，ncmds=0），payload 卷缩到 8 MiB，
  镜像降到 MB 级，fastboot 上传从 114 s 降到秒级。DarwinBoot 会按预期拒绝
  这个假内核，正好用来只验证"菜单项数"和"launcher 是否被调用"。
- artifact 里增加 `esp-listing.txt`（mdir 全卷清单）和 `payload-fv.txt`
  （`check_payload_fv.py` 对成品镜像的拆包结果），以后不用真机也能核对布局。

仍未解决：缺"复位前最后一帧"。手机没有串口，屏幕是唯一信息源。
下一次 boot 时盯屏幕：出现 `Meizu15Launcher: ...` 说明 launcher 已被调用；
只有菜单没有 launcher 输出，说明还是 prober 的问题。

### 同一轮的后续改动（针对"UEFI 起来后自己复位"）

1. `startup.nsh` 不再自动执行启动器。原来它写的是 `fs0:\bootaa64.efi`：当
   prober 只找到 1 项（Shell）时，菜单倒计时选中的就是 Shell，Shell 一进来就
   执行 startup.nsh，直接跳进启动器 —— 屏幕因此永远看不到菜单本身。现在它只
   打印三行提示，把屏幕留在 Shell 提示符上。
2. `patch_simpleinit_prober.py` 改为**默认执行**，workflow_dispatch 的开关反转成
   `skip_prober_fix`（默认 false = 修）。prober 修好后 `/EFI/` 和 `/EFI/BOOT/`
   才真的被遍历，菜单里才会有 `UEFI OS` 这一项。
3. `Meizu15Launcher`：
   - 探测卷不再整读 15.8 MB 内核，改用 `GetInfo` 只取大小（`FileSize()`），
     每个卷只 `Open`+`GetInfo` 一次；
   - 屏幕多打一行 `kernel %u bytes, ADT %u bytes`；
   - 调用 `Loader->Boot()` 之前 `Stall(6s)`，让最后一帧留得住。
4. `DarwinBoot`：在 `inputs validated; exiting boot services` 之后 `Stall(8s)`。
   这一帧就是内核如果 fault 之后屏幕上的全部内容，也是判断"到底走到哪一步"的
   唯一凭据。

### 从 GitHub 侧核对到的事实

`build-meizu15-uefi-payload` #27（commit 58ae92b）两个 job 都 success：
firmware job 13 步、debug job 12 步；artifact 两个 ——
`meizu15-uefi-payload` 24,244,420 B（payload + 未 retarget 的 img）和
`meizu15-uefi-payload-debug` 12,192,103 B，后者解出来就是手上这份
12,480,512 B 的镜像。所以镜像本身没有问题，问题在它跑起来之后。

日志（`.../actions/runs/43483879019/logs`）需要 token，未认证是 403，
所以 #27 的逐步输出这次没拿到；等 .gh-token 就位可以只读拉下来存档。

### 还欠的一件事

firmware（RELEASE）job 的 payload 卷里没有启动器：它只跑 `build-esp.sh`，
没有像 debug job 那样把 `Meizu15Launcher.efi` 复制成 `/bootaa64.efi` 再打卷。
于是 RELEASE 镜像不可能进启动器，两条流水线的产出不等价。要等 debug 这条线
验证通过后，把 RELEASE job 也改成"两趟 + 打启动器 + 断言"。

### 2026-11-14 10:10 读过 FULL-CONTEXT.md 之后的修正

`FULL-CONTEXT.md`（10:00 版，对应 58ae92b）到了。两份交接包比对：Downloads 那份
比桌面那份只多这一个文件，artifacts 三个文件哈希完全一致
（`boot-meizu15-payload-debug.img` = `599D892A…063C`），没有新代码。

按全文第 8 章的铁律，回头改了四处，其中一处是**推翻我自己一小时前的改动**：

1. `startup.nsh` 恢复成自动执行启动器（撤回上一节的改动）。
   理由：菜单第一项是 `UEFI Shell on #61`，倒计时选中的就是它；Shell 执行
   startup.nsh 是**唯一不需要按键**就能到达启动器的路径。我先前把它掐掉，
   等于把自动链路砍断，还把观察点推给"得有人在场按键"。现在恢复，
   改由启动器自己留帧来保证屏幕可读。

2. `Meizu15Launcher` 入口 `Stall(10s)`。只要启动器被调入，屏幕就会停十秒，
   谁看都能看见 `Meizu15Launcher: looking for ...` 这一行 —— 这是"启动器有没有
   被调用"这个当前唯一未决问题的判据。

3. `DarwinBoot` 的交接摘要改为 `Print` 打到屏幕（原先只有 `DEBUG`）。
   铁律第 7 条写明：手机没有串口，`DEBUG()` 在这个平台上等于写进虚空，
   关键信息必须走 `ConOut`。现在 kernel/entry/args/video/entry words/SCTLR 都上屏，
   并在打印后 `Stall(4s)`。

4. 去掉两处 `continue-on-error: true`（RELEASE 与 DEBUG 两条构建）。
   铁律第 6 条：CI 产出要断言，不要用它掩盖失败。产物缺失本来就有单独断言。

### 文档与实现的一处不一致（记下来，别当成已知）

`FULL-CONTEXT.md` 4.3.2 的目录树写着 48 MiB FAT 里有 `/bootaa64.efi`、
`/startup.nsh`、`/EFI/UniXNU/*` —— 那只对 `debug` job 成立。`firmware`（RELEASE）
job 里只跑了 `build-esp.sh`，没有把 `Meizu15Launcher.efi` 复制成 `/bootaa64.efi`，
也没写 startup.nsh，所以 RELEASE 镜像不可能进启动器。等 debug 这条线验证通过后，
RELEASE job 要改成"两趟构建 + 打启动器 + 走同一套断言"。

### 2026-11-14 10:40 离线拆开成品镜像：卷内容已证实

payload 卷在 `SECTION COMPRESS` 里，`mdir` 和 `check_payload_fv.py` 都读不到目录。
用 UEFIExtract（LongSoft/UEFITool A76，win64 版）做 section 解压，其余全部用仓库
自己的 python 走完：`boot.img -> gzip -> FV -> FFS 3F1A9C22 -> COMPRESSED ->
48 MiB FAT16 -> 文件树`。新增 `scripts/ci/verify-payload-image.py`（串起来并断言）、
`scripts/ci/unpack-bootimg-fv.py`、`scripts/ci/fat_ls.py`，说明写在
`docs/verify-image.md`。

对 `artifacts/boot-meizu15-payload-debug.img`（58ae92b 产物）的实测：

```
image      : 12,480,512 bytes, 1 gzip member(s) -> FV 33,554,432 bytes
payload FAT: 50,331,648 bytes  sha256 9048a8856d126097...
volume     : FAT16, 512 B/sector, 2 sectors/cluster, 10 entries

  FILE      20,480  /bootaa64.efi
  FILE      20,480  /efi/boot/bootaa64.efi
  FILE   15,791,328  /efi/unixnu/payloads/kernel.macho
  FILE      13,996  /efi/unixnu/payloads/kernel.macho.adt
  FILE         273  /efi/unixnu/unixnu.toml
  FILE          19  /startup.nsh
```

三条硬结论：

1. `/bootaa64.efi` 确实在卷根，20,480 B，`MZ` 头，与 `/EFI/BOOT/BOOTAA64.EFI`
   逐字节相同（sha256 `f41f69e71b2b38d7...`）。58ae92b 的修法进了镜像。
2. `/startup.nsh` 只有 19 字节，内容 `fs0:\bootaa64.efi`（旧版是 28 字节的
   `fs0:\EFI\BOOT\BOOTAA64.EFI`）。**Shell 一旦被选中，就一定进启动器。**
3. 启动器里的 UTF-16 字符串确认它就是我们写的 Meizu15Launcher，找的路径是
   `\EFI\UniXNU\Payloads\kernel.macho`，并会打印
   `payload volume found, ADT %u bytes` / `loader revision 0x%x, booting`。

所以"镜像里有没有启动器""Shell 会不会进启动器"这两个问题**都不必再上手机验证**，
只剩一个未知：真机跑到哪一步复位。

CI 也加了同样的检查：debug job 的
`Verify the finished image from the outside` 现下载 Linux 版 UEFIExtract，
对成品 boot.img 跑同一个脚本，输出存成 `payload-image.txt`。
这与之前的 `Assert the payload volume layout`（检查打包中途的卷）互补：
后者防打包出错，前者证明交付的字节没错。

### 2026-11-14 11:05 推送成功、CI 全绿、tiny 镜像实测

有 token 之后这条链终于闭环了：commit -> push（走 127.0.0.1:7897）-> Actions ->
artifact -> 本地 verify -> fastboot boot。三次提交最后一次把 CI 也修绿了：

| run | sha | 结果 | 说明 |
|---|---|---|---|
| #28 | e4e275b | failure | 新增的 `Verify the finished image` 步骤自己挂了：`chmod /tmp/ue/UEFIExtract` 找不到文件 |
| #29 | 2848fbd | failure | 改成 find 之后仍然找不到：linux 包里的二进制是**小写** `uefiextract` |
| #30 | 2848fbd | failure | 同上（这次改动还没生效前先跑完的） |
| #31 | 97bf1d4 | **success** | `firmware`、`debug (full)`、`debug (tiny)` 三个 job 全绿，verify 步骤通过 |

#31 的 artifact：

```
meizu15-uefi-payload-debug-full  12,195,649 B   img 12,480,512 B
meizu15-uefi-payload-debug-tiny   6,015,043 B   img  6,311,936 B
meizu15-uefi-payload             24,241,649 B   （RELEASE 两个 img）
```

tiny 镜像的 payload 卷只有 8 MiB，CI 自己的 verify 输出（`payload-image.txt`）：

```
payload FAT: 8,388,608 bytes
  FILE      20,480  /bootaa64.efi
  FILE      20,480  /efi/boot/bootaa64.efi
  FILE       8,192  /efi/unixnu/payloads/kernel.macho      ← 假内核
  FILE      13,996  /efi/unixnu/payloads/kernel.macho.adt
  FILE          98  /startup.nsh
launcher: 20,480 bytes, MZ, sha256 1b4208185b15102e…
```

#### USB 链路是新的瓶颈

同一根线、同一个口，`fastboot boot` 的实测速率：

| 镜像 | 大小 | 结果 |
|---|---|---|
| full（58ae92b） | 12.5 MB | 成功过一次：114 s，110 KB/s |
| full（43CACEBF） | 12.5 MB | 失败：`usb_read failed`，77 s 中断；重试 272 s 再次失败（45 KB/s） |
| full（43CACEBF） | 12.5 MB | 失败：`usb_read failed`，271 s，45 KB/s |
| tiny | 6.3 MB | 传输完成：246 s，25 KB/s |
| tiny | 6.3 MB | 传输完成：412 s，15 KB/s |

#### 一次值得注意的观察（但被我自己搞脏了）

tiny 第一次传完之后：`t+3s` 设备退出 fastboot，之后 **120 秒既不在 fastboot 也不在 adb**。
对比 58ae92b 那次是 `t+15s` 离开、`t+60s` 就回到 fastboot（复位）。
"120 秒不回 fastboot"至少说明这一版**没有在 60 秒内复位**，这是 prober 补丁 + 留帧
那批改动之后第一次看到的行为差异。

但这个观察不可靠：我紧接着又发了一次 `fastboot boot`，而设备当时已经回到
fastboot，于是两次刷机的状态叠在一起，时间线没法再分开。教训：**一次只做一件事，
刷机期间不并发第二条 fastboot 命令。**

#### 当前设备状态

停在 bootloader 界面。这个 bootloader 不支持 `fastboot reboot`
（`FAILED (remote: 'unknown command')`），只能长按电源 12 秒回 Flyme。
