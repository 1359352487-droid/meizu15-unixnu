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

### 2026-11-14 12:00 fastboot 传输慢的根因：火绒的 USB 类过滤驱动

一整天 `fastboot boot` 都在 10–45 KB/s 甚至卡死，先后被归因为"线材"、"USB 口"、
"镜像太大"、"老 bootloader 本来就慢"。全部不对。真正的原因在 Windows 的设备树里：

```
HKLM\SYSTEM\CurrentControlSet\Control\Class\{36fc9e60-c465-11cf-8056-444553540000}
    UpperFilters = hrdevmon

hrdevmon   Running / Boot 启动
Huorong Internet Security Devmon Kext
system32\DRIVERS\hrdevmon.sys   (火绒安全 6.0.0.0)
```

`hrdevmon.sys` 是火绒的设备监控内核驱动，挂在 **USB 类**的 UpperFilters 上 ——
手机每一次 bulk 传输都要过它一遍。这解释了一整天的所有反常现象：

| 现象 | 解释 |
|---|---|
| MTP 传文件正常 | 走 `WUDFWpdMtp`（用户态驱动框架），不经过 WinUSB |
| `fastboot getvar` 0.29 s 正常 | 小包，拦截开销看不出来 |
| 大块传输 10–45 KB/s 或卡死 | 每个 bulk 包都被检查 |
| 换三个驱动包都慢 | oem4(libwdi) / oem16(android_winusb) / winusb.inf **全是 WinUSB 类**，同一个栈 |
| 换 USB 口无效 | 换的两个口都挂在同一个控制器上（Intel xHCI） |
| 昨晚另一台电脑 6 MB / 0.129 s = 48 MB/s | 那台没装火绒 |

**修复**（需要管理员，已执行，原值已备份到 `handoff/usb-class-backup.reg`）：

```
reg delete "HKLM\SYSTEM\CurrentControlSet\Control\Class\{36fc9e60-c465-11cf-8056-444553540000}" /v UpperFilters /f
```

**结果**（不重启，设备重新枚举后即生效）：

```
12:01:31 transfer start: 6,311,936 bytes
12:01:31 fastboot: Sending 'boot.img' (6164 KB)
12:01:31 elapsed 0.05s      <- 608 s 变成 0.05 s
```

6.3 MB / 0.05 s，等效 116 MB/s。**这一项修好了，以后不需要 tiny 镜像了，
12.5 MB 的完整镜像也是零点几秒的事。**

注意：`{4d36e967-…}`（存储卷类）的 UpperFilters 里还有 hrdevmon + partmgr，那一条
不影响 fastboot，暂时保留。

#### 遗留：设备当时停在了一个不认 `boot` 的 fastboot 状态

同一次调用返回 `FAILED (remote: 'unknown command')`，并且

```
getvar product / version / unlocked / is-userspace -> GetVar Variable not found 或 unknown command
getvar serialno -> 881QAESM95THY     （只有这个能答）
```

它认识 `getvar` 和 `serialno`，不认识 `boot`。而 11 点多的同一台设备
`getvar product` 还能返回 `M1881`、`fastboot boot` 还能正常 Sending 12 MB。
判断是 `adb reboot bootloader` 与 USB 重新枚举叠在一起，bootloader 状态机没走完。
处理：长按电源 12 秒回 Flyme，再 `adb reboot bootloader`，先确认 `getvar product`
返回 `M1881` 再执行 `fastboot boot`。

#### 教训（写给自己）

- 排查"设备能枚举、命令能通、大数据传不动"，**先查类级过滤驱动**（UpperFilters），
  不要从线材/端口/镜像大小开始猜。`pnputil /enum-drivers` 看不到过滤驱动，
  得读注册表或 `fltmc`/设备属性。
- 一次只做一件事：这一天有三次因为并发第二条 fastboot 命令、或者杀掉传输进程，
  把时间线搞脏，白丢了好几轮。

## 2026-11-15 15:35 — return stub 开始记录内核交还现场

上一轮（`147c822`，commit 说明是 "loader: turn the checkpoint off"）在真机上把 slot 的
word 2 设成 0，让 `LOADER_CHECKPOINT 1` 不匹配，结果是**顶部出现品红色带**。

按 `Entry.S` 里的约定，这条带子只有两种可能：

```
magenta   stub 被执行，但 x2 != 2  → 走的是 start.s 的 early-exception 向量（x2 = 1）
cyan      checkpoint 命中         → x2 == 2
```

所以内核不是「静默挂住」，而是**早期就异常了**，并且在异常向量里跳进了我们的 stub。
这比前几十轮任何一次都更具体：内核确实跑到了 start.s 之后，只是没走到能打印的地方。

### 被丢掉的那五个寄存器

`start.s` 的 `Lloader_early_exception` 在跳转前这样设置参数：

```
x0 = loader_log 物理地址    x1 = 日志长度
x2 = 1                     (early exception)
x3 = ESR_EL1               x4 = ELR_EL1      x5 = FAR_EL1
x6 = LR                    x7 = SP
```

也就是说，**上一次启动时屏幕上那条品红带背后，本来就带着「哪条指令崩了、碰了哪个地址」**，
而上一版 stub 只存了 x0/x1/x2，把 x3..x7 直接扔了。

### 本轮改动

| 文件 | 改动 |
|---|---|
| `DarwinBootPkg/Driver/Entry.S` | stub 在写 reason 之后存 x3..x7 到 `DarwinSavedContext +176/+184/+192/+200/+208`；`DarwinSavedContext` 数据段从 20 个 `.quad` 补到 27 个（216 字节） |
| `DarwinBootPkg/Driver/DarwinBoot.c` | 结构体同步 5 个字段（`FaultX3..FaultX7`）；`DarwinShowKernelLog` 在 status 行下面多画 3 行 |

顺带修掉一个一直存在的越界：`DarwinSavedContext` 的汇编定义只有 20 个字（160 字节），
而 stub 一直在写 `+160`（Returned）和 `+168`（Reason），也就是写进了后面的 `.text`。
MMU 关掉的时候不报错，但那是运气，不是布局。

### 屏幕判据（这一轮只有一台真机，必须先定好怎么读）

```
kernel returned: reason 1, log N bytes        ← 内核交还了，reason 1 = early exception
x3 0000000096000006 x4 fffffff007...          ← ESR, ELR
x5 ... x6 ... x7 ...
slot magic 545352444c554e58 ...               ← 槽位四个字，magic 证明写对了地方
```

判读表：

| 现象 | 结论 |
|---|---|
| `reason 1`，x3 的 EC（bit31:26）= `0x25` | 同 EL 数据中止：ELR 就是那条访存指令，FAR 是被访地址 |
| EC = `0x21` | 取指中止：ELR 指到一条取不出来的指令（多半是页表/映射问题） |
| EC = `0x00` | 未定义指令：多半是 ISA 不支持（PAN/LSE 一类）或跳飞了 |
| x4 在 `0xFFFFFFF007xxxxxx` | 内核虚拟地址，直接对 `kernel.macho` 反汇编 |
| x4 在 `0x00xxxxxx` / `0x8xxxxxxx` | 物理地址，说明崩在 MMU 打开之前 |
| 顶部没有品红、也没有任何文字 | stub 没被调用：连异常向量都没进去，得从 PrePi/DXE 侧查 |

拿到 x4 之后定位指令的方法（`__TEXT` vmaddr = `0xfffffff007004000`）：

```
off = (x4 - 0xfffffff007004000) + __TEXT.fileoff
scripts/ci/find_spin.py _ci/kernel.macho <x4>     # 也用它确认不是 b . 自旋
```

### 下一步

1. 推这两处改动 → CI 出新的 `boot-meizu15-payload-debug.img`。
2. `fastboot boot` 后拍**第一屏整屏**（不只是顶部带子），读 `x3 / x4 / x5`。
3. x4 落到哪，就去 `kernel.macho` 里看那条指令是什么——这一步之后才有得修，之前都是猜。

## 2026-11-15 15:46 — 新镜像上机：故障现场已上报（等拍照）

`b343381` / `95759b6` 这一版把 x3..x7 存了下来。CI 三次全绿：

```
run 43690721273  build-meizu15-fix           success
run 43690721205  build-meizu15-edk2msm       success
run 43690721271  build-meizu15-uefi-payload  success
```

产物 `meizu15-uefi-payload-debug-full`（5,276,597 B，artifact 14152009508），
里面的 `boot-meizu15-payload-debug.img` = 5,574,656 B，sha256
`A35E9E2AD8E84EBEB08DA5A1DAC5BBB154EF23978DED9B031E1DBBDFD671A7D5`。

### 离线先验：镜像里到底有没有这段代码

`scripts/ci/check_image_content.py` 先在这一版上跑过，因为上一轮已经踩过一次
「以为改了、其实没进镜像」的坑。中途还修了脚本自身的一个错：新加的三行是
`AsciiSPrint` 的 ASCII 格式串，我一开始按 UTF-16 去找，对着正确的镜像报
`absent`——差点又白刷一次。现在是分两组检查：

```
DarwinBoot, UTF-16LE Print strings
  return stub at                         FOUND
  no loader_stub_slot in this kernel     FOUND
  no room for the return stub            FOUND

DarwinBoot, ASCII AsciiSPrint format strings
  kernel returned: reason %lu, log %lu bytes  FOUND
  x3 %016lx x4 %016lx                    FOUND      ← 新增，旧版 absent
  x5 %016lx x6 %016lx                    FOUND      ← 新增，旧版 absent
  x7 %016lx                              FOUND      ← 新增，旧版 absent
```

对照旧镜像 `boot-147c822.img` 跑同一脚本，后三条全是 `absent`。**判据成立。**

### 上机

```
adb reboot bootloader                        → 18 s 后 fastboot 出现
fastboot getvar product                      → product: M1881        （状态机正常）
fastboot boot boot-meizu15-payload-debug.img
  Sending 'boot.img' (5444 KB)  OKAY [ 0.126s]
  Booting                       OKAY [ 0.016s]
  约 42 MB/s，5.4 MB 用时 0.126 s
```

刷入后 22 s：`adb devices` 空、`fastboot devices` 空、Windows PnP 里
`VID_05C6|VID_18D1|VID_2A70` 一个都没有 —— 设备停在固件里，与前几十轮一致。

### 顺便确认掉的旧问题

上一轮记的「fastboot 只剩 15 KB/s、`getvar product` 返回 unknown command」这次
**没有复现**：`getvar product` 正常、`Sending` 0.126 s。那一轮是 USB 重新枚举和
adb 状态机叠在一起，不是链路劣化。

### 现在的状态：只差一张照片

代码、镜像、工具三样都到位了，剩下的一步必须用相机，因为固件里没有 adb：

```
kernel returned: reason 1, log N bytes     ← reason 1 = 早期异常向量
x3 0000....96000006 x4 fffffff007......    ← ESR_EL1, ELR_EL1
x5 ........ x6 ........ x7 ........
slot magic 545352444c554e58 .......
```

拿到 x4（ELR）就能直接定位到指令：

```
python scripts/ci/locate_fault.py inputs/kernel.macho <x4>
```

这个脚本本轮已经用已知值验证过：喂 `0xfffffff0074614a8`（`Entry.S` 注释里记的
内核入口），它解出 `__TEXT_EXEC` 文件偏移 `0x45d4a8`、符号
`_LowResetVectorEnd+0x0`、指令 `14000aed b 0xfffffff00746405c` —— 和注释里记的
`0x14000AED`、`B over 0x2BB4` 完全对上。

也就是说：**给我 x4，我就能说出内核死在哪条指令上、碰的是哪个地址。**
在此之前不再改内核，因为没有新证据的改动都是在赌。

## 2026-11-15 16:36 — 粉带原来有两个来源，我的判读错了第二次

`3a051b4` 上机后的照片（`MVIMG_20261115_160831.jpg`）：**粉带还在，文字还是没有。**

### 错在哪

上一节我认定「粉带 = stub 画的」，据此得出「stub 一直在跑」，并归因于 `ctx` 越界。
但改完 `StubSpan` 后现象**完全没变**，说明这条因果链至少错了一环。

回头量一下 `DarwinTrap` 原来的代码：

```asm
8:
  mov x5, x2                  // 帧缓冲基址
  mov x6, #0x10000            // 64 KiB
3:
  stp w4, w4, [x5], #8
```

而 stub 里：

```asm
  mov x13, x10
  mov x14, #0x10000
3:
  stp w12, w12, [x13], #8
```

**两个处理器画的是同一块区域：基址起 `0x10000` 字节，约 15 行。**
一个是「内核交还了」（品红），一个是「内核早期异常」（品红=取指中止 / 青=数据中止 / 黄=其它）。
照片里那一条 15 行的粉色横条，这两件事都能画出来。**这是设计错误，不是读数错误。**

我在同一个像素图案上做了两次相反的结论，两次都没有证据支持。

### 修法：不再让两者共用同一种表达

`DarwinTrap` 现在完全不画带子，改成在屏幕下方画**位图条码**，把三个寄存器按位画成方块：

```
y = 1400   ESR_EL1   32 bit
y = 1460   ELR_EL1   64 bit
y = 1520   FAR_EL1   64 bit
y = 1580   标记 0xA5  8 bit   ← 只要这段跑到就一定出现，用来区分「画了」和「没画」
```

每 bit 宽 8 像素、高 20 像素，白 = 1，黑（未触碰的背景）= 0，
每 bit 后面留 1 像素空隙，相邻的 1 才数得清；每行最左侧 4 像素宽的短竖杠标起点。

这样屏幕上立刻能分出三种情况：

| 现象 | 结论 |
|---|---|
| 顶部细粉带 + 下方四行条码 | 内核异常了，且 `DarwinTrap` 拿到了现场 —— 条码里就是答案 |
| 顶部细粉带 + 下方无条码 | 粉带来自 **stub**（内核交还），`DarwinTrap` 没被调用过 |
| 无粉带 + 有条码 | 异常发生但 VBAR 指向我们的表，stub 未被使用 |

### 另一个必须修的：stride

`DarwinTrap` 要按行寻址，必须有 row stride。原先我打算写死 4320（1080×4），
但那是把面板尺寸焊进异常处理器。改成从 C 侧写：

```c
extern UINT64 DarwinTrapStride;
DarwinTrapStride = Video[2];        // 调用 DarwinEnter 之前
```

并把 `DarwinTrapFramebuffer` / `DarwinTrapStride` 一起从 `.text` 挪到 `.data`：
`.text` 在 MMU 开着时是只读的，C 侧那一句写进去会触发权限异常，
而且正好发生在最没法报告错误的地方。

### CI

`f8ce6ac`，三个 job 全绿（`43695760734` / `43695760802` / `43695760743`），
说明改过的汇编能过 `llvm-mc`，`adr` 跨到 `.data` 也在 ±1 MB 内。

镜像 sha256 `05BE18EBDC0A6F7AEE7F8AF495B36D575589FFC537F61887816FF5EC7719ACE9`。

### 教训

同一块屏幕像素被两条代码路径共用，是这次连续两次误判的根因。
**能区分两种状态的观察手段，才是观察手段。** 三个寄存器按位画成条码不算优雅，
但它不会和别的东西长得一样，这就够了。

## 2026-11-15 22:00 — 条码解出来了：内核没有崩溃，是正常返回

`5907702` 上机，屏幕底部五组条码清晰可读。用像素分析逐位解码（不是肉眼看）：

```
reason   0000000000000000    0x0000
ESR      00000000000000000000000000000000    0x00000000
ELR      0000000000000000000000000010001010100101000110100010010100011010
                             0x00000022A51A251A
FAR      0000000000000000000000000000000011010001001100000100000000000000
                             0x00000000D1304000
marker   00101101    读到 0x2D，代码写的是 0x5A
```

### marker 对不上，但不能因此否定结论

`0x5A >> 1 = 0x2D`，说明我的采样整体偏了一位。这是**几何问题**，不是数据问题：
校准横杠 400 fb px 在照片里是 454 px，比例 1.135，每 bit 10.2 px，
而方块边缘有 1 px 的压缩模糊，起点判断容易偏半个 block。

几何反推（量每段白色方块的起止再除以 10.28 px/bit）得到 `置1的位 = {2,4,5,7}`，
也就是 `0b00101101 = 0x2D`，与逐点采样完全一致 —— **解码方法本身没错**。

