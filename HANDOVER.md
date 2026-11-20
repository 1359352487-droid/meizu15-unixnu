# 交接记录 — Meizu 15 (M8810) 移植 iOS 16 内核

> 更新于 2026-11-20 23:55。这份文档的目的：**换一台电脑、换一个 agent，能从这个仓库继续干下去，不用重新摸一遍。**
>
> 旧的 `HANDOVER.md`（2026-11-12）记录的是"造出一个能 `fastboot boot` 的 UEFI 镜像"那个早期阶段，已不适用，本次重写。

---

## 0. 一句话状态

内核**已经能启动并跑到 `arm_vm_init()` 深处**（远超一个月前"能进 UEFI"的阶段）。最后一道墙 `PAN` 已推倒；第二次 slide 的对齐断言也**已经查到根因并改掉了源码**——不是 `Policy` 算术，是 return stub 尾巴上的 `0x100`（见 5.3）。现在等一次 payload 构建 + 刷机验证。

---

## 1. 目标

在 **魅族 15（M8810，骁龙 660 / SDM660，Kryo 260 = 4×Cortex-A73 + 4×Cortex-A53，全部 ARMv8.0）** 上启动 **iOS 16 的 XNU 内核**（xnu-8796.121.2 血统）。

**这不是全功能移植**：没有 SDM660 的存储、显示、触摸、基带驱动。目标是把内核引导到能自己打印日志、建立 VM、启动第一个内核线程的程度。用户态、launchd、图形栈都不在范围内。

---

## 2. 硬件与工具链事实（都已实测确认）

| 项 | 值 |
|---|---|
| SoC | SDM660，Kryo 260，**ARMv8.0**（这是关键——很多 ARMv8.1+ 的特性它没有） |
| 内核配置 | `MACHINE_CONFIGS=BCM2837`，**4 KiB 页**，`KERNEL_CONFIGS=DEVELOPMENT` |
| 设备序列号 | `881QAESM95THY`，`getvar product` → `M1881` |
| 仓库 | `1359352487-droid/meizu15-unixnu`，分支 `main` |
| token | `.gh-token`（fine-grained PAT，会过期，过期后 API 返回 401） |
| 内核源码 | `oosexgang/hakios-xnu-kernel`（HakiOS 打过补丁的 xnu-8796） |
| boot_args | `Darwin 23` 布局，`sizeof == 0x480`，与 XNU 的 camelCase 版本**偏移完全一致**（`Video` 都在 0x28），命名差异不是 bug |

**面板实测寄存器值**（每次启动都会打印，很有用）：

```
CurrentEL 1   SCTLR_EL1 0xC0180D   TCR_EL1 0x280803518   (t0sz = 24)
ID_AA64MMFR0_EL1 0x1122  (PARange 2, 4 KiB granule supported)
ID_AA64PFR0_EL1  0x1000222
WIN D0004000 HIGH D0004000 RUN ADFA0000 @ D0000000
```

---

## 3. 当前进度：探针结果表

调试手段是在源码里插 `loader_checkpoint(N)`，启动时传 `checkpoint=N`。**执行到就停下并报 `reason 2`；没执行到就报 `reason 0`。**

### 3.1 已命中（`reason 2`）——这些位置确认可达

| 探针 | 位置 |
|---|---|
| 45 | `arm_init.c`：`processor_bootstrap()` 之后、`arm_vm_init()` 之前 |
| 46 | `arm_vm_init.c`：`arm_set_user_tbi()` 之前 |
| 52 | 第一个 `init_ptpages()` 之后 |
| **58** | **`arm_vm_init()` 函数体第一条语句**（PAN 修复后才命中） |
| 61 | `arm_set_user_tbi()` 之后（TBI 没问题） |
| 65 | `arm_vm_physmap_init()` 里第一次 slide 之后 |
| 66 | `arm_vm_page_granular_RWNX()` 之后 |
| 68 / 69 / 70 / 71 | `arm_vm_page_granular_prot()` 内部（入口/循环前/循环后/defer 前）——**注意：这三个函数被调用三次，固定编号无法区分是哪次** |
| **83** | **第二次 slide 进入 `arm_vm_physmap_slide()` 函数体** |
| **113** | **第二次 slide 的 `orig_va` 确实不对齐**（`orig_va & ARM_PGMASK != 0`） |

