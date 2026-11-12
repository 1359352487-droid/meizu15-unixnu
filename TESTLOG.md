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