关键是：**`reason` 和 `ESR` 两行是完全的零，没有任何一位被置 1。**
采样偏移一位不可能把 16 位、32 位全部变成 0。所以：

```
x2 = 0
x3 = 0
```

### 这个组合的含义

对照 `hakios` 补丁里 `start.s` 的约定：

| x2 | 含义 | x3 |
|---|---|---|
| 1 | early exception（早期异常） | ESR_EL1 |
| 2 | checkpoint 命中 | checkpoint 编号 |
| **0** | **`loader_return()` 正常返回** | 返回码 |

**`x2 = 0` 且 `x3 = 0`，走的是 `loader_return.c` 的正常返回路径。**

这也解释了之前所有对不上的现象：

- **没有异常** —— `ESR = 0`，内核没死
- **但屏幕有品红带** —— stub 确实被执行了
- **C 侧 `DarwinShowKernelLog` 什么都没画** —— 因为恢复路径出了问题

### 我一直假设错的地方

前几轮我把 `x3/x4/x5` 一律当成 `ESR/ELR/FAR` 解读，注释里也这么写：

> The early-exception vector in start.s sets them like this before branching:
> x3 = ESR_EL1, x4 = ELR_EL1, x5 = FAR_EL1 ... with x2 = 1

**这套语义只在 `x2 = 1` 时成立。** 现在是 `x2 = 0`，走的是另一个函数
（`loader_return.c`），那几个寄存器在那里装的是别的东西 —— 所以
`ELR = 0x22A51A251A` 这种不像地址的值完全正常，它不是 ELR。

我拿错误的语义读了正确的数据，得出的「内核异常了」这个结论是错的。

### 那么真正要修的是什么

内核跑到了 `loader_return()` 并正常返回，`reason = 0`、`code = 0`。
stub 收到之后要做的是：**恢复 `DarwinSavedContext` 里的现场，`ret` 回 C，
让 `DarwinShowKernelLog` 把内核日志画出来。**

它没画。所以失败点在恢复这一段，而不是在内核。

排查方向已经很窄了：

1. `ctx` 指针是否指向正确的 `DarwinSavedContext`
2. `SCTLR/TTBR/TCR/MAIR` 恢复顺序是否有问题
3. `x19~x29` 恢复后 C 代码是否还能正常跑
4. `DarwinShowKernelLog` 里 `Returned` 标志是否真的被写进去了

下一步的改动应该是**让 stub 在恢复的每一步都留痕**，而不是继续猜。
例如在恢复前把 `ctx` 的值本身画成条码 —— 这样就能直接验证第 1 条。

### 教训

同一个寄存器集合在不同 `x2` 下有不同语义，而我在注释里只写了其中一种，
后面所有解读都建立在这份不完整的注释上。
**寄存器语义必须跟着 reason 分支写清楚，不能只记一条路径。**

## 2026-11-15 22:32 — 两侧数据对上了：stub 有日志，C 读不到

`9b124b8` 上机。第一行终于把两种状态分开了。

### C 侧（顶部那行无条件打印）

```
ret 0 reason 0 log 0+0 ctx CB045088
```

`Returned = 0`，`LogAddress = 0`，`LogLength = 0`。

### stub 侧（底部条码，逐位解码）

```
x0   (日志地址) = 0x00000000D0BB89C0
x1   (日志长度) = 0x00000000000000C0   = 192 字节
reason          = 0
code            = 0
marker          = 0x2D   (代码画的是 0x5A)
```

`marker` 又一次稳定地读成 `0x2D`，也就是 `0x5A` 右移一位 —— 采样偏了半个块。
但 **`x1 = 0xC0` 是可采信的**：它的非零位少、位置靠右，偏移不会改变读数。

### 这个对比意味着什么

| | stub 侧 | C 侧 |
|---|---|---|
| 日志地址 | `0xD0BB89C0` | `0` |
| 日志长度 | `192` 字节 | `0` |
| Returned | — | `0` |

**stub 确实从内核拿到了 192 字节的日志，但 C 读到的是全零。**

所以 `loader_return_after=15` 这条链是通的：内核跑了 15 秒，`loader_return()`
把日志指针和长度交给了 stub。**剩下唯一的问题是 stub 写进去的地方和 C 读的地方不是同一处。**

### ctx 地址自相矛盾

C 侧打印 `ctx = 0x0CB045088`。

拿它和同一张图里的其他地址比：

```
Loading driver at 0x000CB04Bxxx   (DarwinBoot)
SetUefiImageMemoryAttributes - 0x00000000CB073000 - ...
SetUefiImageMemoryAttributes - 0x00000000CB079000 - ...
```

`.data` 按 PE 段表在 RVA `0x27000`，加载后应当是 `ImageBase + 0x27000`，
即 `0xCB072000` 一带。但打印出来的是 `0xCB045088` —— **比 ImageBase 还小 0x5F78**。

离线验证过（`scripts/ci/inspect_efi.py`）：

```
PE at 0x3ac  ImageBase 0x0  Entry 0x1000
  .text   0x00001000 0x00026000
  .data   0x00027000 0x0001e000
  .reloc  0x00045000 0x00001000
```

`.data` 在 RVA `0x27000`。`0xCB045088` 落在镜像之外，**它不是
`DarwinSavedContext` 的地址**。

### 结论

`&DarwinSavedContext` 在运行时求值给出了一个不在本镜像内的地址。
stub 往 `0xCB045088 + 160` 写 `Returned = 1`，写到的是一块没人读的内存；
C 侧读自己的 `DarwinSavedContext.Returned`，永远是 0。

这就是「stub 有日志、C 读不到」的全部原因，也解释了更早那几轮
「有粉带、没有字」—— 恢复路径里 `ldp x10, x11, [x9, #16]` 从错误地址读
SCTLR/TTBR，写回后系统状态被破坏，`ret` 回去自然什么都画不出来。

### 下一步

不让 C 侧用 `&` 取这个符号的地址。改成：

1. C 侧在调用 `DarwinEnter` 之前，把 `DarwinSavedContext` 的地址**用别的方式**
   算出来（例如通过 `gBS->ImageBase` 或 GetMemoryMap 校验），或者
2. 让 stub 自己 `adr` 得到地址后，把它画成条码，与 C 打印的值并列比较一次。

第 2 条只花一次启动就能一锤定音，代价最小，先做它。

## 2026-11-16 00:07 — 通了：内核日志第一次出现在屏幕上

`21d578d` 上机。**屏幕顶部出现内核自己的输出。**

```
kernel returned: reason 0, log 192 bytes
x3 0000000000000000  x4 0000000000000000
x5 0000000000000000  x6 0000000000000000
x7 0000000000000000
slot magic 545352444C554E58  stub 00000000D1304000
checkpoint 0  delta 000000010C900000

--- kernel message buffer ---
[  65.892252]: vm_cache_geometry_colors: 16
[  65.892264]: do_cacheid() - 65536 bytes Unknown cache (I: 32768 D:32768 (separate)),
              16-way assoc, 64 bytes/line
```

**`log 192 bytes` + 实际的日志内容**，两件事同时成立。这是这一个多月里第一次
读到内核自己说的话。

### 真正的 bug：在 MMU 关闭的状态下写虚拟地址

条码把这件事钉死了。用几何方法（量每段白块的 framebuffer 宽度 ÷ 9）解码 ctx 行：

```
条码重建:  0x00000000CB045088
C 侧打印:  0x00000000CB045088
```

**两者完全一致。** 参数块传递是对的，stub 拿到的地址是对的。

那为什么 `Returned` 一直是 0？因为**写的时候 MMU 是关的**：

```
stub（MMU 关）str [x9+160]  → 写物理 0xCB045118
C 侧（MMU 开）读虚拟 0xCB045088 + 160
```

这是两块不同的内存。`1` 被写进了一个没人读的物理页上，
`DarwinShowKernelLog` 看到 `Returned == 0` 直接 return，
于是屏幕上永远只有带子、没有字。

修法是把顺序改成「先恢复 MMU，再写 ctx」：

```
1. MMU 关：从 ctx 读 SCTLR/TTBR/TCR/MAIR
2. 打开 MMU
3. 用同一个 x9 写 ctx      ← 此时虚拟寻址，和 C 读到的是同一块
4. 恢复 x19~x29 → ret
```

### 这一路真正花时间的地方

不是内核，是**观察通道本身**。回头看，同一个症状反复出现，每次都以为是新问题：

| 现象 | 真正原因 | 看起来像 |
|---|---|---|
| 有粉带、无文字 | stub 写 ctx 时 MMU 是关的 | 内核崩了 |
| 有粉带、无条码 | 写 ctx 先于绘制，写崩了 | stub 没被调用 |
| 有条码、读出来高位错 | 采样落在块边缘 | 内存被覆盖 |
| 条码画不出来 | `adr x9` 抢了 framebuffer 基址 | 绘制代码没跑 |

**每一个都是「我的诊断工具自己坏了，而坏法恰好和被测对象的故障一模一样」。**
四次里有四次，都是先相信工具、后怀疑工具，于是多花了整整几轮。

### 现在的位置

内核跑起来了，`reason 0`（正常返回），日志能读了。`loader_return_after=15`
让它在 15 秒后交还控制权；日志时间戳 `65.89` 说明它已经跑到 65 秒，
远超那个阈值——所以这个通道现在是稳定的。

`x3..x7 全零` 也印证了：走的不是异常路径，是正常的 `loader_return`。

### 下一步

1. 去掉 `loader_return_after=15`，让内核继续往下跑，看它能不能自己完成启动
2. 日志只有 192 字节且只有最后两行，说明 `loader_log` 缓冲区太小或被截断，
   把缓冲区加大才能看到启动全过程

## 2026-11-16 00:14 — 读到内核日志之后：它卡在 cache 探测

拿到日志是第一件事，读懂它是第二件。

### 那两行日志的含义

```
[  65.892252]: vm_cache_geometry_colors: 16
[  65.892264]: do_cacheid() - 65536 bytes Unknown cache (I: 32768 D:32768 (separate)),
              16-way assoc, 64 bytes/line
```

`do_cacheid()` 是 XNU **极早期**的 CPU 缓存探测，在 `arm_init` 里跑，
比 pmap、比线程、比任何设备都早。也就是说内核刚过 `b 0x2BB4`
（`_start` 那条跳转）之后不远就停在这里了。

时间戳 65.89 秒证明它没死——计时器还在走，只是不再往下推进。

### 「Unknown cache」是什么

对照三份数据：

| | L1 | L1 相联度 | L2 |
|---|---|---|---|
| XNU 假设（BCM2837） | 32K I + 32K D | 2-way | 512K, 16-way |
| SDM660 小核（A53） | 32K I + 32K D | **2-way** | 128K, 16-way |
| SDM660 大核（A73） | **64K** I + 64K D | 4-way | 512K, 8-way |
| **日志实测** | 32K I + 32K D | **16-way** | — |

XNU 读出来的是「32K + 32K、**16-way**、64 字节行」。A53 的 L1 是 2-way，
只有 L2 才是 16-way。**这个组合在真实硅片上不存在**，所以
`vm_cache_geometry_colors` 找不到匹配项，打了一句 `Unknown cache`。

这说明 `CTR_EL0` / `CCSIDR_EL1` 的读法或解释与 BCM2837 的假设不符。
在别的移植里，这一步要么按实测值建几何，要么直接改
`pe_arm_get_cpu_cache_info` 让 `arm_init` 接受未知几何。

### 顺带确认：日志没被截断

`loader_return.c` 里 `LOADER_LOG_SIZE` 是 1536 KB，head 区 256 KB。
而实测 `log 192 bytes`——**不是缓冲区不够，是内核只写了这么多**。

屏幕也没截断：`DiagDrawRaw` 从 Row 6 起、每行 42 像素，1920 高的屏能放约 39 行，
192 字节大约 4 行。所以屏幕上看到的就是全部。

### 这一版改了什么

`Meizu15Launcher.c` 的启动参数从 `loader_return_after=15` 改成 `60`。

理由是问题变了：
- 之前要证明「通道能用」，15 秒够了
- 现在要回答「内核自己不被打断时能走多远」，需要长窗口

60 秒的判据：

| 结果 | 含义 |
|---|---|
| 返回了，日志变长 | 内核继续跑了，往前走了一段 |
| 返回了，日志一样 | 走到同一处然后等待，没进展 |
| **完全不返回** | 死在某条没有 panic 的路径上，屏幕只剩色带 |

第三种本身就是答案——说明它不再走 `loader_return`，那才是真正需要看的地方。

## 2026-11-16 01:02 — checkpoint 参数化成功，但撞上 loader_jump 的 t0sz 门槛

### 这一轮的两个确认

**1. checkpoint 参数化有效**

```
DARWINBOOT: checkpoint requested 10
DARWINBOOT: slot magic 0x545352444C554E58 stub 0x0D130000 checkpoint 10 delta 0x10C900000
```

`slot[2]` 真的写进去了 10。之前硬编码 `Slot[2] = 0` 让 23 个检测点全部哑火，
这个坑填上了。

**2. 三带齐全，但内核没有返回**

照片里绿、红、蓝三带都在（蓝带在 y≈1440–1510，一开始被我先看成只有两带）。
三带齐 = 内核入口正确、`br x15` 执行了。

而 `checkpoint=10` 下**没有 `reason 2`，也没有内核日志**。对比
`checkpoint=0` 那次（日志停在 `do_cacheid`），这次连 `do_cacheid` 都没出现。

### 差别在哪：位置

`arm_init` 的结构（hakios 补丁）：

```c
arm_init(boot_args *args)
{
    const_boot_args = *args;
    BootArgs = args = &const_boot_args;

    loader_checkpoint(10);        // ← 在这里
    cpu_data_init(&BootCpuData);  //   而 do_cacheid() 在它里面
    ...
}
```

`checkpoint=0` 时：10 不匹配 → 继续 → 进 `cpu_data_init` → `do_cacheid` 打印两行 → 停住。

`checkpoint=10` 时：10 匹配 → 进 `loader_jump(2, 10)` → **然后没有下文**。

### 根因：loader_jump 直接拒绝

`loader_return.c` 里：

```c
static void
loader_jump(uint64_t reason, uint64_t code)
{
    uint64_t stub = loader_stub_slot[1];
    ...
    tcr  = __builtin_arm_rsr64("TCR_EL1");
    t0sz = tcr & 0x3f;
    /* The TTBR0 walk must start at L1 (1 GB per entry) and reach the stub. */
    if (t0sz < 25 || t0sz > 33 || (stub >> (64 - t0sz)) != 0) {
        return;
    }
    loader_return_entered = true;
    ...
}
```

面板打印过：

```
CurrentEL 1  SCTLR_EL1 0xC0180D  TCR_EL1 0x280803518
```

`t0sz = 0x280803518 & 0x3F = 0x18 = 24`。

**24 < 25 → 直接 return。** checkpoint 在 `arm_init` 最开头触发，那时
XNU 还没重建 `TCR_EL1`（`pmap_bootstrap` 之后才会设成 25），所以这条路上
`loader_jump` 一定会拒绝。

它也解释了为什么 `checkpoint=10` 之后内核没有继续跑到 `loader_return`：
`loader_jump` 被调用过一次（虽然拒绝了），但更重要的是**它没有把控制权
真正交出去**，而内核在这之后走到了 `do_cacheid` 附近的同一处停滞点——
只是这次连那两行日志都没来得及写。

### 结论

**`arm_init` 早期的 checkpoint 在 t0sz=24 下无法工作。** 要定位停点，只有两条路：

1. 用 `TCR` 重建**之后**的 checkpoint 编号（需要知道哪些编号在 `pmap_bootstrap` 之后）
2. 放宽 `loader_jump` 的 t0sz 下限（改 HakiOS 上游代码，要重新编内核）

第 1 条更省事，但需要先确认编号分布。第 2 条更彻底。

### 下一步

先回到 `checkpoint=0` 确认基线没被这轮改动破坏，然后再决定走哪条路。

## 2026-11-16 01:16 — checkpoint 17 也没命中：内核没走到 arm_vm_init

`e7d6522` 上机，`checkpoint=17`。结果：

```
kernel returned: reason 0, log 192 bytes
checkpoint 17 delta 00000010C900000
--- kernel message buffer ---
[  65.912846]: vm_cache_geometry_colors: 16
[  65.912859]: do_cacheid() - 65536 bytes Unknown cache (I: 32768 D:32768 ...)
```