### 3.2 未命中（`reason 0`）——这些位置没走到

| 探针 | 位置 |
|---|---|
| 47 | `kva_active = TRUE` 之后 |
| 48 / 49 | `pmap_bootstrap()` 前后 |
| 50 / 51 / 53 | `boot_tte` 赋值前 / `init_ptpages` 前 / `arm_vm_prot_init` 后 |
| 54 / 55 / 56 / 57 | physmap 计算各点 |
| 62 | `arm_vm_physmap_init(args)` 之后 |
| 64 | `set_mmu_ttb(invalid_ttep)` 之后 |
| 67 | 第二次 slide 之后 |
| 81 | 第二次 slide 调 `arm_vm_page_granular_prot()` 之前 |
| 85 / 87 / 89 | 第二次 slide 内的 `orig_va` 断言后 / `va` 断言后 / `len` 计算后 |

**59 / 60 / 63 从未测过。**

### 3.3 索引探针（区分第几次调用）

`arm_vm_page_granular_prot()` 和 `arm_vm_physmap_slide()` 都被调用多次，所以部分探针写成 `loader_checkpoint(N + ptov_index)`：

- `ptov_index == 0` → 第一次 slide（`gVirtBase`..`segLOWEST`，几 MB）
- `ptov_index == 1` → 第二次 slide（`topOfKernelData`..`real_avail_end`，**约 752 MB**）

---

## 4. 已解决的问题

### 4.1 PAN —— 那道墙（最重要的发现）

**症状**：`arm_init()` 的探针 45 命中，但 `arm_vm_init()` 里所有探针全部静默，日志恒为 192 字节（只有 `do_cacheid()` 两行）。

**根因**：`hakios.patch` 在 `pexpert/pexpert/arm64/BCM2837.h` 里写了：

```c
/*
 * The cores this kernel really runs on (Cortex-A55/A78, QEMU cortex-a76) are
 * ARMv8.2: they set PSTATE.PAN on every exception entry, ...
 */
#define __ARM_PAN_AVAILABLE__     1
```

并在 `makedefs/MakeInc.def` 里让编译器生成 pan 指令：

```
MACHINE_FLAGS_ARM64_BCM2837 = -DARM64_BOARD_CONFIG_BCM2837 -Xclang -target-feature -Xclang +pan
```

**HakiOS 的目标是 Cortex-A55/A78（ARMv8.2），而我们的 Kryo 260 是 ARMv8.0，没有 PAN 寄存器。** `arm_init()` 里那句 `__builtin_arm_wsr("pan", 1)` 是**未定义指令**，位置恰好在探针 45 之后、`arm_vm_init()` 之前。异常 → panic → `halt_all_cpus()` → `loader_return()` → 屏幕显示 `reason 0`，而 `kprintf` 还没起来，什么也打不出来。

**修复**：`scripts/ci/disable_pan.py`，两处一起改（只关宏不够，编译选项还在的话编译器仍会生成 pan 指令）：

1. `BCM2837.h`：`__ARM_PAN_AVAILABLE__` 1 → 0
2. `MakeInc.def`：移除 `-Xclang -target-feature -Xclang +pan`

**二进制验证**：扫描两个内核里 `MSR (immediate)` 家族指令：

| 内核 | `0xD500409F` | `0xD500419F` |
|---|---|---|
| PAN 开启 | 9 处 | 15 处 |
| PAN 关闭 | 0 | 0 |

**共 24 条 `msr pan` 全部消失。** 修复后 `checkpoint=58` 立刻拿到 `reason 2`。

### 4.2 设备树地址（真 bug，但不是卡点）

`DarwinBoot.c` 原来写：

```c
Args->DeviceTree = Kernel.Base + TreeAddress - Address;   // KVA
```

但 `arm_vm_init()` 之前内核跑在 V=P 模式下，KVA 不可访问。XNU 的约定是**先给物理地址**，虚拟化由 `arm_vm_init()` 内部自己做：