**`reason 0`，不是 `reason 2`。** checkpoint 17 没有命中。

### 这说明了什么

`reason` 的两个来源很清楚：

| reason | 来源 | 代码 |
|---|---|---|
| 2 | checkpoint 命中 | `loader_checkpoint()` → `loader_jump(2, n)` |
| 0 | 正常返回路径 | `loader_return()` 末尾 → `loader_jump(0, 0)` |

拿到 0 就意味着：内核走了 `loader_return()`，但**没有经过 checkpoint 17**。
而 17 就在 `arm_vm_init()` 后面第一个语句。

**所以内核没有跑完 `arm_vm_init()`。** 它的活动范围被压缩到：

```
arm_init 开始
  → loader_checkpoint(10)       t0sz=24 被拒
  → cpu_data_init()
      → do_cacheid()            ← 日志停在这里
  → ...                          ← 死在这段
  → arm_vm_init()                ← 从未到达
  → loader_checkpoint(17)        ← 从未执行
```

### 三个 checkpoints 之间的规律

| 版本 | checkpoint | reason | 日志 |
|---|---|---|---|
| 21d578d | 0 | 0 | do_cacheid 两行 |
| 420c409 | 0（60 秒） | 0 | do_cacheid 两行 |
| e7d6522 | 17 | 0 | do_cacheid 两行 |

三次日志内容**完全一致**，一次都没有推进。这坐实了「内核在这个位置就停住，之后什么都没发生」。

### 一个必须纠正的误读

三次的时间戳：

```
65.892252   (15 秒窗口)
65.933665   (60 秒窗口)
65.912846   (60 秒窗口, checkpoint 17)
```

都在 65.9 秒附近，差异不到 0.05 秒。

**但 bootarg 分别是 15 秒和 60 秒。** 如果 `loader_return_after` 定时器按设定工作，
15 秒那次应该显示约 15 秒，60 秒那次约 60 秒。

结论：**这个时间戳不是「内核运行了多久」**，而是 `mach_absolute_time()` 的读数。
在这个移植里它的基准很可能是 SoC 上电时刻，而不是内核入口。

所以之前我说的「内核跑了 65 秒」是读错了。**内核实际只运行了很短的时间就停住了。**
这也解释了为什么它连 `kprintf("kprintf initialized\n")` 都没打出来——
那行在 checkpoint 18 之前，还在 `arm_vm_init` 之后。

### 另一条佐证：定时器的 banner 不见了

`loader_return_timer()` 触发时，第一件事是往日志里写：

```c
static const char banner[] = "\n--- loader_return_after expired ---\n";
```

而屏幕上**没有这一行**。日志里只有 `--- kernel message buffer ---` 和两行 cache 信息。

也就是说 `loader_return()` 大概率**不是**由定时器调起的。那它是由谁调起的？剩下两个调用点：

- `loader_return_test()` — 由定时器调用，仍会有 banner
- `loader_stop_poll()` — 需要 `loader_live.stop`，那是宿主经另一核发起的，本机没有

三个入口对不上，说明 `loader_log` 的内容在 `linearize()` 时被重排过，
banner 可能落在被裁掉的那一段里。这不是新故障，但意味着**不能靠 banner 判断触发源**。

### 现在的判断

故障窗口非常窄：

```
do_cacheid() 正常返回
   ↓
??? ← 死在这里
   ↓
arm_vm_init() 从未进入
```

`arm_init` 里 `cpu_data_init()` 到 `arm_vm_init()` 之间的语句不多，主要是：
`PE_init_platform`、`configure_timer_apple_regs`、`wfe_timeout_configure`、
`configure_misc_apple_boot_args`、`configure_misc_apple_regs`、
`platform_stall_panic_or_spin`、`processor_bootstrap`。

其中 `platform_stall_panic_or_spin(PLATFORM_STALL_XNU_LOCATION_ARM_INIT)`
这个名字最可疑——它在 34 号 checkpoint 之前，而且「stall」正是观察到的现象。

### 下一步

1. **把 checkpoint 加在 `cpu_data_init()` 之后、`arm_vm_init()` 之前**
   （现有编号里没有这一段的可中断点，需要新增，例如 45/46）
2. **验证 `platform_stall` 分支**：它是「panic 还是自旋」的选择点，
   如果走进了自旋，就完全符合「静默停住」

第 2 条不用改内核也能查——`platform_stall_panic_or_spin` 的行为取决于
boot-arg，先看它的实现。

## 2026-11-16 01:34 — reason 0 的真身：halt_all_cpus，不是「正常跑完」

这一轮修正了一个关键误读，而且它推翻了之前几轮的一个基本假设。

### reason 0 是谁调出来的

`hakios.patch` 第 2402–2414 行：

```c
extern void loader_return(void);

__attribute__((noreturn))
void
halt_all_cpus(boolean_t reboot)
{
    ...
    if (reboot) {
        printf("CPU halted\n");
        PEHaltRestart(kPEHaltCPU);
    }
    /* No platform driver powers the machine off: hand control (and the log) back to the loader. */
    hakios_adreno_stop();
    loader_return();          // ← 这里
    while (1) { ; }
}
```

`halt_all_cpus()` 是 XNU 的**停机路径**，`panic` 最终也走到它。

所以 `reason 0` 有两层含义，我之前只认了第一种：

| 我原先的理解 | 实际情况 |
|---|---|
| 内核正常跑完 → `loader_return()` | **内核走到了 `halt_all_cpus()`**，可能是 panic，也可能是正常关机 |

### 一条一直被忽略的旁证

`x3..x7` 全是零。之前我把它读成「不是异常路径」——那是对的，但结论说早了。

`x3..x7` 只在 `start.s` 的 early-exception 向量里被设置（ESR/ELR/FAR/LR/SP）。
`halt_all_cpus() → loader_return()` 这条路**根本不设置它们**，所以它们当然是零。

**「x3..x7 全零」和「走 halt 路径」是同一件事的两种说法**，我却一直拿它当「内核正常」的证据。

### 那 panic 文本为什么没出现

`loader_return()` 里：

```c
loader_log_linearize();
if (!loader_return_entered) {
    loader_log_append_msgbuf();          // 附加内核消息缓冲
}
if (!loader_return_entered && debug_buf_base != NULL && ...) {
    loader_log_puts("\n--- panic buffer ---\n");   // 附加 panic 缓冲
    ...
}
```

而 `loader_log_append_msgbuf()` 的守卫：

```c
struct msgbuf *mb = msgbufp;
if (mb == NULL || mb->msg_bufc == NULL || mb->msg_size <= 0 || ...) {
    return;                              // 静默返回，一个字都不加
}
loader_log_puts("\n--- kernel message buffer ---\n");
```

屏幕上**出现了** `--- kernel message buffer ---`，说明 `msgbufp` 有效、守卫通过了。
后面跟着的循环会跳过零字节，但不会丢 panic 文本。

**结论：`msgbuf` 里就只有那两行。** 内核在 `do_cacheid()` 之后、
`kprintf("kprintf initialized")`（在 checkpoint 18 之前）之前就停了，
那时 `msgbuf` 还几乎没内容。

### 三次运行的数据

| 版本 | 参数 | 时间戳 | 日志长度 | 内容 |
|---|---|---|---|---|
| 21d578d | `loader_return_after=15` | 65.892 | 192 | do_cacheid ×2 |
| 420c409 | `=60` | 65.934 | 192 | 同上 |
| e7d6522 | `=60 checkpoint=17` | 65.913 | 192 | 同上 |
| **c68609d** | `=60 xnu-stall=no debug=0x14e` | **65.100** | **194** | 同上 |

`c68609d` 是唯一一个时间戳和长度都变了的一次（65.9→65.1，192→194）。

**这说明 `xnu-stall=no` / `debug=0x14e` 至少有一个被内核接受了**，行为确实变了。
但停止点没变——仍卡在同一处。

### 现在的完整判断

```
_start → arm_init
  → loader_checkpoint(10)        t0sz=24，loader_jump 拒绝
  → cpu_data_init()
      → do_cacheid()             日志最后两行
  → PE_init_platform()           ← 之后就没有任何输出了
  → configure_timer_apple_regs()
  → wfe_timeout_configure()
  → wfe_timeout_init()
  → configure_misc_apple_boot_args()
  → configure_misc_apple_regs()
  → platform_stall_panic_or_spin()
  → ...
  → arm_vm_init()                ← 从未到达（checkpoint 17 未命中）
  → halt_all_cpus()              ← 但确实到了这里
```

「到了 `halt_all_cpus()`」和「没到 `arm_vm_init()`」是矛盾的——除非
`halt_all_cpus()` 是被 panic 从中间某处直接调用的（panic 不返回）。

**这正好解释了一切**：内核在 `arm_init` 中途 panic，panic → `halt_all_cpus()` →
`loader_return()` → 我们看到的 reason 0。而 panic 文本没能进 `msgbuf`，
因为那个阶段 `msgbuf` 的 printf 通路还没建立。

### 下一步

1. **确认 panic**：加 `-p` 或 `debug=0x...` 打开 panic 的串口/屏幕输出
2. **绕开 checkpoint 门槛**：放宽 `loader_jump` 的 `t0sz` 下限到 24，
   这样才能用 11/30/31/32/33/34 定位到具体语句

第 2 条需要重编内核，但现在是唯一能真正推进的路。

## 2026-11-16 19:53 — 新内核确证在镜像里，行为却没变

`80b59c0`（第一个真正含 `t0sz` 改动的镜像）上机。

### 屏幕

```
kernel returned: reason 0, log 192 bytes
checkpoint 0 delta 00000010C900000
--- kernel message buffer ---
[  66.116463]: vm_cache_geometry_colors: 16
[  66.116475]: do_cacheid() - 65536 bytes Unknown cache (I: 32768 D: 32768 (separate)),
              16-way assoc, 64 bytes/line
```

三带齐全，条码正常，日志还是那两行，长度还是 192。

### 先排除「镜像装错」这个可能

上一轮查出 payload 曾打包 dSYM 里的 66 MB 调试镜像，所以这次不敢再假设。
把 boot.img 拆到底，直接读 FAT 里的 `kernel.macho`：

```
/EFI/UniXNU/Payloads/kernel.macho   15,791,328  3856 cl
  sha256  167066dac79b956b896d611bb3a7961475db42cf0571ec8d8959f371993af3cf
```

对比本地产物：

```
新内核（含 t0sz 改动）  167066dac79b956b896d611bb3a7961475db42cf0571ec8d8959f371993af3cf
旧内核                  bd33c05acb5ff77fb6c6085a8822e641e7099c53d53e7978d15992316e1e0772
```

**镜像里的就是新内核，字节完全一致。** 这条假设正式排除。

### 路上修的一个解析 bug

拆 FAT 时先后踩了两次坑，都值得记下来：

1. `fat_ls.py` 用 `root_entries != 0` 判定 FAT16，但这个卷标着 FAT16、
   FAT 表只有 12 扇区，按 FAT16 读 16 位项会把边界读错——
   `fat_entry(6)` 返回 `0xA009`（40969），远超卷里的 3960 个簇，
   于是链在 5 个簇后就断了，15.8 MB 的内核被读成 8192 字节。

2. 按 FAT12 的 12 位打包规则重读，`cluster 6 -> 7 -> 8 -> 9...` 是连续链，
   一切正常。

新增 `scripts/ci/fat12_read.py` 专门做这件事。

### 结论：`t0sz` 改动生效了，但没解决问题

这次的数据是干净的，所以可以确定：

**内核确实是新的，仍然停在 `do_cacheid()` 之后，仍然只写 192 字节日志。**

也就是说，放宽 `loader_jump` 的 `t0sz` 下限**没有让早期 checkpoint 工作起来**。

### 为什么

回看 `loader_checkpoint()`：

```c
void
loader_checkpoint(uint64_t n)
{
    if (loader_stub_slot[2] == n) {
        loader_jump(2, n);
    }
}
```

而这次启动参数是 `-v loader_return_after=60 xnu-stall=no debug=0x14e`，
**没有 checkpoint=n**。`slot[2]` 是 0，`loader_checkpoint(n)` 全部不匹配。

这一版本来就不是用来测 checkpoint 的——它是用来验证新内核能否改变行为的。
答案是：**不能。**

### 下一次必须带 checkpoint

这轮证明了工具链正确（新内核确实在镜像里），下一轮才是真正的检测点实验：

```
-v loader_return_after=60 checkpoint=11
```

选 11 而不是 10，理由：
- 10 在 `cpu_data_init()` **之前**，而日志证明 `do_cacheid()`（在 `cpu_data_init` 内部）
  已经执行过，所以 10 必然命中——命中就说明内核还没到我们要找的地方，信息量低
- 11 在 `PE_init_platform()` 之后，而日志显示那之后**没有任何输出**，
  所以 11 命中与否直接指向「`PE_init_platform` 是否跑完」

### 判读

| 结果 | 含义 |
|---|---|
| `reason 2` + `checkpoint 11` | 门槛通了，`PE_init_platform` 跑完了，故障在更后面 |
| `reason 0` | 11 没命中，故障在 `cpu_data_init` 和 `PE_init_platform` 之间 |
| 没有返回 | 内核死在更早，或 `loader_jump` 仍被拒 |

## 2026-11-16 20:42 — checkpoint 11 命中：t0sz 放宽生效了

这是整条链路上第一个真正的 `reason 2`。

### 屏幕

```
kernel returned: reason 2, log 0 bytes
x3 0000000000000000  x4 0000000000000000
x5 0000000000000000  x6 0000000000000000
x7 0000000000000000
slot magic 545352444C554E58  stub 0000000D1304000
checkpoint 11 delta 00000010C900000
(log empty)
```

参数是 `-v loader_return_after=60 checkpoint=11`。

### 三个结论

**1. `t0sz` 放宽真的起作用了**

前四次上机拿到的都是 `reason 0`（`halt_all_cpus` 路径），这次是 `reason 2`
（`loader_checkpoint` 路径）。`loader_jump()` 里的

```c
if (t0sz < 25 || t0sz > 33 || (stub >> (64 - t0sz)) != 0) return;
```

在所有早期检测点上都会直接 return，因为内核进来时 `TCR_EL1 = 0x280803518`，
`t0sz = 24`。把下限改成 24 之后，这道门才开。

**2. 内核确实走到了 `PE_init_platform()` 之后**

`loader_checkpoint(11)` 在 `PE_init_platform(FALSE, args)` 的下一行。
它能命中，说明：

- `cpu_data_init(&BootCpuData)` 跑完了（`do_cacheid()` 在里面，日志也印证过）
- `PE_init_platform()` 跑完了

之前所有「内核可能死在 arm_init 之前」的猜测全部作废。

**3. `log 0 bytes` 是正常的，不是坏消息**

`msgbuf` 在 `kprintf` 初始化之前是空的，而 `kprintf` 要到 checkpoint 18/19
之后才建立（`kernel_startup_initialize_upto(STARTUP_SUB_KPRINTF)`）。
11 在那么早的位置，日志当然是空的。

屏幕上的 `(log empty)` 就是 `DarwinShowKernelLog` 对空日志的分支。

### 检测点序列更新

```
PE_init_platform()                已过
cp11                              ★ 命中
configure_timer_apple_regs()      cp30
wfe_timeout_configure()           cp31
wfe_timeout_init()                cp32
configure_misc_apple_boot_args()  (无检测点)
configure_misc_apple_regs()       (无检测点)
cp33
platform_stall_panic_or_spin()    ← 唯一的静默嫌疑
cp34                              ← 下一个目标
cp35
ml_parse_cpu_topology()
cp12
cp40..44
arm_vm_init()
cp17
```

### 下一步：直接跳 34

不逐个走 30/31/32/33，而是一次跨到 34，因为它卡在 `platform_stall_panic_or_spin()`
后面——那正是这段里唯一能不报错就静默停住的地方。

| 34 的结果 | 含义 |
|---|---|
| `reason 2` | `configure_*` 全部跑完，stall 也过了；故障在 34 之后 |
| `reason 0` | 没到 34；用 30/31/32/33 把剩下那段切开 |

## 2026-11-16 22:40 — checkpoint 34 命中：stall 假设排除，arm_init 前半段全通

### 屏幕

```
kernel returned: reason 2, log 0 bytes
x3 0000000000000000  x4 0000000000000000
x5 0000000000000000  x6 0000000000000000
x7 0000000000000000
slot magic 545352444C554E58  stub 0000000D1304000
checkpoint 34 delta 00000010C900000
(log empty)
```

参数 `-v loader_return_after=60 checkpoint=34`，两个独立位置都确认了编号
（顶部 `checkpoint 34`，中部 `DARWINBOOT: checkpoint requested 34`）。

### 排除掉的最大嫌疑

`platform_stall_panic_or_spin(PLATFORM_STALL_XNU_LOCATION_ARM_INIT)` 在
checkpoint 34 的**前一行**。34 命中，说明这个调用**正常返回了**。

这是这几轮里最想确认的一件事：它是 `#if (DEVELOPMENT || DEBUG)` 里的
bring-up 钩子，如果走进自旋分支，现象就是「不 panic、不打日志、画面定住」——
和我们看到的一模一样。**现在这条假设正式排除。**

### 已确认通过的完整路径

```
arm_init(args)
  cpu_data_init(&BootCpuData)
    do_cacheid()                    ← 日志那两行，早已确认
  PE_init_platform(FALSE, args)     ← cp11 命中
  configure_timer_apple_regs()      ← cp30
  wfe_timeout_configure()           ← cp31
  wfe_timeout_init()                ← cp32
  configure_misc_apple_boot_args()  ← cp33
  configure_misc_apple_regs()
  platform_stall_panic_or_spin()    ← cp34 命中，不是 stall
```

**`arm_init` 的前半段完全健康。** 之前所有关于「内核死在这段」的推测都作废了。

### 下一个目标：17

34 之后到 17 之间还有：`cp35`、`ml_parse_cpu_topology()`、`cp12`、
`cp40~44`、`cp13~16`，然后是 `arm_vm_init(xmaxmem, args)` 和 `cp17`。

选 17 而不是 35/12/40/13 的理由有两个：

1. **它证明 `arm_vm_init()` 跑完** —— 这是 `arm_init` 里最后一个未知的大块
2. **它应该第一次给出非空日志** —— `kprintf` 在 checkpoint 18/19 附近初始化，
   而 17 在 `arm_vm_init` 之后，那时 `msgbuf` 该有内容了

第 2 点尤其重要：到现在为止所有检测点的日志都是 0 字节，我们始终看不到
内核自己说了什么。17 如果命中且日志非空，就能读到 `kprintf` 的实际内容。

### 判读

| 结果 | 含义 |
|---|---|
| `reason 2` + 非空日志 | `arm_init()` 跑完了，故障在 `machine_startup()` 或更后 |
| `reason 2` + 空日志 | 到了 `arm_vm_init` 但 `kprintf` 没起来，这本身是个发现 |
| `reason 0` | 死在 34 和 `arm_vm_init` 之间，用 35/12/40~44/13~16 切开 |

## 2026-11-16 22:59 — checkpoint 17 未命中：故障窗口两侧都封住了

### 屏幕

```
kernel returned: reason 0, log 192 bytes
checkpoint 17 delta 00000010C900000
--- kernel message buffer ---
[  67.947725]: vm_cache_geometry_colors: 16
[  67.947737]: do_cacheid() - 65536 bytes Unknown cache (I: 32768 D: 32768 (separate)),
              16-way assoc, 64 bytes/line
```

`reason 0`，不是 2。**checkpoint 17 没有命中。**

### 现在的窗口

三次结果放在一起，范围第一次有了两侧边界：

| 检测点 | 结果 | 位置 |
|---|---|---|
| 11 | `reason 2` | `PE_init_platform()` 之后 |
| 34 | `reason 2` | `platform_stall_panic_or_spin()` 之后 |
| **17** | **`reason 0`** | **`arm_vm_init()` 之后 —— 未到达** |

所以内核死在 **cp34 之后、`arm_vm_init()` 完成之前**。

### 中间这 11 个检测点

```
cp34  ★命中
      chosen = NULL;  dt_entry_size = 0;
cp35
      ml_parse_cpu_topology();
cp12
      master_cpu = ml_get_boot_cpu_number();
cp40
      assert(master_cpu >= 0 && master_cpu <= ml_get_max_cpu_number());
      BootCpuData.cpu_number = (unsigned short)master_cpu;
      CpuDataEntries[master_cpu].cpu_data_paddr = ...;
cp41
      thread = thread_bootstrap();
cp42
      thread->machine.CpuDatap = &BootCpuData;
      thread->machine.pcpu_data_base = 0;
      machine_set_current_thread(thread);
cp43
      thread->machine.preemption_count = 0;
cp44
      cpu_bootstrap();
cp13
      rtclock_early_init();
cp14
      kernel_debug_string_early("kernel_startup_bootstrap");
      kernel_startup_bootstrap();
cp15
      timer_call_init();
      cpu_init();
cp16
      processor_bootstrap();
      arm_vm_init(xmaxmem, args);
cp17  ✗未命中
```

### 日志仍是 192 字节，这本身是一条线索

`kernel_debug_string_early("kernel_startup_bootstrap")` 在 cp14 之后。
它如果执行了，日志应该会变长。日志没变，说明**内核没走到 cp14**。

这把范围进一步收到 cp34 到 cp14 之间：

```
35, 12, 40, 41, 42, 43, 44, 13
```

### 下一个：44（中点）

34→17 的中点附近就是 44，无论命中与否都能把区间砍半。

| 结果 | 新的窗口 |
|---|---|
| `reason 2` | 故障在 44 → 17（cpu_bootstrap / rtclock / startup / cpu_init / processor_bootstrap / arm_vm_init） |
| `reason 0` | 故障在 34 → 44（拓扑解析 / assert / thread_bootstrap 那一段） |

理论上再 3 次就能定位到单个函数。

## 2026-11-16 23:28 — checkpoint 44 命中：窗口收窄到 44 之后

```
kernel returned: reason 2, log 0 bytes
checkpoint 44 delta 00000010C900000
DARWINBOOT: checkpoint requested 44
```

### 四次的完整图景

| 检测点 | 结果 | 已确认通过的最后一步 |
|---|---|---|
| 11 | `reason 2` | `PE_init_platform()` |
| 34 | `reason 2` | `platform_stall_panic_or_spin()` |
| 44 | `reason 2` | `thread_bootstrap()` 及其后续设置 |
| 17 | `reason 0` | —— `arm_vm_init()` 从未完成 |

故障窗口：**cp44 之后、`arm_vm_init()` 完成之前**。

剩下四个检测点和五处调用：

```
cp44  ★
      cpu_bootstrap();                          13
      rtclock_early_init();                     14
      kernel_debug_string_early("kernel_startup_bootstrap");
      kernel_startup_bootstrap();               15
      timer_call_init();  cpu_init();           16
      processor_bootstrap();
      arm_vm_init(xmaxmem, args);               17  ✗
```

### 下一个：15（中点）

同时还有一个额外信号：`kernel_debug_string_early()` 在 cp14 之前，
而到现在为止日志一直是 **0 字节**。

- 15 命中 + 日志仍空 → 那个调用也没产出，问题可能在 console 而不在代码
- 15 命中 + 日志变长 → **第一次看到内核自己说话**，文本本身即是发现

### 已用过的时间

从 checkpoint 11 命中（20:42）到 44 命中（23:28），约 2 小时 45 分，
中间经历了 3 次完整构建+刷机循环。每轮约 25 分钟（构建 10 分 + 下载 + 刷机 + 拍照）。

## 2026-11-17 00:21 — checkpoint 15 命中：只剩一个检测点

```
kernel returned: reason 2, log 0 bytes
checkpoint 15 delta 00000010C900000
DARWINBOOT: checkpoint requested 15
```

### 五次的完整图景

| 检测点 | 结果 | 已确认通过的最后一步 |
|---|---|---|
| 11 | `reason 2` | `PE_init_platform()` |
| 34 | `reason 2` | `platform_stall_panic_or_spin()` |
| 44 | `reason 2` | `thread_bootstrap()` |
| **15** | **`reason 2`** | **`kernel_startup_bootstrap()`** |
| 17 | `reason 0` | —— `arm_vm_init()` 从未完成 |

### 只剩三处调用、一个检测点

```
cp15  ★
      timer_call_init();
      cpu_init();                          16  ← 最后一个
      processor_bootstrap();
      arm_vm_init(xmaxmem, args);          17  ✗
```

### 一个必须记下来的发现：日志始终是 0 字节

从 cp11 到 cp15，**四次的日志都是 `log 0 bytes`**。

而 `kernel_debug_string_early("kernel_startup_bootstrap")` 就在 cp15 之前执行过。
它同样没有产出任何东西。

这说明：**内核的 console 通路在这个阶段完全没建立**，而不是「某个函数卡住了它」。
`kprintf` 要到 cp18/19 之后（`kernel_startup_initialize_upto(STARTUP_SUB_KPRINTF)`），
`PE_init_printf` 在 cp20 之后。

所以这段区间里，内核即使有话说也发不出来。**想拿到调用栈，只能靠检测点，
不能指望内核自己打印。**

### 下一步

cp16 是最后一个已埋好的检测点。之后要往前推进，必须改内核——
在 `processor_bootstrap()` 和 `arm_vm_init()` 前后各加一个检测点。

| 16 的结果 | 新的窗口 | 下一步 |
|---|---|---|
| `reason 2` | `processor_bootstrap()` 或 `arm_vm_init()` | 需要新增检测点 |
| `reason 0` | `timer_call_init()` 或 `cpu_init()` | 需要新增检测点 |

无论哪种结果，下一轮都要重编内核（约 10 分钟），不能再靠换 bootarg 了。

## 2026-11-17 00:39 — checkpoint 16 命中，窗口收到最后两处调用

```
kernel returned: reason 2, log 0 bytes
checkpoint 16 delta 00000010C900000
DARWINBOOT: checkpoint requested 16
```

### 六次检测点的完整结果

| 检测点 | 结果 | 已确认通过的最后一步 |
|---|---|---|
| 11 | `reason 2` | `PE_init_platform()` |
| 34 | `reason 2` | `platform_stall_panic_or_spin()` |
| 44 | `reason 2` | `thread_bootstrap()` |
| 15 | `reason 2` | `kernel_startup_bootstrap()` |
| **16** | **`reason 2`** | **`timer_call_init()` 和 `cpu_init()`** |
| 17 | `reason 0` | —— `arm_vm_init()` 从未完成 |

**`arm_init` 里只剩两处调用没有验证过：**

```
cp16  ★
      processor_bootstrap();
      arm_vm_init(xmaxmem, args);
cp17  ✗
```

### 二分法到此为止，必须动内核

已埋好的检测点用完了。要继续走，只能在 `processor_bootstrap()` 和
`arm_vm_init()` 之间新埋一个。

### 参考 meizu21 项目得到的两个改动

翻了 `UniXNU/UniXNU` 的 `docs/xnu/four-k-page-tables.md` 和
`patches/xnu/non-apple-cache-monitor.patch`，发现两件事：

**1. `ml_enable_monitor()` 缺 guard —— 头号嫌疑**

`osfmk/arm64/arm_vm_init.c` 里：

```c
arm_vm_physmap_init(args);
set_mmu_ttb_alternate(cpu_ttep & TTBR_BADDR_MASK);

#if XNU_MONITOR          ← UniXNU 加的，我们没有
ml_enable_monitor();
#endif

set_mmu_ttb(invalid_ttep & TTBR_BADDR_MASK);
```

**这是 `arm_vm_init()` 干的第一件事**，而 `arm_vm_init()` 正是检测点 17 之前
最后一段没验证的代码。Apple 平台上这行开 PPL（页保护层），本 SoC 没有 PPL。

grep 我们的 `hakios.patch`：`ml_enable_monitor` 出现 **0 次**。

**2. 新增检测点 45**

埋在 `processor_bootstrap()` 之后、`arm_vm_init()` 之前。这样如果 17 仍然
不命中，一次上机就能分清是 `processor_bootstrap()` 还是 `arm_vm_init()`。

### 实现

新脚本 `scripts/ci/apply_nonapple_guards.py`，在 CI 里于 apply.sh 之后运行：

```
guarded ml_enable_monitor() in .../osfmk/arm64/arm_vm_init.c
added loader_checkpoint(45) after processor_bootstrap() in .../osfmk/arm/arm_init.c
done (2 change(s))
```

内核构建成功，说明 `XNU_MONITOR` 宏在该文件可见、guard 语法正确。

### 下一次上机的判读

启动参数回到 `checkpoint=17`：

| 结果 | 含义 |
|---|---|
| `reason 2` | `ml_enable_monitor()` 就是卡点，`arm_vm_init()` 第一次跑完 |
| `reason 0` | 还卡着 → 用 `checkpoint=45` 分清是哪个函数 |

### 另外记录：参考项目与我们路线的差异

`loolooc/UniXNU-meizu21` 和 `XiaoChen8371/unixnu-meizu21` 都是 SM8650 的
魅族 21，目标 iPadOS 17 / iOS 16.5。它们的 `/STARTUP.md` 明确写着
「r20 使用固定 boot image 中已经验证的组合，不要自行替换其中任意一项」——
**它们不自己编译 XNU**，用已验证的内核快照。

这解释了为什么它们能进 iOS 桌面而我们卡在 `arm_init`：它们绕开了最难的一段。
但 UniXNU 官方的 `four-k-page-tables.md` 因为同样使用 BCM2837 4K 配置，
对我们直接适用，列出了完整的必备补丁清单。逐条比对后我们缺的是：

| 补丁 | 影响 |
|---|---|
| `non-apple-cache-monitor` | `ml_enable_monitor()` —— **已补** |
| `apple-only-cpu-tunables` | `APPLY_TUNABLES` 在非 Apple 上也会跑 |
| `native-4k-user-abi` | 4K 用户态页 shift（影响用户态，不影响启动） |
| `pgz-aligned-validation` | PGZ 地址解码 |

`BOOTSTRAP_TABLE_SIZE`（`*20`）和 `zzx-pmap-sync-64bit-pa` 我们已经有了。

## 2026-11-17 01:39 — guard 加上后 17 仍不命中：ml_enable_monitor 排除

```
kernel returned: reason 0, log 192 bytes
checkpoint 17 delta 00000010C900000
--- kernel message buffer ---
[  67.960823]: vm_cache_geometry_colors: 16
[  67.960835]: do_cacheid() - 65536 bytes Unknown cache (I: 32768 D: 32768 (separate)),
              16-way assoc, 64 bytes/line
```

给 `ml_enable_monitor()` 加了 `#if XNU_MONITOR` guard、重新编译内核
（`babcf01e…`，与上一版差 21437 字节）、确认装进镜像（镜像内哈希一致）之后，
**checkpoint 17 依然 `reason 0`，日志依然 192 字节。**

### 结论

**`ml_enable_monitor()` 不是卡点**，至少不是唯一的一个。

这是一个干净的排除：内核确实换了（字节差异 + 镜像内哈希双重确认），
guard 确实生效（CI 日志有 `guarded ml_enable_monitor() in ...`），
行为却没有变化。

### 下一步：45

好在同一轮里已经把 checkpoint 45 埋进了内核，位置正好在
`processor_bootstrap()` 之后、`arm_vm_init()` 之前：

```
cp16  ★命中（timer_call_init / cpu_init 都过了）
      processor_bootstrap();
cp45  ← 这次测
      arm_vm_init(xmaxmem, args);
cp17  ✗
```

| 45 的结果 | 含义 |
|---|---|
| `reason 2` | `processor_bootstrap()` 没问题，故障在 `arm_vm_init()` 内部，需要在它里面再埋细的检测点 |
| `reason 0` | 故障就在 `processor_bootstrap()` —— 这个函数此前从未被单独验证过 |

这次**不需要重编内核**，因为 45 已经在 `inputs/kernel.macho` 里了，
只改 launcher 触发 payload 重建即可（约 8 分钟，比之前省一半）。

### 如果落到 arm_vm_init 里面

`arm_vm_init()` 是个很大的函数（源码 89 KB），它开头是：

```c
arm_vm_physmap_init(args);
set_mmu_ttb_alternate(cpu_ttep & TTBR_BADDR_MASK);
ml_enable_monitor();          ← 已排除
set_mmu_ttb(invalid_ttep & TTBR_BADDR_MASK);
flush_mmu_tlb();
kva_active = TRUE;
...
```