```c
// osfmk/arm64/arm_vm_init.c，约 1571 行
arm_vm_physmap_slide(temp_ptov_table, end_kern, ...);
if (!SecureDTIsLockedDown()) {
    PE_slide_devicetree(temp_ptov_table[ptov_index - 1].va - end_kern);
}
```

已改为 `Args->DeviceTree = TreeAddress;`。这修掉了一个真 bug，但**没有**解决当时的卡点（说明另有原因）。

### 4.3 FAT12 读取

镜像的 FAT 卷声称 FAT16 但实际是 FAT12（FAT 大小 12 扇区）。`fat_ls.py` 读 16 位表项会得到 `fat_entry(6) = 0xA009`（40969），链在 5 个簇后断裂，15.8 MB 的内核只能读出 8192 字节。已用 `scripts/ci/fat12_read.py`（12 位打包：`base = fat_off + cluster + cluster//2`）替换。

---

## 5. 曾经的卡点（根因已定位，见 5.3）

**`arm_vm_physmap_init()` 里的第二次 slide，映射约 752 MB 物理内存时，`orig_va` 不对齐。**

```c
arm_vm_physmap_slide(temp_ptov_table,
    args->topOfKernelData - gPhysBase + gVirtBase,      // ← orig_va，实测不对齐
    real_avail_end - args->topOfKernelData,             // ← 约 752 MB
    AP_RWNA, ARM64_GRANULE_ALLOW_BLOCK | ARM64_GRANULE_ALLOW_HINT);
```

导致 `arm_vm_physmap_slide()` 里连续两个断言失败：

```c
assert((orig_va & ARM_PGMASK) == 0);              // 探针 113 确认这条失败
assert((temp_ptov_table[ptov_index].va & ARM_PGMASK) == 0);   // 同一根因的第二个症状
```

### 5.1 为什么这个值理论上应该对齐

```
orig_va = topOfKernelData - gPhysBase + gVirtBase
        = (Address + Used) - Address + gVirtBase     ← gPhysBase 就是 Address，抵消
        = Used + gVirtBase
```

- `Used = ALIGN_VALUE(Layout->Policy + PolicySize, SIZE_16KB) - Address`
- 传给 `PlaceImage()` 的 `Address` 是 `BLOCK_SIZE + (Kernel.Base & (BLOCK_SIZE - 1))` = `0x204000`，本身是 `SIZE_16KB` 的整数倍
- `gVirtBase = 0xFFFFFFF007004000`，末尾 `0x4000`
- `ARM_PGMASK = ARM_PGBYTES - 1`（4 KiB 配置下 `0xFFF`，16 KiB 下 `0x3FFF`）——**两种掩码下 `0x4000` 都算对齐**

**推算三遍都说对齐，实测三遍都说不齐。** 说明我读源码时有一个前提是错的，但靠读源码找不出来。

### 5.2 已经试过但失败的测量手段

想用 `loader_stub_slot[3]`（屏幕上显示为 `delta` 的那个字）把 `orig_va` 的值传出来：

```c
loader_stub_slot[3] = orig_va;
loader_checkpoint(112 + ptov_index);
```

**结果：内核完全不返回。** 屏幕停在进入内核前的画面，没有 `kernel returned` 那一行。说明该槽虽然只被打印成 delta，但 **loader 的返回路径依赖它**，覆盖就破坏了返回机制。**这个通道不可用。**

### 5.3 根因：`Used` 被 return stub 的尾巴破坏（2026-11-20 定位）

推算错在一个前提上：`TopOfKernelData = Address + Used` 里的 `Used` **不是** `PlaceImage()` 算出来的那个
16 KiB 对齐值。`DarwinBoot.c` 在 `PlaceImage()` 之后还要把 return stub 抄进内核窗口，并把它计入 `Used`：

```c
StubOffset = ALIGN_VALUE (Used, 16);
StubSpan   = ParameterOffset + 24;                 // 0xE8 + 24 = 0x100 > StubSize 0xF0
...
Used = StubOffset + ALIGN_VALUE (StubSpan, 16);    // 只对齐到 16，不是页粒度
```