后面还有 `pmap_bootstrap`、CPU 拓扑、页表建立等。真到那一步，
需要在里面再埋 2～3 个检测点，一次构建全部埋进去，避免一轮一个。

## 2026-11-17 01:57 — checkpoint 45 命中：故障在 arm_vm_init() 内部

```
kernel returned: reason 2, log 0 bytes
checkpoint 45 delta 00000010C900000
DARWINBOOT: checkpoint requested 45
```

### 45 命中意味着什么

45 埋在 `processor_bootstrap()` 之后、`arm_vm_init()` 之前。它命中，说明：

- `processor_bootstrap()` **没有问题**（这个函数此前从未被单独验证过）
- 故障确定在 **`arm_vm_init()` 内部**

### arm_vm_init 的结构

这个函数 580 行（源码 89 KB），关键里程碑：

```
1816  arm_vm_init 开始
1955  cpu_ttep = kvtophys((vm_offset_t)cpu_tte);
2141  arm_set_user_tbi();
2143  arm_vm_physmap_init(args);
2144  set_mmu_ttb_alternate(cpu_ttep & TTBR_BADDR_MASK);
2146  ml_enable_monitor();          ← 已排除
2148  set_mmu_ttb(invalid_ttep & TTBR_BADDR_MASK);
2150  flush_mmu_tlb();
2151  kva_active = TRUE;            ← 从 bootstrap 页表切走的时刻
2200  flush_mmu_tlb();
2247  pmap_bootstrap(dynamic_memory_begin);
```

### 决定：一次埋四个点，不再一轮一个

前面为了走完 `arm_init` 花了 6 次上机、约 3 小时。这个函数有 580 行，
按老办法要 6～8 轮。所以这次一次埋 46/47/48/49：

```
arm_vm_init()
    46  arm_set_user_tbi() 之前     → 前 ~300 行都过了
    47  kva_active = TRUE 之后      → physmap + 两次 set_mmu_ttb +
                                       flush_mmu_tlb + 切换页表都过了
    48  pmap_bootstrap() 之前       → AuxKC 和 arm_vm_map 那一大块过了
    49  pmap_bootstrap() 之后       → pmap_bootstrap 自身过了
```

一次构建，四次上机就能把 580 行切成五段。

### 先测 48 的理由

48 之前全是普通的页表操作；`pmap_bootstrap()` 才是 pmap 真正建立的地方。

而日志里那条一直存在的可疑信息是：

```
do_cacheid() - 65536 bytes Unknown cache (I: 32768 D: 32768 (separate)),
               16-way assoc, 64 bytes/line
```

**32K+32K / 16-way / 64B line 这个组合在真实硅片上不存在**（A53 的 L1 是 2-way，
只有 L2 才是 16-way；A73 的 L1 是 64K）。

`pmap_bootstrap()` 正是第一个会因为缓存几何不正确而行为异常的地方
（它要按 cache line 对齐、按 way 组织）。所以 48 这一跳的信息量最大。

### 顺带

`arm_vm_init.c` 里原本没有 `loader_checkpoint` 的声明（hakios 补丁只在
`arm_init.c` 里加了 `extern`），脚本里一并补上，否则 C99 下会因隐式声明
编译失败。

## 2026-11-17 02:42 — checkpoint 48 未命中：故障在 pmap_bootstrap 之前

```
kernel returned: reason 0, log 192 bytes
checkpoint 48 delta 00000010C900000
DARWINBOOT: checkpoint requested 48
```

日志仍是 192 字节，仍是 `do_cacheid` 那两行。

### 窗口

| 检测点 | 结果 | 位置 |
|---|---|---|
| 45 | `reason 2` | `processor_bootstrap()` 之后、`arm_vm_init()` 之前 |
| **48** | **`reason 0`** | **`pmap_bootstrap()` 之前 —— 未到达** |

所以故障在 **cp45 与 cp48 之间**，也就是 `arm_vm_init()` 的前三分之一。

### 剩下两个点

```
cp45  ★命中
      ...前 ~300 行...
cp46  arm_set_user_tbi() 之前
      arm_set_user_tbi();  arm_vm_physmap_init();  set_mmu_ttb_alternate();
      set_mmu_ttb(invalid_ttep);  flush_mmu_tlb();  kva_active = TRUE;
cp47  ← 下一步
      ...AuxKC、arm_vm_map 循环...
cp48  ✗未命中
      pmap_bootstrap(dynamic_memory_begin);
cp49
```

### 下一步：47

| 47 的结果 | 新的窗口 |
|---|---|
| `reason 2` | 切出 bootstrap 页表成功；故障在 `kva_active` 到 `pmap_bootstrap` 之间（AuxKC 块、`arm_vm_map` 循环） |
| `reason 0` | 没走到那里；再用 46 区分「前 300 行」和「physmap / set_mmu_ttb / flush」 |

### 一个观察

从 cp45 命中到 cp48 未命中，日志**始终是同样的 192 字节**。也就是说
`arm_vm_init()` 里没有任何一行成功输出过。

这不奇怪——`kprintf` 要到 checkpoint 18/19 之后才初始化，而 18/19 在
`arm_vm_init()` **之后**。所以这段区间内核即便想报错也发不出来。
无论这里是挂起还是静默 panic，屏幕上都只剩那两行。

### 效率

这次仍然不需要重编内核——46/47/48/49 都在 `inputs/kernel.macho`
（`c8f24d1b…`）里了。改 launcher → payload 重建约 8 分钟 → 刷机。
若 47 也不命中，接下来测 46，同样只重建 payload。

## 2026-11-17 03:10 — checkpoint 46 也未命中：窗口收到 arm_vm_init 的前 325 行

```
kernel returned: reason 0, log 192 bytes
checkpoint 46 delta 00000010C900000
```

### 四个预先埋好的检测点全部用完

| 检测点 | 结果 | 位置 |
|---|---|---|
| 45 | `reason 2` | 进入 `arm_vm_init()` |
| 46 | `reason 0` | `arm_set_user_tbi()` 之前 —— 未到达 |
| 47 | `reason 0` | `kva_active = TRUE` 之后 —— 未到达 |
| 48 | `reason 0` | `pmap_bootstrap()` 之前 —— 未到达 |

故障在 **`arm_vm_init()` 的 1816–2141 行**，即函数开头 325 行。

### 这一段在做什么

```c
1831  gVirtBase = args->virtBase;
1832  gPhysBase = args->physBase;
1836  real_phys_size = args->memSize;
1847  gPhysSize = mem_size = ((gPhysBase + args->memSize) & ~PAGE_MASK) - gPhysBase;
1857  if (mem_size >= ((VM_MAX_KERNEL_ADDRESS - VM_MIN_KERNEL_ADDRESS) / 2))
1858      panic("Unsupported memory configuration %lx", mem_size);
1865  physmap_base = phystokv(args->topOfKernelData);
1869  uint64_t physmap_slide = early_random() & ARM64_PHYSMAP_SLIDE_MASK & ~PAGE_MASK;
1872  physmap_base += physmap_slide;
1888  static_memory_end = physmap_base + mem_size + (PTOV_TABLE_SIZE * ARM_TT_TWIG_SIZE);
1914  boot_tte = (tt_entry_t *)&bootstrap_pagetables;
1917  #if DEVELOPMENT || DEBUG
1924      if (pages_used > BOOTSTRAP_TABLE_SIZE)
1925          panic("BOOTSTRAP_TABLE_SIZE too small for memory config");
1947  invalid_tte = (tt_entry_t *)alloc_ptpage(TRUE);
1954  cpu_tte = (tt_entry_t *)alloc_ptpage(TRUE);
1973  init_ptpages(cpu_tte, gVirtBase, dynamic_memory_begin, TRUE, ...);
1993  segPRELINKTEXTB = getsegdatafromheader(&_mh_execute_header, "__PRELINK_TEXT", ...);
     （后面十几个 getsegdatafromheader）
2076  vm_set_page_size();
2097  arm_vm_prot_init(args);
2121  init_ptpages(cpu_tte, dynamic_memory_begin, ROUND_L1(dynamic_memory_begin), FALSE, ...);
2127  init_ptpages(cpu_tte, dynamic_memory_begin, VM_MAX_KERNEL_ADDRESS, TRUE, ...);
```

### 三个具体的嫌疑

**1. `early_random()`（1869 行）**

在这么早的阶段取随机数。如果熵源（通常是 timer 或 SoC 的 RNG）没起来，
这个调用本身就可能挂住。而 Physmap slide 依赖它。

**2. `init_ptpages()`（1973 行）**

这是**页表真正建立的地方**。它遍历 kernel 的段列表，为每一段建 L1/L2 表项。
如果段信息（来自 boot_args / Mach-O header）不对，或者页表池不够，这里会出问题。

**3. `BOOTSTRAP_TABLE_SIZE` 检查（1917–1927）**

```c
#if DEVELOPMENT || DEBUG
    size_t bytes_mapped = dynamic_memory_begin - gVirtBase;
    size_t l1_entries = 1 + ((bytes_mapped + ARM_TT_L1_SIZE - 1) / ARM_TT_L1_SIZE);
    size_t pages_used = 2 * (l1_entries + 1);
    if (pages_used > BOOTSTRAP_TABLE_SIZE) {
        panic("BOOTSTRAP_TABLE_SIZE too small for memory config");
    }
#endif
```

我们的内核是 DEVELOPMENT 构建，这个检查会执行。hakios 已把
`BOOTSTRAP_TABLE_SIZE` 从 `ARM_PGBYTES * 8` 改成 `* 20`。

注意单位：`pages_used` 是**页数**，而 `BOOTSTRAP_TABLE_SIZE` 是**字节数**
（`ARM_PGBYTES * 20` = 81920）。页数永远小于 81920，所以这个比较**恒为假**，
不可能触发 panic。这一条可以排除。

### 新埋的四个点（50–53）

```
cp45  ★命中
      98 行：内存几何、real_phys_size、physmap_base、early_random()、physmap_end
cp50  boot_tte = (tt_entry_t *)&bootstrap_pagetables;
      alloc_ptpage() × 2（invalid_tte、cpu_tte）
cp51  init_ptpages(cpu_tte, gVirtBase, dynamic_memory_begin, TRUE, ...)
cp52  ★ 这次测这里
      段查找、vm_set_page_size、arm_vm_prot_init、第二个 init_ptpages
cp53  arm_set_user_tbi() 之前
cp46  ✗未命中
```

51 和 52 **夹住 `init_ptpages()`**：如果 51 命中而 52 不命中，就直接锁定了这个调用。

### 本地先验证了脚本

用下载的 `arm_vm_init.c` 在本地跑了一遍插入，确认：

- 8 个检测点全部插入（46–53）
- 位置正确：50@1916、51@1976、52@1978、53@2103、46@2147、47@2159、48/49@2255/2257
- 幂等性通过（第二次运行无变化）

避免又一次 10 分钟的构建失败。

## 2026-11-17 18:30 — 新探针 50–53 埋入，先在本机验证

checkpoint 46 未命中，窗口锁定在 `arm_vm_init()` 的 1816–2141 行（325 行）。
四个原有探针（45–49）已全部用完，所以在这一段里新埋四枚。

### 新探针的位置

| 探针 | 源码行 | 之前已完成的内容 |
|---|---|---|
| 50 | 1916 | 98 行：`gVirtBase`/`gPhysBase`、`real_phys_size`、`gPhysSize`、`physmap_base`、`early_random()` 取 slide、`physmap_end` 算术 |
| 51 | 1976 | `boot_tte` 赋值 + 两次 `alloc_ptpage()`（`invalid_tte`、`cpu_tte`）及 `bzero` |
| 52 | 1978 | **第一个 `init_ptpages()` 完成** |
| 53 | 2103 | 段查找、`vm_set_page_size()`、`arm_vm_prot_init()`、第二个 `init_ptpages()` |

51 与 52 **夹住 `init_ptpages()`**：若 51 命中而 52 不命中，就直接锁定这个调用。

### 先在本地验证，避免又一次 10 分钟构建失败

用下载的 `arm_vm_init.c` 在临时目录跑了一遍插入脚本：

```
added loader_checkpoint(50)
added loader_checkpoint(51)
added loader_checkpoint(52)
added loader_checkpoint(53)
added loader_checkpoint(46) ... (49)
```

插入后核对行号：50@1916、51@1976、52@1978、53@2103、46@2147、47@2159、48/49@2255/2257 —— 顺序正确。
幂等性也验证了（第二次运行无任何新增）。

### 三个嫌疑点的排查

**1. `early_random()`（1869 行）—— 证据不支持它是卡点**

```c
rc = ccdrbg_init(&erandom.drbg_info, ..., bootseed, ...);
if (rc != CCDRBG_STATUS_OK) {
    panic("ccdrbg_init() returned %d", rc);      // 早期 panic 打印不出来
}
```

失败路径确实是静默 panic，符合症状。但它的依赖是 `ccsha256_ltc_di`
（corecrypto 自带的轻量实现，已静态链进内核），**不需要 crypto provider 注册**。
HakiOS 还专门在别处加了 `if (g_crypto_funcs == NULL) return;` 的 guard。
所以这条链大概率是通的。若测 50 不命中，它就是第一嫌疑；若 50 命中，则排除。

**2. `BOOTSTRAP_TABLE_SIZE` 检查（1917–1927）—— 可以排除**

```c
size_t pages_used = 2 * (l1_entries + 1);
if (pages_used > BOOTSTRAP_TABLE_SIZE) {
    panic("BOOTSTRAP_TABLE_SIZE too small for memory config");
}
```

`pages_used` 是**页数**，`BOOTSTRAP_TABLE_SIZE` 是**字节数**（`ARM_PGBYTES * 20`
= 81920）。页数永远小于 81920，比较恒为假。这条 panic 不可能触发。

**3. `alloc_ptpage()` 无边界检查 —— 结构性问题，值得记下**

```c
#if !(defined(KERNEL_INTEGRITY_KTRR) || defined(KERNEL_INTEGRITY_CTRR))
	map_static = FALSE;                       // 我们没有 KTRR/CTRR
#endif
	if (map_static) {
		assert(ropage_next < (vm_offset_t)&ropagetable_end);   // 有保护
		...
	} else {
		vaddr = phystokv(avail_start);         // 无 assert、无边界检查
		avail_start += ARM_PGBYTES;
	}
```

本机没有 `KERNEL_INTEGRITY_KTRR/CTRR`，于是 `map_static` 被强制改成 FALSE，
走的是**完全没有保护**的那一支。`init_ptpages()` 每映射 1 GB 就取一页，
池若不够就一路往上加，既不看边界也不看那页是否可用。

UniXNU 的 `native-4k-bootstrap-capacity` 补丁正是在 `start.s` 里加了容量检查：

```asm
add $6, x25, #(BOOTSTRAP_TABLE_BYTES >> 12), lsl #12
cmp $4, $6
b.hs Lbootstrap_tables_exhausted
```

但那只保护 `start.s` 里的分配，**不保护 C 代码里 `init_ptpages()` 的循环**。
如果 52 不命中而 51 命中，就要从这里入手。

按我们的几何估算，循环次数并不多：`gVirtBase = 0xFFFFFFF007004000`，
`dynamic_memory_begin` 约 `physmap_base + mem_size + 2 MB`，再叠加最多 1 GB 的
slide，大约 4–6 次迭代。而池有 20 页。所以单纯「池不够」不成立，
除非 `dynamic_memory_begin` 算出来异常大——那又会指回 `early_random()` 和
`physmap_base` 的算术。

### 这一轮的判读表

启动参数 `checkpoint=52`：

| 结果 | 新的窗口 |
|---|---|
| `reason 2` | `init_ptpages()` 完成；故障在段查找 / `arm_vm_prot_init` / 第二个 `init_ptpages` |
| `reason 0` | 测 51 → 命中则是 `init_ptpages()` 本身，不命中则测 50（`early_random` / physmap 算术） |

## 2026-11-17 20:45 — 51 也未命中：init_ptpages 无罪，窗口收到前 98 行

```
kernel returned: reason 0, log 192 bytes
checkpoint 51 delta 00000010C900000
DARWINBOOT: checkpoint requested 51
```

### 八次检测点的完整结果