于是 `TopOfKernelData` 的低 12 位恒为 `0x100`，`orig_va = Used + gVirtBase` 同样带 `0x100`，
`ARM_PGMASK`（4 KiB 下 `0xFFF`，16 KiB 下 `0x3FFF`）两种都过不了。
**残差与 `Policy` / ramdisk / `gPhysBase` 完全无关，恒为 `0x100`** —— 这正好解释了 5.1 里
"推算三遍都说对齐，实测三遍都说不齐"：推算停在 `Layout->Used`，恰好是 `Used` 被改写的前一行。

用真实段表（`scripts/ci/macho_map.py inputs/kernel.macho`：`Base 0xFFFFFFF007004000`、
`End 0xFFFFFFF008314000`）与屏幕上的 `WIN D0004000` 复算：

| Policy | Used 旧 | orig_va & 0xFFF | Used 新 | 掩码后 |
|---|---|---|---|---|
| 0xD1314000 | 0x1310100 | **0x100** | 0x1314000 | 0 |
| 0xD2000000 | 0x1FFC100 | **0x100** | 0x2000000 | 0 |
| 0xD4000000 | 0x3FFC100 | **0x100** | 0x4000000 | 0 |
| 0xD8000000 | 0x7FFC100 | **0x100** | 0x8000000 | 0 |

**修复**（`edk2-msm-overlay/DarwinBootPkg/Driver/DarwinBoot.c`，stub 分支末尾）：

```c
Used = ALIGN_VALUE (StubOffset + StubSpan, SIZE_16KB);   // 原：ALIGN_VALUE (…, 16)
```

stub 仍在 `Used` 之内（内核不会把它当空闲内存发出去），边界回到页粒度；
`temp_ptov_table[].va` 那条断言是同一根因的第二个症状，一并解决。
`HeadSize` 一侧有 `DARWIN_WINDOW_RESERVE` ≥ 32 MiB 的余量，多出 ≤16 KiB 无害。

日志细节见 `TESTLOG.md` 2026-11-20 23:55。

---

## 6. 放弃的方案（及原因）

| 方案 | 结论 | 依据 |
|---|---|---|
| `KERNEL_STACK_MULTIPLIER` 1→4 | **无效** | 它只影响**线程栈**（`KERNEL_STACK_SIZE`），而启动阶段跑在**中断栈**上 |
| `INTSTACK_SIZE` / `EXCEPSTACK_SIZE` 4×4096→16×4096 | **无效** | 内核因此大了 98,304 字节（证明改动生效），但探针结果不变。反汇编 `arm_vm_init` 入口发现 **prologue 只分配 96 字节**——16 KiB 的栈绰绰有余，从来不是栈的问题 |
| `ml_enable_monitor()` 加 `#if XNU_MONITOR` 守卫 | **无效** | BCM2837 配置里 `XNU_MONITOR` 未定义（= 0），该调用根本不编译；即使编译，它也在 `arm_vm_init()` 内部，够不着当时的卡点 |
| 怀疑 `boot_args` 字段名 PascalCase vs camelCase 不匹配 | **排除** | `BootArgs.h` 里的 `STATIC_ASSERT` 证明偏移完全一致（`Video` 都在 0x28），二进制兼容 |
| 怀疑 `/chosen/random-seed` 缺失导致 `bootseed_init` panic | **排除** | 直接翻 `meizu15-8plus.adt` 二进制，`random-seed` 属性存在且是真实 64 字节数据 |
| 怀疑 `PTOV_TABLE_SIZE`（=8）溢出 | **排除** | 非 MONITOR 分支只调用 2 次 `arm_vm_physmap_slide`，远小于 8 |
| 怀疑 `avail_start` 未初始化 | **排除** | 它在 1940 行初始化，早于 2143 行的调用点 |
| 怀疑 `TopOfKernelData` 偏小（1.19 MB vs 内核 19 MB） | **误判已纠正** | `0x1300F0` 只是 `DarwinBoot.c` 注释里的示例数字，不是实测值；`PlaceImage()` 第 1024 行 `Layout->Args = Address + Kernel->End - Kernel->Base` 已经包含内核大小 |
| 用 `loader_stub_slot[3]` 传值出来 | **不可用** | 破坏 loader 返回路径，内核不再返回 |