| 检测点 | 结果 | 已确认通过的最后一步 |
|---|---|---|
| 11 | `reason 2` | `PE_init_platform()` |
| 34 | `reason 2` | `platform_stall_panic_or_spin()` |
| 44 | `reason 2` | `thread_bootstrap()` |
| 15 | `reason 2` | `kernel_startup_bootstrap()` |
| 16 | `reason 2` | `timer_call_init()` + `cpu_init()` |
| 45 | `reason 2` | 进入 `arm_vm_init()` |
| 52 | `reason 0` | —— 第一个 `init_ptpages()` 之后 |
| **51** | **`reason 0`** | **—— `init_ptpages()` 之前那一行** |

### 一个重要结论：`init_ptpages()` 无罪

51 就在 `init_ptpages(...)` 的上一行，它没命中，说明内核**连那个调用之前的一行都没走到**。

所以之前对 `alloc_ptpage()` 无边界检查的怀疑，以及 `init_ptpages()` 循环分配页表的分析，
**都不适用于当前卡点**。那些是真实存在的隐患，但不是现在的原因。

### 窗口：`arm_vm_init()` 的前 98 行

```
cp45  ★命中（进入 arm_vm_init）
      gVirtBase = args->virtBase;
      gPhysBase = args->physBase;
      real_phys_size = args->memSize;
      gPhysSize = mem_size = ((gPhysBase + args->memSize) & ~PAGE_MASK) - gPhysBase;
      if (mem_size >= ((VM_MAX_KERNEL_ADDRESS - VM_MIN_KERNEL_ADDRESS) / 2))
          panic("Unsupported memory configuration %lx", mem_size);
      physmap_base = phystokv(args->topOfKernelData);
      physmap_slide = early_random() & ARM64_PHYSMAP_SLIDE_MASK & ~PAGE_MASK;
      physmap_base += physmap_slide;
      static_memory_end = physmap_base + mem_size + (PTOV_TABLE_SIZE * ARM_TT_TWIG_SIZE);
      dynamic_memory_begin = static_memory_end;
cp50  ← 现在测这里
      boot_tte = (tt_entry_t *)&bootstrap_pagetables;
      alloc_ptpage() × 2
cp51  ✗未命中
```

### 50 这一跳的意义

| 50 的结果 | 含义 |
|---|---|
| `reason 2` | 前 98 行的算术没问题；故障在 `boot_tte`、两次 `alloc_ptpage()`，或者它们中间的 `BOOTSTRAP_TABLE_SIZE` 检查 |
| `reason 0` | **故障就在这 98 行里**，`early_random()` 成为头号嫌疑 |

### 如果 50 不命中

`early_random()` 在 1869 行，它的懒初始化结尾是：

```c
rc = ccdrbg_init(&erandom.drbg_info, ..., sizeof(bootseed), bootseed, ...);
if (rc != CCDRBG_STATUS_OK) {
    panic("ccdrbg_init() returned %d", rc);
}
```

这个阶段 panic 打印不出来（`kprintf` 要到 checkpoint 18 之后），所以机器就是静默停住 ——
和观察完全一致。

反方证据：`ccdrbg_init()` 只需要 `ccsha256_ltc_di`，它是静态链进内核的轻量实现，
不需要 crypto provider 注册，理论上零种子也能成功。

两条都要查。下一次上机就能分辨。

### 另外记一笔：`physmap_base = phystokv(args->topOfKernelData)`

这一行在 1865 行，同样在 98 行之内。`topOfKernelData` 由 loader 填进 `boot_args`，
而我们面板上显示的是 `WIN D0004000 HIGH D0004000 ... window 0x30000000`。

如果这个字段和窗口的关系算错，`physmap_base` 会偏，后面的 `static_memory_end`、
`dynamic_memory_begin` 全部跟着偏。这是 98 行里第三个值得查的算术。

## 2026-11-17 21:10 — 50 也未命中：只剩 arm_vm_init 的前 98 行

```
kernel returned: reason 0, log 192 bytes
checkpoint 50 delta 00000010C900000
DARWINBOOT: checkpoint requested 50
```

### 十次检测点的完整结果

| 检测点 | 结果 | 已确认通过的最后一步 |
|---|---|---|
| 11 | `reason 2` | `PE_init_platform()` |
| 34 | `reason 2` | `platform_stall_panic_or_spin()` |
| 44 | `reason 2` | `thread_bootstrap()` |
| 15 | `reason 2` | `kernel_startup_bootstrap()` |
| 16 | `reason 2` | `timer_call_init()` + `cpu_init()` |
| 45 | `reason 2` | 进入 `arm_vm_init()` |
| 53 | `reason 0` | —— `arm_vm_prot_init()` 之后 |
| 52 | `reason 0` | —— 第一个 `init_ptpages()` 之后 |
| 51 | `reason 0` | —— `init_ptpages()` 之前一行 |
| **50** | **`reason 0`** | **—— `boot_tte` 赋值之前** |

`arm_vm_init()` 里从 46 往后全部静默，而 50 是其中最早的一个。
**故障就在函数开头这 98 行（1816–1914）。**

### 三个候选逐个查证，全部排除

**1. `mem_size` 那条 panic —— 不会触发**

面板上 `window 0x30000000`，即 768 MB。

```c
gPhysSize = mem_size = ((0xD0004000 + 0x30000000) & ~PAGE_MASK) - 0xD0004000 = 0x30000000;
if (mem_size >= ((VM_MAX_KERNEL_ADDRESS - VM_MIN_KERNEL_ADDRESS) / 2))
```

`VM_MAX - VM_MIN = 0xFFFFFFFBFFFFFFFF - 0xFFFFFFE000000000 = 0x1BFFFFFFFF` ≈ 120 GB，
一半是 60 GB。768 MB 远小于它，不触发。

**2. `random-seed` 缺失 —— 已排除，属性存在且有效**

这是我这一轮最看重的怀疑，因为 `early_random()` 的失败路径是静默 panic：

```c
n = PE_get_random_seed(seed, sizeof(seed));       // 要 64 字节
if (n < sizeof(seed))
    panic("Expected %lu seed bytes from bootloader, but got %u.", 64, n);
```

而 `PE_get_random_seed()` 只从设备树读：

```c
if ((SecureDTLookupEntry(NULL, "/chosen", &entryP) == kSuccess)
    && (SecureDTGetProperty(entryP, "random-seed", ..., &size) == kSuccess)) { ... }
return size;                                      // 属性不存在则返回 0
```

所以直接翻了 `meizu15-8plus.adt` 的二进制：

```
0170  00 00 00 00 07 00 00 00 63 68 6F 73 65 6E 00 00   ........chosen..
0180  72 61 6E 64 6F 6D 2D 73 65 65 64 00 00 00 00 00   random-seed.....
01A0  40 00 00 00 09 9B 44 CC 0F 65 8D 58 08 7B EB 16   @.....D.e.X.{...
```

`40 00 00 00` = 64 字节，后面是真实的随机数据（不是全零）。
`PE_get_random_seed()` 会返回 64，`bootseed_init_bootloader()` 不会 panic。**排除。**

顺带确认：`ramdisk` / `trust-cache` / `addon` / `policy` 在 ADT 里都没有，
但 DarwinBoot 只在对应指针非 NULL 时才写那些偏移，我们传的是 NULL，所以无害。

**3. 地址范围 —— 合法**

`gVirtBase = 0xFFFFFFF007004000` 落在
`VM_MIN_KERNEL_ADDRESS (0xFFFFFFE000000000)` 与 `VM_MAX_KERNEL_ADDRESS (0xFFFFFFFBFFFFFFFF)` 之间。

### 还核对过的：boot_args 布局是对的

之前担心 `BootArgs.h` 注释写的是 **Darwin 23** ABI，而我们跑的是 xnu-8796（Darwin 22）。
逐字段核对后确认布局一致：

```c
// DarwinBoot 的 BootArgs.h
UINT16 Revision;  UINT16 Version;  UINT32 Padding;   // 0x00/0x02/0x04
UINT64 VirtualBase;                                  // 0x08
UINT64 PhysicalBase;                                 // 0x10
UINT64 MemorySize;                                   // 0x18
UINT64 TopOfKernelData;                              // 0x20
UINT64 Video[6];                                     // 0x28
```

XNU 8796 的 `pexpert/pexpert/arm64/boot.h`：

```c
uint16_t Revision;  uint16_t Version;                // 0x00/0x02
uint64_t virtBase;                                   // 0x08（编译器自动补 4 字节 padding）
uint64_t physBase;                                   // 0x10
uint64_t memSize;                                    // 0x18
uint64_t topOfKernelData;                            // 0x20
Boot_Video Video;                                    // 0x28
```

`STATIC_ASSERT (OFFSET_OF (DARWIN_BOOT_ARGS, Video) == 0x28)` 与 XNU 一致。
名字不同、偏移相同，二进制兼容。**不是问题。**

### 结论：只能继续埋点

98 行里每个候选都被输入数据排除了，剩下的是 `phystokv()` 和 `early_random()`
两个调用，以及中间的算术。再分析不会有新信息，所以新增四个探针：

| 探针 | 位置 | 切分意义 |
|---|---|---|
| 54 | `gPhysSize` 赋值之后 | 三个字段赋值 + 尺寸算术 |
| 55 | `physmap_base = phystokv(...)` **之前** | 尺寸 panic 检查 |
| 56 | `physmap_base += physmap_slide` **之后** | **夹住 `phystokv` + `early_random`** |
| 57 | `dynamic_memory_begin` 范围检查之前 | `static_memory_end` / `physmap_end` 算术 |

55 与 56 把仅有的两个调用夹在中间。已在本地用真实的 `arm_vm_init.c`
验证过插入位置与幂等性（12 个探针全部到位）。

下一次上机测 **56**：

| 结果 | 含义 |
|---|---|
| `reason 2` | 两个调用都返回了，故障在 56→57 的算术 |
| `reason 0` | `phystokv()` 或 `early_random()` 没回来，55 再分左右 |

## 2026-11-17 22:25 — 56 未命中：只剩 phystokv 和 early_random

```
kernel returned: reason 0, log 192 bytes
checkpoint 56 delta 00000010C900000
DARWINBOOT: checkpoint requested 56
```

### 十一个检测点的结果

| 检测点 | 结果 | 位置 |
|---|---|---|
| 11、34、44、15、16 | `reason 2` | arm_init 各段 |
| 45 | `reason 2` | 进入 `arm_vm_init()` |
| 53、52、51、50 | `reason 0` | arm_vm_init 中段以后 |
| **56** | **`reason 0`** | **`physmap_base += physmap_slide` 之后** |

56 紧跟在 `physmap_base += physmap_slide;` 之后，它没命中，说明内核没走完上面三行：

```c
physmap_base  = phystokv(args->topOfKernelData);                         // 调用 1
physmap_slide = early_random() & ARM64_PHYSMAP_SLIDE_MASK & ~PAGE_MASK;  // 调用 2
assert(physmap_slide < ARM64_PHYSMAP_SLIDE_RANGE);
physmap_base += physmap_slide;
```

### 这是整个 98 行里仅有的两个调用

其余全是 `boot_args` 上的算术，而算术逐项对着面板数值核算过，没有能触发 panic 的情况：

| 项 | 实测/推算 | 阈值 |
|---|---|---|
| `mem_size` | `0x30000000`（768 MB） | 内核地址空间一半，约 60 GB |
| `gVirtBase` | `0xFFFFFFF007004000` | `0xFFFFFFE000000000`..`0xFFFFFFFBFFFFFFFF` |
| `/chosen/random-seed` | 存在，64 字节真实数据 | 需要 64 |

所以嫌疑收敛到 `phystokv()` 和 `early_random()` 两个。

### 下一步：55

55 在 `physmap_base = phystokv(...)` 的上一行。

| 55 的结果 | 含义 |
|---|---|
| `reason 2` | 两个调用之一没回来 —— 下一步在它们之间再插一个探针分清 |
| `reason 0` | 连更前面都没到，用 54 定位尺寸算术 |

### 为什么 early_random 更可疑

它的种子路径结尾是：

```c
rc = ccdrbg_init(&erandom.drbg_info, ..., sizeof(bootseed), bootseed, ...);
if (rc != CCDRBG_STATUS_OK)
    panic("ccdrbg_init() returned %d", rc);
```

而这个阶段的 panic **一个字都打不出来**——`kprintf` 要到 checkpoint 18 附近才起来，
那已经是 `arm_vm_init()` 之后很久了。

### 这一轮顺手解决的两个工程问题

**1. 下载从一小时压到两分钟**

内核 artifact 里混着 dSYM 的 66 MB 调试镜像，总共 160 MB，按 40 KB/s 要跑一小时。
现在 workflow 额外上传一个只含 `kernel.macho.gz` 的 artifact：

```
meizu15-kernel        5.85 MB
meizu15-xnu-hakios-v2  133 MB   （顺带把 dSYM 排除掉了）
```

**2. 注释块替换脚本重复插入 `Request.BootArgs`**

`set_block.py` 会把传入文件整段插到原 `BootArgs` 之前，而我的模板文件末尾
误带了一行 `Request.BootArgs`，导致出现两行（编译会报重复初始化）。
已在脚本里过滤掉含 `Request.BootArgs` 的行，从源头修掉。

## 2026-11-17 23:06 — 54 也未命中：在纯赋值处崩，指向栈

```
kernel returned: reason 0, log 192 bytes
checkpoint 54 delta 00000010C900000
DARWINBOOT: checkpoint requested 54
```

### 十二个检测点

| 检测点 | 结果 |
|---|---|
| 11、34、44、15、16（`arm_init` 各段） | `reason 2` |
| 45（`processor_bootstrap()` 之后、`arm_vm_init()` 之前） | **`reason 2`** |
| 53、52、51、50、56、55 | `reason 0` |
| **54（`gPhysSize = mem_size` 之后）** | **`reason 0`** |

### 为什么这一条特别有信息量

45 命中、54 未命中，两者之间只有：

```c
gVirtBase      = args->virtBase;
gPhysBase      = args->physBase;
real_phys_size = args->memSize;
gPhysSize = mem_size = ((gPhysBase + args->memSize) & ~PAGE_MASK) - gPhysBase;
```

**四个字段读取 + 一次整数算术，没有调用、没有循环、没有分支。**

一个函数死在这里，不是死于「工作没做完」，而是死于「没能进来」。

### 两个可能，第一个更有希望

**A. 栈溢出**

XNU 内核栈默认 `4*4096 = 16 KiB`，而它自己的注释就警告过这件事
（`osfmk/mach/arm/vm_param.h` 第 320 行）：

```c
/*
 * KERNEL_STACK_MULTIPLIER can be defined externally to get a larger
 * kernel stack size. For example, adding "-DKERNEL_STACK_MULTIPLIER=2"
 * helps avoid kernel stack overflows when compiling with "-O0".
 */
# define KERNEL_STACK_SIZE (4*4096*KERNEL_STACK_MULTIPLIER)
```

三条事实叠在一起：

1. 我们的构建是 `KERNEL_CONFIGS=DEVELOPMENT`（`xnu-build.sh` 第 20 行），
   这类配置通常不做激进优化，栈槽不会合并
2. `arm_init()` 把 `boot_args` 的副本（**0x480 = 1152 字节**）放在自己栈上，
   并把它的地址传给 `arm_vm_init()`：
   ```c
   const_boot_args = *args;
   BootArgs = args = &const_boot_args;
   ...
   arm_vm_init(xmaxmem, args);
   ```
3. **45 在 `arm_init` 的帧里跑，命中；54 在 `arm_vm_init` 的帧里跑，静默。**
   越深越崩——这正是栈溢出的特征

**B. `args` 指针失效**

如果指针坏了，1831 行第一次解引用就崩，而 45 在调用之前，仍会命中。
但 `args` 指向的是 `arm_init` 栈上的副本，只要 `arm_init` 的帧还在就有效，
所以这个可能性低于 A。

### 决定：把栈放大四倍，一次分辨两种可能

`KERNEL_STACK_MULTIPLIER` 从 1 改成 4（16 KiB → 64 KiB）。它只影响栈的分配大小，
不改变任何 ABI，风险很低。

加进 `apply_nonapple_guards.py` 作为第 6 处修改，本地已验证锚点唯一、幂等。

启动参数保持 `checkpoint=54`：

| 结果 | 结论 |
|---|---|
| **`reason 2`** | **栈溢出确认**，`arm_vm_init()` 的帧在 16 KiB 上放不下；之后可以把栈回调到 2 倍，或按需保留 4 倍 |
| `reason 0` | 栈不是原因，问题在 `args` 或更早；下一步查 `const_boot_args` 的复制与 `arm_init` 的帧大小 |

## 2026-11-17 23:51 — 把栈放大到 64 KiB 后 54 仍不命中：栈排除

```
kernel returned: reason 0, log 192 bytes
checkpoint 54 delta 00000010C900000
```

`KERNEL_STACK_MULTIPLIER` 从 1 改成 4（16 KiB → 64 KiB），内核字节差异 31 字节
（就是那个常量），确认装进镜像（`0da79ae4…`）。**54 依旧静默。**

### 栈假设排除

原来的推理是：45 在 `arm_init` 的帧里命中，54 深一层就静默，符合栈溢出特征。
四倍栈之后依然如此，所以不是栈。

### 顺手查了两件事，都有结论

**1. 四个全局变量在哪个段**

用 `scripts/ci/macho_map.py` 解析内核 Mach-O：

| 段 | vmaddr | vmsize |
|---|---|---|
| `__TEXT` | `0xFFFFFFF007004000` | 1,703,936 |
| `__DATA_CONST` | `0xFFFFFFF0071A4000` | 966,656 |
| `__TEXT_EXEC` | `0xFFFFFFF007290000` | 8,863,744 |
| `__DATA` | `0xFFFFFFF007B10000` | 4,456,448 |
| `__BOOTDATA` | `0xFFFFFFF007F50000` | 311,296 |
| `__LINKEDIT` | `0xFFFFFFF00802C000` | 2,949,120 |

内核映像整体 `0xFFFFFFF007004000 .. 0xFFFFFFF0082FC000`，19,890,176 字节。

符号位置：

```
gVirtBase       0xFFFFFFF0071B5110   __DATA_CONST
gPhysBase       0xFFFFFFF0071B5100   __DATA_CONST
real_phys_size  0xFFFFFFF0071B5108   __DATA_CONST
gPhysSize       0xFFFFFFF0071B5120   __DATA_CONST
mem_size        0xFFFFFFF007BB8880   __DATA
```

`arm_vm_init` 开头要写的四个变量**全在 `__DATA_CONST`**。

**2. `__DATA_CONST` 在 bootstrap 页表里是不是只读**

查 `osfmk/arm64/proc_reg.h`：

```c
#define ARM_TTE_BOOT_BLOCK \
	(ARM_TTE_TYPE_BLOCK | ARM_TTE_VALID | ARM_TTE_BLOCK_SH(SH_OUTER_MEMORY) | \
	 ARM_TTE_BLOCK_ATTRINDX(CACHE_ATTRINDX_WRITEBACK) | ARM_TTE_BLOCK_AF)
```

**没有 `ARM_TTE_BLOCK_AP(...)`**，AP 字段为 0，即 `AP_RWNA`
（特权级读写、用户级不可访问）。所以 bootstrap 页表对 `__DATA_CONST` 是可写的。

**这条也排除。**

### 所以只剩四个赋值本身

新增三个探针，把 1831–1852 切成四段：

```
cp58  gVirtBase = args->virtBase; 之前     ← 这次测
      gVirtBase = args->virtBase;
      gPhysBase = args->physBase;
cp59  ← 两个赋值之后
      real_phys_size = args->memSize;
cp60  ← 之前
      gPhysSize = mem_size = ((gPhysBase + args->memSize) & ~PAGE_MASK) - gPhysBase;
cp54  ← 之后
```

一次构建，最多四次上机就能定位到单行。

### 58 这一跳改变调查方向

| 结果 | 含义 |
|---|---|
| `reason 2` | 函数体进来了，问题在那四个赋值中的一个，用 59/60/54 精确定位 |
| `reason 0` | **函数体根本没进**——不是赋值的问题，而是调用本身或 prologue。下一步要查 `arm_init()` 怎么传 `xmaxmem` 和 `&const_boot_args` |

## 2026-11-18 00:37 — 58 未命中：函数体没进，根因是中断栈

```
kernel returned: reason 0, log 192 bytes
checkpoint 58 delta 00000010C900000
DARWINBOOT: checkpoint requested 58
```

58 是 `arm_vm_init` 函数体的**第一条语句**（在 `gVirtBase = args->virtBase;` 之前，
注释之后）。它不命中，说明连函数体都没进——**问题不在赋值，而在调用或 prologue。**

### 找到了上一轮栈修复无效的原因

我改的是 `KERNEL_STACK_MULTIPLIER`，它只影响：

```c
# define KERNEL_STACK_SIZE      (4*4096*KERNEL_STACK_MULTIPLIER)
```

这是**线程栈**。而启动阶段根本不用线程栈。

`start.s` 第 498–510 行：

```asm
mov     sp, x0                              // SP_EL0 = intstack_top
...
adrp    lr, EXT(arm_init)@page
add     lr, lr, EXT(arm_init)@pageoff
```

**`arm_init` 跑在中断栈上。** 中断栈的大小是：

```c
#define INTSTACK_SIZE           (4*4096)     // 16 KiB
#define EXCEPSTACK_SIZE         (4*4096)     // 16 KiB
```

### 为什么这解释了一切

这个 16 KiB 要同时装下：

1. `arm_init()` 的帧，其中包含 **`boot_args` 的完整副本，0x480 = 1152 字节**：
   ```c
   const_boot_args = *args;
   BootArgs = args = &const_boot_args;
   ```
2. `arm_vm_init()` 的帧——这是个 580 行的大函数，prologue 一次性开辟的栈空间不小
3. `loader_checkpoint()` → `loader_jump()` 的调用链

**prologue 里 `sub sp, sp, #N` 之后第一次 push 就越过栈底，撞上 guard page，
当场数据中止，函数体一行都没执行。**

这与观察到的每一条现象都对得上：

| 现象 | 解释 |
|---|---|
| 45 命中 | `arm_init` 的帧较浅，还在 16 KiB 内 |
| 58、54、55、56、50–53 全部静默 | 都在 `arm_vm_init` 的帧里，prologue 即溢出 |
| 改 `KERNEL_STACK_MULTIPLIER` 无效 | 那是线程栈，与启动无关 |
| 栈上没有更早的症状 | 溢出点在 guard page 上，直接中止，不写坏别的数据 |

### 修复

`INTSTACK_SIZE` 和 `EXCEPSTACK_SIZE` 各从 `4*4096` 提到 `16*4096`（16 KiB → 64 KiB）。
8 核的话中断栈总量约 512 KiB，在这个窗口里可以接受。

启动参数保持 `checkpoint=58`：

| 结果 | 结论 |
|---|---|
| **`reason 2`** | **中断栈溢出确认**，`arm_vm_init` 的帧在 16 KiB 上放不下；接着连续测 59 / 60 / 54，然后一路往上 |
| `reason 0` | 中断栈也不是原因，转向查 `arm_vm_init` 的符号解析与调用点 |

这一轮如果对了，前面十几轮「越深越静默」的现象会一次性得到解释。

## 2026-11-18 01:37 — 找到根因：设备树地址用了虚拟地址，而那时只能用物理地址

### 58 未命中，栈彻底排除

```
kernel returned: reason 0, log 192 bytes
checkpoint 58 delta 00000010C900000
DARWINBOOT: checkpoint requested 58
```

中断栈放大到 64 KiB（内核尺寸 +98,304 字节，`intstack` 符号落在
`__BOOTDATA 0xFFFFFFF007F88000..0xFFFFFFF007F98000`，正好 64 KiB），
58 依然静默。**栈这条线到此完全排除。**

### 反汇编给出了两个关键事实

`scripts/ci/dis_prologue.py` 解 `arm_vm_init` 的入口：

```
0xFFFFFFF007458C3C  D10603FF  sub sp, sp, #96          ← 帧只有 96 字节
0xFFFFFFF007458C40  A9126FFC  ldp x28, x27, [sp, #288]
...
0xFFFFFFF007458C74  52800740  mov w0, #58
0xFFFFFFF007458C78  940073A3  bl loader_checkpoint     ← 探针确实在二进制里
```

**prologue 只分配 96 字节。**16 KiB 的栈都绰绰有余，之前怀疑栈溢出是错的。
探针也确实被编译进去了。

### 真正的断点：`arm_init` 里 processor_bootstrap 之后那一段

XNU 原版 `osfmk/arm/arm_init.c`：

```c
469  processor_bootstrap();
470
471  if (PE_parse_boot_argn("maxmem", &maxmem, sizeof(maxmem))) {
472      xmaxmem = (uint64_t) maxmem * (1024 * 1024);
473  } else if (PE_get_default("hw.memsize", &memsize, sizeof(memsize))) {   ← 读设备树
474      xmaxmem = (uint64_t) memsize;
475  } else {
476      xmaxmem = 0;
477  }
...
503  nanoseconds_to_absolutetime(XCALL_ACK_TIMEOUT_NS, &xcall_ack_timeout_abstime);
512  #if __ARM_PAN_AVAILABLE__
513      __builtin_arm_wsr("pan", 1);
514  #endif
516  arm_vm_init(xmaxmem, args);
```

探针 45 在 `processor_bootstrap()` 之后，58 在 `arm_vm_init()` 里。
**中间隔着 `PE_get_default("hw.memsize", ...)`，它要遍历设备树。**

### 设备树地址给错了

`pexpert/arm/pe_init.c`：

```c
PE_state.deviceTreeHead = boot_args_ptr->deviceTreeP;     // 原样保存，不转换
```

而虚拟化是**很久以后**才做的，在 `arm_vm_init()` 自己里面
（`osfmk/arm64/arm_vm_init.c` 第 1571 行附近）：

```c
// Device tree (if not locked down), ramdisk, boot args
arm_vm_physmap_slide(temp_ptov_table, end_kern, ..., AP_RWNA, 0);
if (!SecureDTIsLockedDown()) {
    PE_slide_devicetree(temp_ptov_table[ptov_index - 1].va - end_kern);
}
```

`PE_slide_devicetree()` 才是那个「物理地址 + slide = 虚拟地址」的步骤。

**所以 `deviceTreeP` 必须是在切换页表之前就能访问的地址，也就是物理地址。**

而我们一直在填：

```c
Args->DeviceTree = Kernel.Base + TreeAddress - Address;   // KVA
```

### 这解释了整条现象链

| 现象 | 解释 |
|---|---|
| 45 命中 | 它在 `PE_get_default()` 之前 |
| 58 及之后全部静默 | 它们都在 `arm_vm_init()` 里，而内核根本没走到调用 |
| 日志恒为 192 字节 | `do_cacheid()` 之后没有任何东西能输出，panic 也打印不出来 |
| `reason 0` 而非 2 | 数据中止 → panic → `halt_all_cpus()` → `loader_return()` |
| 改栈无效 | 帧只有 96 字节，从来不是栈的问题 |
| 改 `ml_enable_monitor` 无效 | 那个调用在设备树读取之后，根本没执行到 |

### 修复

`DarwinBoot.c` 第 1679 行：

```c
Args->DeviceTree = TreeAddress;      // 物理地址
```

只改这一行，内核不动，需要重建 payload。

| 结果 | 结论 |
|---|---|
| 58 命中 | 根因确认，`arm_vm_init` 第一次真正开始执行；接着连测 59 / 60 / 54，再往上到 55–57 和 50–53 |
| 58 仍静默 | 设备树不是原因，转向 `PE_parse_boot_argn` 与 `nanoseconds_to_absolutetime` |

## 2026-11-18 01:43 — 根因锁定：PAN，ARMv8.0 上没有这个寄存器

### 设备树修复无效

把 `Args->DeviceTree` 从 KVA 改成物理地址（`TreeAddress`）后，58 依然静默：

```
kernel returned: reason 0, log 192 bytes
checkpoint 58 delta 00000010C900000
```

设备树不是（唯一的）原因。回到 `arm_init` 里 45 与 `arm_vm_init` 之间剩下的几行。

### 真凶：`__ARM_PAN_AVAILABLE__`

`hakios.patch` 在 `pexpert/pexpert/arm64/BCM2837.h` 里加了两处：

```c
/*
 * The cores this kernel really runs on (Cortex-A55/A78, QEMU cortex-a76) are
 * ARMv8.2: they set PSTATE.PAN on every exception entry, so the kernel must
 * know to lift it around user memory accesses.
 */
#define __ARM_PAN_AVAILABLE__     1
```

`makedefs/MakeInc.def`：

```
MACHINE_FLAGS_ARM64_BCM2837 = -DARM64_BOARD_CONFIG_BCM2837 \
    -Xclang -target-feature -Xclang +pan
```

**注释自己写明了：这套配置是给 Cortex-A55/A78 用的。**

而 SDM660 的 Kryo 260 是 **4× Cortex-A73 + 4× Cortex-A53，全部 ARMv8.0**。
PAN 是 ARMv8.1 的可选特性，这两个核都没有实现。

于是 `arm_init()` 里：

```c
#if __ARM_PAN_AVAILABLE__
	__builtin_arm_wsr("pan", 1);      // msr pan, #1 —— 未定义指令
#endif
	arm_vm_init(xmaxmem, args);
```

**执行到这一行就触发未定义指令异常。**

### 为什么这能解释全部二十轮的现象

| 现象 | 解释 |
|---|---|
| 45 命中 | 它在 `msr pan` 之前 |
| 58、54、55、56、50–53 全静默 | 都在 `arm_vm_init()` 里，而内核根本没走到调用 |
| 日志恒为 192 字节 | `do_cacheid()` 的 printf 是早于这里的最后输出；异常在这里 panic，`kprintf` 还没起来 |
| `reason 0` 而非 2 | panic → `halt_all_cpus()` → `loader_return()` |
| 反汇编显示帧只有 96 字节 | 从来不是栈的问题 |
| 改 `KERNEL_STACK_MULTIPLIER`、`INTSTACK_SIZE` 无效 | 同上 |
| 改 `ml_enable_monitor` 无效 | 那个调用在 `arm_vm_init()` 里，够不着 |
| 改设备树地址无效 | 那只是同一条路径上更早的另一个隐患，但不是这次的断点 |

### 修复

新增 `scripts/ci/disable_pan.py`，两处一起改：

1. `BCM2837.h`：`__ARM_PAN_AVAILABLE__` 1 → 0
2. `MakeInc.def`：移除 `-Xclang -target-feature -Xclang +pan`

只关宏不够——编译选项还在的话，编译器仍会为其它路径生成 pan 指令。
脚本已接入 workflow 的补丁步骤，并在本地用真实文件验证过锚点与幂等。

启动参数保持 `checkpoint=58`。

## 2026-11-18 02:07 — 突破：PAN 就是那堵墙

```
kernel returned: reason 2, log 0 bytes
checkpoint 58 delta 00000010C900000
DARWINBOOT: checkpoint requested 58
```

**`reason 2`。二十轮以来 `arm_vm_init()` 第一次真正开始执行。**

### 修复内容回顾

`hakios.patch` 为它的目标核（Cortex-A55/A78，ARMv8.2）在
`pexpert/pexpert/arm64/BCM2837.h` 里开了 PAN：

```c
#define __ARM_PAN_AVAILABLE__     1
```

并在 `makedefs/MakeInc.def` 里要求编译器生成 pan 指令：

```
MACHINE_FLAGS_ARM64_BCM2837 = ... -Xclang -target-feature -Xclang +pan
```

而 SDM660 的 Kryo 260 是 4× Cortex-A73 + 4× Cortex-A53，**全部 ARMv8.0**，
PAN 是 ARMv8.1 的可选特性，这两个核没有实现。

`arm_init()` 里 `__builtin_arm_wsr("pan", 1)` 于是成为一条未定义指令，
位置恰好在探针 45 之后、`arm_vm_init()` 之前。

新增 `scripts/ci/disable_pan.py` 两处一起改（只关宏不够，编译选项还在的话
编译器仍会为其它路径生成 pan 指令）。

### 二进制层面的确认

扫描两个内核里 `MSR (immediate)` 家族指令：

| 内核 | `0xD500409F` | `0xD500419F` |
|---|---|---|
| PAN 开启 | 9 处 | 15 处 |
| PAN 关闭 | 0 | 0 |