---

## 7. 下一步（三条路，建议并行考虑）

### 7.1 短期：重建 payload，验证 5.3 的对齐修复（下一步就干这个）

`DarwinBoot.c` 已改（`Used = ALIGN_VALUE (StubOffset + StubSpan, SIZE_16KB)`），
**内核不用重编**（`inputs/kernel.macho` 没动），只走 payload：

```powershell
# 推 DarwinBoot.c 的改动 → build-meizu15-uefi-payload.yml 自动触发（paths: edk2-msm-overlay/**）
# 约 8 分钟，下载 meizu15-uefi-payload-debug-full（5.2 MB）
& "$pt\fastboot.exe" boot <image>
```

启动参数 `checkpoint=89`：

| 结果 | 含义 |
|---|---|
| `reason 2` 且没出现 `112` / `113` | `orig_va` 已对齐，第二次 slide 走完；接着测 **62 / 67**，再看 `arm_vm_physmap_init()` 是否整体通过 |
| `reason 2` 但 `113` 仍命中 | 还有别的未对齐来源（这时探针改动保住了内核不 panic，能继续二分） |
| `reason 0` | 二次 slide 内部有别的问题，用 83 → 85 → 87 → 89 四点二分 |

### 7.2 中期：QEMU（**解决"看不见 panic"**）

**这是最有价值的一条。** 我们的编译目标 `MACHINE_CONFIGS=BCM2837` 正是树莓派 3 的 SoC，而 QEMU 有 `raspi3b` 机器类型——**配置同源，比任何真机都更接近内核的编译假设。**

HakiOS 自己就有 `make qemu-open` / `make qemu-u-boot`。

**收益**：
- 串口输出从第一条指令就能抓，**早期 panic 带完整 backtrace**（现在只能看到 `reason 0`）
- 可挂 gdb 单步
- 秒级迭代

**推荐先做选项 B**：QEMU raspi3b + 直接引导内核（跳过 UEFI，用一小段 stub 构造 `boot_args` 直接跳 XNU）。`boot_args` 怎么填可以参考 `DarwinBoot.c` 的现有代码。

### 7.3 中期：Mac 本地编译（**解决"慢"**）

**HakiOS 官方支持 macOS 原生构建**，`xnu-deps/setup.sh` 里有专门的 `Darwin` 分支：

```bash
# macOS: no theos, but Xcode has the same cctools and the SDK can be had from github.com/theos/sdks.
theos_mac() { ... }
[ "$XNU_HOST_OS" = Darwin ] && { theos_mac; tools="git curl tar make cc clang clang++ flex bison m4 perl python3"; }
```

**需要**：Xcode（完整版）、`iPhoneOS16.5.sdk`（约 220 MB，从 `github.com/theos/sdks`）、`MacOSX13.3.sdk`（脚本自动下载，只取 DriverKit 头）。
**不需要**：theos 的 Linux 工具链（macOS 用 Xcode 自带的 cctools）。

**收益对比**：

| 场景 | 耗时 |
|---|---|
| CI 全量（现在每轮） | ~18 分钟 + 排队 + 下载 |
| 本地首次全量 | ~10–15 分钟 |
| **本地增量（改一个文件）** | **1–2 分钟** |

我们现在每轮改的只是探针位置，增量编译能把 18 分钟压到 2 分钟。

---

## 8. 关键文件与命令

### 8.1 文件

| 路径 | 作用 |
|---|---|
| `inputs/kernel.macho` | **每次内核重建后必须替换**，payload workflow 用它往镜像里塞 |
| `scripts/ci/apply_nonapple_guards.py` | 所有探针 + 栈调整 + `ml_enable_monitor` 守卫。幂等，带验证 |
| `scripts/ci/disable_pan.py` | 关 PAN（两处） |
| `scripts/ci/fat12_read.py` | 读镜像里的 FAT12 卷，验证内核版本 |
| `scripts/ci/carve_fat.py` | 从 boot.img 里切出 FAT 卷 |
| `scripts/ci/macho_map.py` | 解析 Mach-O 段表 + 符号地址（查 `gVirtBase` 落在哪个段之类） |
| `scripts/ci/dis_prologue.py` | 反汇编函数入口（用过它发现 `arm_vm_init` 的 prologue 只占 96 字节） |
| `edk2-msm-overlay/DarwinBootPkg/Driver/DarwinBoot.c` | 构造 `boot_args`、填 stub 槽、跳内核 |
| `TESTLOG.md` | 2219 行按时间排列的工程日志，含每轮的现象与推理 |

### 8.2 命令

**刷机**：

```powershell
$pt='tools\platform-tools'
& "$pt\adb.exe" -s 881QAESM95THY reboot bootloader
Start-Sleep 22
& "$pt\fastboot.exe" boot <image>
```

**典型一轮**：

1. 改探针 → 推脚本/launcher → 推内核（如需）
2. 内核 workflow 构建（~10 min）→ 下载 `meizu15-kernel` artifact（**5.8 MB，不是 160 MB**）
3. 替换 `inputs/kernel.macho` → 推送 → payload workflow（~8 min）→ 下载 `meizu15-uefi-payload-debug-full`（5.2 MB）
4. 刷机 → 拍照 → 读 `reason` 和 `checkpoint`

**注意 workflow 的 `Publish to a Release` 步骤总是失败**（tag 重名），无害，`Build` 和 `Upload` 都成功。

---

## 9. 教训（写给下一个人）

1. **`Publish to a Release` 失败不代表构建失败** —— 看 `artifacts` 是否存在。
2. **内核 artifact 曾经是 160 MB**，因为 `find -name '*.dSYM'` 从不匹配（`-name` 只测 basename，而 DWARF 负载同名）。用 `-not -path '*.dSYM/*'` 才有效。现在额外上传一个只含 `kernel.macho.gz` 的 **5.8 MB** artifact。
3. **`loader_stub_slot[3]` 不是空闲输出通道** —— 写它会破坏 loader 返回路径。
4. **固定编号的探针无法区分函数被多次调用** —— 用 `loader_checkpoint(N + ptov_index)` 索引化。
5. **`reason 0` 不等于"正常结束"** —— 它表示走了 `halt_all_cpus()` → `loader_return()` 路径（panic 或正常 halt）。
6. **这个阶段的 panic 打印不出来** —— `kprintf` 要到 `arm_vm_init()` 之后才起来，所以在 `arm_vm_init()` 内部崩溃只能看到 `reason 0` 和一个 192 字节的旧日志。这正是 QEMU 值得做的原因。
7. **PowerShell 的 `String.Replace` 没有 3 参数重载** —— 用 Python 做文本编辑。
8. **`.NET` 的 `ReadAllText` 用相对路径时解析到进程目录**，不是 PowerShell 的当前目录 —— 始终用绝对路径。
9. **改 `scripts/ci/*.py` 也会触发内核重建**，因为它在 workflow 的 `paths` 里。
10. **重写脚本时要让验证逻辑和常量列表对齐** —— 我曾在重写 `apply_nonapple_guards.py` 时漏掉探针 62/63/64，导致 CI 上验证失败、内核根本没构建，白跑一轮 18 分钟。
11. **`TopOfKernelData` 不等于 `PlaceImage()` 的 `Layout->Used`** —— `DarwinBoot.c` 后面还会把 return stub 计入并把 `Used` 对齐到 **16 字节**。凡是拿 `Policy` 推 `orig_va` 的算术都要算到这一跳之后（见 5.3）。
12. **这台机器上没有 `git` 在 PATH 里**，用 `D:\WorkBuddy\resources\vendor\PortableGit\cmd\git.exe`；仓库 `http[s].proxy` 已指向 `127.0.0.1:7897`，`adb/fastboot` 在 `C:\Users\54714\Desktop\搞机工具箱9.91\`。刷机前先 `adb devices` 确认 `881QAESM95THY` 在线。