**共 24 条 `msr pan` 全部消失。**

### 一个副作用观察

日志从 192 字节变成 **0 字节**。192 字节是 `do_cacheid()` 写进 `msgbuf` 的两行；
现在内核走到了比那更远的位置，`msgbuf` 被重新初始化，所以读到空。

### 下一步

**此前 46–57 的全部测量作废**——它们都是在 PAN 指令还在的时候做的，
`reason 0` 与探针位置无关。

不再逐个往上走，直接测最远的 **49**（`pmap_bootstrap()` 之后，接近
`arm_vm_init()` 末尾）：

| 结果 | 含义 |
|---|---|
| `reason 2` | `arm_vm_init()` 完整跑完，那堵墙从头到尾只是 PAN |
| `reason 0` | 中段还有别的故障，用 53 二分 |

探针时间顺序：58 → 59 → 60 → 54 → 55 → 56 → 57 → 50 → 51 → 52 → 53 → 46 → 47 → 48 → 49

## 2026-11-20 23:55 — 对齐的真正来源：不是 Policy，是 return stub 的 0x100 尾巴

卡点回顾：第二次 slide 的 `orig_va`（= `args->topOfKernelData - gPhysBase + gVirtBase`）
不对齐，探针 113 确认。所有推算都说应该对齐，因为推算只算到 `Layout->Used`。

### 漏掉的最后一跳

`DarwinBoot.c` 在 `PlaceImage()` 之后还有一段：把 return stub 抄进内核窗口，
并把 `Used` 扩展到覆盖它：

```c
StubOffset = ALIGN_VALUE (Used, 16);
StubSpan   = ParameterOffset + 24;      // 0xE8 + 24 = 0x100，大于 StubSize 0xF0
...
Used = StubOffset + ALIGN_VALUE (StubSpan, 16);   // ← 只对齐到 16
```

`Layout->Used` 是 16 KiB 对齐的（`ALIGN_VALUE(..., SIZE_16KB) - Address`，
`Address` 也是 16 KiB 倍数），但 `StubOffset` 同样是 16 KiB 倍数，
再加上只对 16 对齐的 `StubSpan = 0x100`，最终

```
TopOfKernelData = Address + Used      → 低 12 位 = 0x100
orig_va         = Used + gVirtBase    → 低 12 位 = 0x100  ≠ ARM_PGMASK+1
```

`ARM_PGMASK = 0xFFF`（4 KiB），16 KiB 配置下是 `0x3FFF` —— `0x100` 两种都不算对齐。

### 数值复算（本地用真实段表算的）

`Kernel.Base = 0xFFFFFFF007004000`、`Kernel.End = 0xFFFFFFF008314000`
（`scripts/ci/macho_map.py inputs/kernel.macho`），屏幕 `WIN D0004000` 即 `Address`：

| Policy 取值 | Used（旧） | orig_va & 0xFFF | orig_va & 0x3FFF | Used（新） | 掩码后 |
|---|---|---|---|---|---|
| 0xD1314000 | 0x1310100 | **0x100** | **0x100** | 0x1314000 | 0 |
| 0xD2000000 | 0x1FFC100 | **0x100** | **0x100** | 0x2000000 | 0 |
| 0xD4000000 | 0x3FFC100 | **0x100** | **0x100** | 0x4000000 | 0 |
| 0xD8000000 | 0x7FFC100 | **0x100** | **0x100** | 0x8000000 | 0 |

`Policy` 怎么变，残差恒为 `0x100`。所以这不是设备树、不是 ramdisk 大小、
不是 `gPhysBase` 换算错——是 stub 尾巴。也解释了为什么"推算三遍都说对齐，
实测三遍都说不齐"：推算是从 `Layout->Policy` 一路 `ALIGN_VALUE` 到 `Layout->Used`，
恰好停在 `Used` 被改写的那一行之前。

### 修复

`edk2-msm-overlay/DarwinBootPkg/Driver/DarwinBoot.c`，stub 分支末尾：

```c
Used = ALIGN_VALUE (StubOffset + StubSpan, SIZE_16KB);   // 原来是 ALIGN_VALUE (…, 16)
```

- 内核仍然不会把 stub 当空闲内存发出去（stub 仍在 `Used` 之内）
- `TopOfKernelData` 回到页粒度边界，`orig_va` 对齐断言与 `temp_ptov_table[].va`
  断言同时满足（后者是同一根因的第二个症状）
- `HeadSize` 那边有 `DARWIN_WINDOW_RESERVE` ≥ 32 MiB 的余量，多出的 ≤16 KiB 无害

### 修复后该验证什么

断言已改成探针 112 + ptov_index（`apply_nonapple_guards.py`），不会 panic，
所以一次启动能给两件事：

1. `checkpoint=89`（第二次 slide 的 `len` 计算之后）→ `reason 2` 说明第二次 slide 走完，
   接着测 **62 / 67** 看 `arm_vm_physmap_init()` 是否整体通过
2. 同一次启动里如果 `112 + 1` 命中，说明还有别的未对齐来源，那就在这个点上继续查

镜像需要重编 payload：`DarwinBoot.c` 在 `edk2-msm-overlay/**` 下，
`build-meizu15-uefi-payload.yml` 的 `paths` 会自己触发；内核**不用重编**
（`inputs/kernel.macho` 未变）。
## 2026-11-21 00:12 — 89 命中：第二次 slide 走到了 len 之后

修复版 payload（`8efe55e`）刷上机，屏幕：

```
kernel returned: reason 2, log 0 bytes
slot magic 545352444C554E58  stub 00000000D131C000
checkpoint 89 delta 00000010C900000
WIN D0004000 HIGH D0004000 RUN ADFA0000 @ D0000000
```

### 能确定的三件事

1. **`reason 2`** —— `checkpoint=89`（第二次 slide 的 `len` 计算之后）命中了。
   旧内核在这一趟里 85 / 87 / 89 一个都不亮。
2. **`stub 0xD131C000`** → `StubOffset = 0xD131C000 - 0xD0004000 = 0x1318000`
   = `Layout->Used`，与按 `Policy` 推出的值一致（Args 0xD1314000 → Tree…Policy 0xD131C000）。
   也就是说修复后的 `Used = ALIGN(0x1318000 + 0x100, 16K) = 0x131C000`，
   屏幕这一行正是 stub 自己的地址，不是 `TopOfKernelData`。
3. **`WIN D0004000`** —— `Address` 确认是 0xD0004000，于是

   | | orig_va | & 0xFFF |
   |---|---|---|
   | 修复前（Used 0x1318100） | 0xFFFFFFF008321100 | **0x100** |
   | 修复后（Used 0x131C000） | 0xFFFFFFF008320000 | **0** |

### 还确定不了的一件事

探针 112（`orig_va & ARM_PGMASK != 0`）如果真的触发，屏幕上**也是这四行**
—— 探针只让 `loader_checkpoint` 停住，不改变 launcher 请求的 `checkpoint=89`
和 `delta`。所以从这一张面板分不出「89 真命中」和「112 先命中」。

### 所以下一轮直接测 62

`checkpoint=62`（`arm_vm_physmap_init(args)` 之后）在第二次 slide 的外面。
62 命中 = 两次 slide + 三次 `arm_vm_page_granular_prot()` 全部走完、
112 从未触发；62 不命中 = 还在 slide 里，用 67 → 66 → 65 倒着二分。

launcher 里 `Request.BootArgs` 已从 89 改成 62（只改这一行，注释同步）。
## 2026-11-21 00:27 — 62 命中：`arm_vm_physmap_init()` 整体通过

```
kernel returned: reason 2, log 0 bytes
slot magic 545352444C554E58  stub 00000000D131C000
checkpoint 62 delta 00000010C900000
```

`checkpoint=62` 是 `arm_vm_physmap_init(args)` **之后**的第一条。它命中意味着：

1. 第一次 slide（`gVirtBase`..`segLOWEST`）完成
2. `arm_vm_page_granular_RWNX()` 完成
3. 第二次 slide（`topOfKernelData`..`real_avail_end`，约 752 MB）完成
4. 三次 `arm_vm_page_granular_prot()` 全部完成
5. **探针 112 从未触发** —— 若 `orig_va` 仍不对齐，内核会停在 slide 里，走不到 62

所以 5.3 的修复（`Used` 圆整到 SIZE_16KB）是成立的，物理映射这一段**结束**。

### 作废的旧测量

62 / 67 / 81 / 85 / 87 / 89 这几个"未命中"都是在 **PAN 还没修**、且**对齐还没修**的时候测的，
它们不说明移植停在哪里。现在按新基线重排：45 → 58 → 61 → 65 → 66 → 68-71 → 83 → 89 → **62** 全部可达。

### 下一站：49

不再逐个往上走，直接跳到剩下的最远点 **49**（`pmap_bootstrap()` 之后，`arm_vm_init()` 收尾前）：

| 结果 | 含义 |
|---|---|
| `reason 2` | `arm_vm_init()` 基本跑完；接着补 48 → 47，再往 `arm_init()` 尾部走 |
| `reason 0` | 62 与 49 之间有故障（`kva_active`、`pmap_bootstrap`、段处理），用 48 → 47 二分 |
## 2026-11-21 00:53 — 49 未命中：窗口缩到 physmap_init 之后、pmap_bootstrap 之前

```
kernel returned: reason 0, log 192 bytes
checkpoint 49 delta 00000010C900000
--- kernel message buffer ---
[   49.003290]: vm_cache_geometry_colors: 16
[   49.003303]: do_cacheid() - 65536 bytes Unknown cache (I:32768 D:32768 (separate)), 16-way assoc, 64 bytes/line
```

`reason 0` = `halt_all_cpus()` → `loader_return()`，日志还是那 192 字节的 `do_cacheid()`
两行 —— `kprintf` 还没起来，所以在 62 与 49 之间有一次静默故障（数据中止一类的老症状）。

### 新基线

| 探针 | 位置 | 结果 |
|---|---|---|
| 61 | `arm_set_user_tbi()` 之后 | 命中（PAN 修复后） |
| **62** | `arm_vm_physmap_init(args)` 之后 | **命中（本轮之前）** |
| 63 | `set_mmu_ttb_alternate(...)` 之后 | 未测 |
| 64 | `set_mmu_ttb(invalid_ttep)` 之后 | 未测 |
| **47** | `kva_active = TRUE` 之后 | **本轮要测** |
| 48 | `pmap_bootstrap()` 之前 | 未命中过（旧测量作废） |
| **49** | `pmap_bootstrap()` 之后 | **未命中（本轮）** |

### 头号嫌疑：`set_mmu_ttb (invalid_ttep)`

那一步把 TTBR0 换成只含无效表项的页表，**当前内核 VA 的映射就没了**。
XNU 走这一步是为了在建立最终页表期间强制走物理/identity 访问；之后任何一句
还按 VA 取全局变量的代码都会立刻数据中止 —— 而 SCTLR 的 MMU 仍然开着，
所以异常向量本身也可能取不到指令，`kprintf` 更没有机会打印。

这也是为什么 68–71（`granular_prot` 内部）都还能命中、62 也命中，
而到 49 就没声音了：窗口正好夹着 63 / 64。

### 另一个记下来的观察（暂不追）

日志时间戳是 `[49.003290]` —— 内核起来不到一秒却报 49 秒。时间基数换算
（`nanoseconds_to_absolutetime` / boot_args 的 `TimebaseFrequency`）可能有偏差，
但它不影响当前二分，先记着。

### 下一轮

`checkpoint=47`：命中说明 63/64 都过去了，故障在 `kva_active`..`pmap_bootstrap`；
不命中说明故障就在 63/64 附近，再测 64 二分。
## 2026-11-21 01:06 — 47 命中：窗口只剩 `pmap_bootstrap()`

```
kernel returned: reason 2, log 0 bytes
slot magic 545352444C554E58  stub 00000000D131C000
checkpoint 47 delta 00000010C900000
```

`checkpoint=47`（`kva_active = TRUE` 之后）命中。结合上一轮：**47 通、49 不通**。

于是 63 / 64（`set_mmu_ttb_alternate`、`set_mmu_ttb(invalid_ttep)`）都**过了** —— 那个
"撤掉当前 VA 映射"的嫌疑被排除掉一半，至少它自己没当场崩。故障落在
`kva_active = TRUE` 之后、`pmap_bootstrap()` 返回之前这一小段。

### 下一轮：48

`48` 是 `pmap_bootstrap()` 调用之前那句：

| 结果 | 含义 | 再下一步 |
|---|---|---|
| `reason 2` | 进得去 `pmap_bootstrap()`，崩在里面 | 读 `pmap_bootstrap` 源码，按它内部的点数继续埋（`pmap_allocate` / `pmap_init` / 页表切换） |
| `reason 0` | 崩在 `kva_active` 与调用之间那几行 | 那几行的反汇编 + 逐条对照 |

### 现在这条链已经很长了

`45 → 58 → 61 → 62 → 47` 全通，`49` 卡住。相对 PAN 那堵墙之前的"全部静默"，
现在每次启动都能往前挪一个真实的位置，说明探针+payload 这套迭代是有效的。
## 2026-11-21 01:18 — 48 命中：崩在 `pmap_bootstrap()` 内部

```
kernel returned: reason 2, log 0 bytes
checkpoint 48 delta 00000010C900000
```

`48`（`pmap_bootstrap(dynamic_memory_begin)` 之前那句）命中，`49`（之后）静默 ——
窗口从"十几行"缩到"一个函数体"。payload 二分到此为止，剩下只能进内核埋点。

### 窗口里有什么

`arm_vm_init.c` 2151–2247（47 之后、48 之前）这段，除了赋值只有三样东西值得怀疑：

```c
arm_vm_kernelcache_phys_start = segLOWEST - gVirtBase + gPhysBase;   /* 纯算术 */
arm_vm_kernelcache_phys_end   = end_kern  - gVirtBase + gPhysBase;
if (arm_vm_auxkc_init()) { ... }                                      /* 没有 AuxKC，应返回 false */
pmap_bootstrap(dynamic_memory_begin);
```

而 `pmap_bootstrap()` 内部（`osfmk/arm/pmap/pmap.c:2095`）：

```c
pmap_data_bootstrap();   /* pv_head_table / pp_attr_table，从 avail_start 分配 */
uat_bootstrap();         /* 读设备树里的 UAT / IOMMU 节点 */
sart_bootstrap();        /* 读设备树里的 SART 节点 */
avail_start = PMAP_ALIGN(avail_start, __alignof(bitmap_t));
...                      /* 之后是一大段 L3 页表初始化 */
```

`uat_bootstrap()` / `sart_bootstrap()` 依赖 ADT 里真有对应节点。这份
`meizu15-8plus.adt` 是从真机 dump 出来的，但节点形状是否满足 XNU 的期望，
此前从没验证过。

### 新探针 90–95（`apply_nonapple_guards.py`）

在 `osfmk/arm/pmap/pmap.c` 三处 bootstrap 两侧各插一枚：

| 探针 | 位置 |
|---|---|
| 90 / 91 | `pmap_data_bootstrap()` 前 / 后 |
| 92 / 93 | `uat_bootstrap()` 前 / 后 |
| 94 / 95 | `sart_bootstrap()` 前 / 后 |

脚本里新增 `PMAP_PROBES` / `PMAP_EXPECTED` / `add_pmap_probes()`，`main()` 里加了
`find(root, "osfmk/arm/pmap/pmap.c")` 与验证段。**本地用真 `pmap.c` 跑过**：
三对探针都落在正确位置（2183/2185、2190/2192、2198/2200），第二遍运行零改动。

### 判读

| 命中 | 结论 |
|---|---|
| 90 命中、91 静默 | `pmap_data_bootstrap()` 自己崩 —— `avail_start` 算术 / `pv_head_table` 大小 |
| 92 命中、93 静默 | `uat_bootstrap()` —— UAT 节点缺失或形状不对 |
| 94 命中、95 静默 | `sart_bootstrap()` —— SART 节点同上 |
| 91/93/95 全通 | 崩在 `pmap_bootstrap()` 后半段（L3 页表那段 `while (va_l1 < va_l1_end)`） |

launcher 同步改成 `checkpoint=90`；内核需要重建（这是自 PAN 修复以来第一次动内核），
所以这一轮要走 `build-meizu15-xnu-hakios-v2` → 替换 `inputs/kernel.macho` → payload。