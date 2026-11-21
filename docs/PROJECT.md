# Meizu 15 上的 XNU：项目全景

> 这份文档回答"我们现在在哪、怎么走到这里的、接下来往哪走"。
> 逐轮的实测记录在 [`../TESTLOG.md`](../TESTLOG.md)（40+ 个里程碑），
> 历史细节与已解决问题在 [`../HANDOVER.md`](../HANDOVER.md)。
> 本文只写**结论、结构和路径**。

---

## 1. 项目是什么

把 Apple 开源的 XNU 内核（**xnu-8796.121.2**，即 Darwin / iOS 16.5 的内核）移植到
**魅族 15（M8810）** 上运行。

目标分三层，成本差着数量级：

| 层 | 目标 | 状态 |
|---|---|---|
| **L1 内核** | 内核打印自己的日志、建立 VM、启动第一个内核线程 | ✅ **已达成（2026-11-22 00:17，探针 117）** |
| **L2 用户态** | 根卷 + `bsd_init` + launchd + SpringBoard | 未开始，路径清楚（见 §7） |
| **L3 外设** | 显示、触摸、GPU、音频、Wi-Fi、蜂窝 | 未开始，有现成参照项目（见 §8） |

---

## 2. 硬件事实（全部实测确认）

| 项 | 值 |
|---|---|
| SoC | SDM660（骁龙 660），Kryo 260：**4×Cortex-A73 + 4×Cortex-A53，全部 ARMv8.0** |
| GPU | Adreno 512（a5xx） |
| 内存 | 4 GB；运行窗口 `ADFA0000 @ D0000000`（屏幕条码） |
| 屏幕 | 1080×1920 AMOLED |
| 存储 | eMMC |
| 设备序列号 | `881QAESM95THY` |
| 引导 | 已解锁，可 `fastboot boot`（不必刷分区） |

**关键判断**：ARMv8.0 是这台机器与 Apple 硬件最大的结构性差异。
Apple 从 A11 起是 ARMv8.2+，XNU 里大量代码默认硬件具备后来才有的特性 ——
本项目四道技术墙里有三道直接来自这个差异（见 §5）。

---

## 3. 引导链全貌

```
fastboot boot out/boot-meizu15-payload-debug.img
        │
        │  5,578,752 B boot.img = gzip(FV 33,554,432 B)
        ▼
   EDK2 UEFI 固件（构建时用 BCM2837/hakios 的平台配置 + Meizu 的 overlay）
        │
        ├── Meizu15PayloadDxe      板级初始化、内存图、显示控制台
        ├── Meizu15Launcher        菜单 / BootArgs / 从 payload 卷读 kernel.macho
        │        └── payload FAT 卷 16,672,256 B：kernel.macho + meizu15-8plus.adt
        └── DarwinBoot             解析 Mach-O、放置镜像、填 boot_args、跳内核
                │
                ▼
        XNU 内核第一条指令
```

两个容易踩的工程细节：

1. **payload 卷只有 15.9 MiB**（16,672,256 B），内核 16,283,328 B 已经接近上限，
   再加东西就要调整卷大小或换压缩方式。
2. **`DarwinBoot.c` 里 `Used` 必须按 16 KiB 对齐**（`ALIGN_VALUE(StubOffset + StubSpan, SIZE_16KB)`），
   否则 `TopOfKernelData` 尾部带 `0x100`，会让 `arm_vm_physmap_slide()` 的 `orig_va` 不对齐（§5 第 2 条）。

引导之后的**返回通道**是本项目自建的关键设施：内核可以从 `loader_return()` 回到 stub，
stub 把内核日志的最后 224 字节和条码画到屏幕上 —— 这是"看不见 panic"环境下唯一的观测手段。

---

## 4. 内核启动路径与探针地图

内核里埋了 60 多个 `loader_checkpoint(N)`，用二分法定位崩溃点。
`loader_checkpoint()` 会写一行 `cp N` 进环形日志，`loader_return()` 在末尾再写一行 `last cp N`，
**一次启动就能拿到整条轨迹**（早期一次只能测一个点，成本高一个数量级）。

### 4.1 已经走过的完整路径（全部实测）

```
arm_init() 前半
  10  11  30 31 32 33 34 35  12  40 41 42 43 44  13 14 15 16  45

arm_vm_init() 头部
  58 59 60  54 55 56 57  50 51 52 53  46  61

physmap（两次 slide + 三次 granular_prot）
  82 84 86 88 80   66 68 69 70 71  67   83 85 87 89 81   62 63 64

pmap_bootstrap()  （中断栈放大到 256 KiB 之后才通）
  47 48   96 97 98   90 91 92 93 94 95

arm_init() 尾部
  24 25 26 38 [PE_init_cpu 跳过] 39 36 37 27 28

machine_startup() / kernel_bootstrap()
  72 73 74
  75 76 77 78          ← printf / vm_mem_bootstrap / console_init / sched_init
  100 101 102 103 104 105 106 107   ← MACH_IPC hooks … kernel_thread_create … load_context
  108 109 110 111 112 113          ← load_context 内部 … machine_load_context 之前

kernel_bootstrap_thread()   ← 切换成功，CPU 进入第一个内核线程
  114 115 116 117      ← 入口 / stall / idle_thread_create / sched_startup
```

**117 之前的整条链没有未验证的黑区。**

### 4.2 探针编号约定

| 区间 | 用途 |
|---|---|
| 10–21、30–45 | HakiOS 上游在 `arm_init()` / `arm_init_cpu()` 里预留的编号 |
| 46–71 | `arm_vm_init()` 与 physmap |
| 80–89 | **索引探针**：`loader_checkpoint(80 + ptov_index)`，区分第几次 slide |
| 90–98 | `pmap_bootstrap()` |
| 100–107 | `kernel_bootstrap()` 尾部 |
| 108–113 | `load_context()` |
| 114–117 | `kernel_bootstrap_thread()` |

### 4.3 观测通道（重要）

- **`loader_log`**：内核里的环形日志（256 KB 头 + 1 MB 环），`loader_return()` 时取末尾 **224 字节**交给 stub
- **为什么是 224**：stub 在 y=1694..1888 画条码，更长的日志会被盖住
- **`loader_probe_values[6]`**：想让数值稳定显示时，先存进这个数组，
  由 `loader_return()` 在**截断之后**打印（`v <数字>`）—— 直接在探针处打印会被后续输出挤出窗口
- **屏幕条码**：`reason`、`checkpoint`、`delta`、`WIN/HIGH/RUN`、`x3..x8`、`slot magic`、`stub` 地址
- **`last cp N`**：一行就够判读当前进度

---

## 5. 四道墙（已全部推倒）

这四条都符合同一个模式：**源码看起来没问题，行为却矛盾** ——
因为源码假设了 Apple 硬件有、而 ARMv8.0 没有的东西。

### 5.1 `PAN` —— 影响最大的一条

内核早期就执行 `msr pan, #1` / `msr pan, #0`（Privileged Access Never）。
**ARMv8.0 没有 PAN**（ARMv8.1 才引入）→ 未定义指令异常 → 静默死。
修法：在 `apply_nonapple_guards.py` 里把这些指令改成条件编译/空操作。

### 5.2 `TopOfKernelData` 的 `0x100` 尾巴

`arm_vm_physmap_slide()` 要求 `orig_va` 页对齐。`DarwinBoot.c` 把内核放好后
又追加 return stub，`Used` 只按 16 字节对齐，导致 `TopOfKernelData` 尾部有 `0x100`。
修法：`Used = ALIGN_VALUE(StubOffset + StubSpan, SIZE_16KB)`。

### 5.3 中断栈耗尽（64 KiB → 256 KiB）

`pmap_bootstrap()` 的 prologue 需要 `sub sp, #0x240` + 5 个 `stp`；
到那时中断栈只剩不到 1 KiB → 写越界 → 静默死。
修法：`INTSTACK_SIZE` / `EXCEPSTACK_SIZE` 改成 `64 * 4096`（256 KiB）。

> 这条曾经被误判为"改大栈无效"，因为那次测量在 PAN 修复之前做，
> 内核根本到不了栈被吃光的位置。**这是本项目最贵的一次误判。**

### 5.4 `XNU_MONITOR` 分支：设备树从未被 rebase

`PE_slide_devicetree()` 只存在于 `#if XNU_MONITOR` 分支里，而 **BCM2837 不定义 `XNU_MONITOR`**，
所以走的是 `#else` —— 那里**没有** rebase 设备树。于是内核用到的 DT 地址一直是物理地址，
在开启 MMU 之后就无效了。
修法：在 `#else` 分支里、第二次 `arm_vm_physmap_slide` 之后调用 `SecureDTInit()`。

> 附带事实：`SecureDTInit()` **不做任何校验**（`pexpert/gen/device_tree.c` 只存指针），
> 所以那块内存的正确性只能靠内核自己保证。

### 5.5 额外：跳过 `PE_init_cpu()`

`PE_init_cpu()` 在开源树里**没有源码**（`pexpert/pexpert/arm64/` 只有头文件），
符号在链接后的内核里**被 strip**，连反汇编都不行。
探针 38 命中、39 不命中，窗口里只有它 —— 直接跳过，后续一切正常。

---

## 6. 当前状态

**探针 117 命中（2026-11-22 00:17）**：

```
117 = sched_startup() 之后
```

含义：

- `machine_load_context()` 的上下文切换**成功**（114 命中）
- `idle_thread_create()` 完成（116 命中）
- `sched_startup()` 返回（117 命中）—— **调度器已启动**

也就是说 **CPU 此刻在 `kernel_bootstrap_thread()` 里执行**，不再是启动路径上的顺序代码。
L1 目标达成。

### 6.1 曾被我判错的两件事（写下来避免重复）

1. **`act_cpudatap` 不是坏的**。从 `_machine_load_context` 反汇编得到真实偏移：
   `thread+0x1c8` = `act_cpudatap`，`thread+0x158` = `kstackptr`。
   修复前它的值和 `cpu_datap(0)` 完全相同 —— 那几轮"崩溃在切换"的结论是错的，
   真正原因是探针没进到线程里。
2. **`S3_3_C4_C4_0` 不是 Apple 专属寄存器**，它就是 **`FPCR`**（`mrs x19, FPCR`）。

教训：**`machine_thread` 的成员名不要猜**，`pc` / `act_cpudatap` 在这个树里都不存在；
要用就从反汇编里取偏移。

---

## 7. 剩余路径

### 7.1 内核启动收尾（预计 10–25 轮）

`kernel_bootstrap_thread()` 117 之后：

| 探针 | 调用 |
|---|---|
| 118 | `thread_daemon_init()` |
| 119 | `kernel_startup_initialize_upto(STARTUP_SUB_THREAD_CALL)` |
| 120 | `thread_bind(processor)` |
| 121 | `mapping_adjust()` |
| 122 | `clock_service_create()` |
| 123 | `device_service_create()` |
| 124 | `phys_carveout_init()` |
| 125 | `kdp_init()` |
| 126 | `kernel_startup_initialize_upto(STARTUP_SUB_SYSCTL)` |
| 127 | `vm_kernel_addrperm = early_random() ...` |
| 128 | `PE_init_iokit()` ← 大概率要跳过（Apple 平台代码） |
| 129 | `kernel_startup_initialize_upto(STARTUP_SUB_EARLY_BOOT)` |

这一段性质变了：不是"找 ARMv8.0 缺哪个寄存器"，而是"逐个子系统看它需不需要这块板子没有的东西"。

### 7.2 用户态（L2）

需要的东西与前面完全不同：

- **块设备 + 根卷**：现在只有 payload 里那个 16 MB FAT 卷。eMMC 驱动 + APFS 或 mockfs
- **中断**：到目前为止**中断从未真正打开过**
- **Mach IPC / bootstrap**：launchd 靠 Mach ports
- **`bsd_init()`**：BSD 层需要完整的驱动栈

### 7.3 外设（L3）

| 项 | 参照项目 | 说明 |
|---|---|---|
| 显示（MDSS + 面板） | **OPPO R11**（同 SDM660、同 1080×1920 AMOLED） | 先做 CPU 呈现 |
| 触摸 | R11（Synaptics）、dipper（FTS） | — |
| GPU（Adreno 512 / a5xx） | HakiOS `hakios.adreno.c`（a6xx）+ Mesa freedreno（a5xx 已支持） | 见 §8 |
| 音频 | dipper（ADSP ASM/ADM/AFE + TAS2557） | — |
| Wi-Fi / 蜂窝 | fajita（LTE 数据）、dipper（WCN3990） | 蜂窝见 §8.2 |

---

## 8. 外部参照：这个生态已经有人走到了哪

### 8.1 同 SoC（SDM660）的项目 —— 优先级最高

| 项目 | 设备 | 状态 |
|---|---|---|
| `appleiPodTouch4/UniXNU-r11` | OPPO R11（1080×1920 AMOLED、eMMC） | `record_state = "available"`，带 `ios-16.5-20F66` |
| `qine233/UniXNU-clover` | 小米平板 4 LTE | eMMC mockfs 根 + EL0 test init，APFS 探测中 |
| `Chromitsune/UniXNU-Lavender` | 红米 Note 7 | — |
| `gs240333/UniXNU-wayne` | 小米 6X | — |

**我们自己在 UniXNU 的设备表里**（`devices.toml`，`name = "meizu15"`，`soc = "SDM660"`）。

### 8.2 更成熟的其他 SoC 项目（技术可直接借鉴）

| 项目 | 设备 | 已实现 |
|---|---|---|
| HakiOS 0.5 beta `fajita` | OnePlus 6T（SDM845） | Wi-Fi、**LTE 数据**、扬声器+麦克风、蓝牙、**Adreno 630 GPU（Mesa GLES 3.2，60 fps）**、充电 |
| `youzjuzi/dipper-xnu` | 小米 8（SDM845） | 显示、触摸、**A630 GPU**、WCN3990 Wi-Fi、蓝牙、扬声器（ADSP + TAS2557） |
| `UniXNU-raphael` | 红米 K20 Pro（SM8150） | 桌面/触摸，iOS 16.7.16 / iPadOS 17.7.11 |

### 8.3 GPU 是怎么实现的（不是转译二进制）

HakiOS 有**三条**渲染路径，默认那条不是 Metal：

| 路径 | 链路 | 着色器 | 实测（Adreno 642L） |
|---|---|---|---|
| 默认 | CoreAnimation → **OpenGL ES** → **Mesa freedreno** → HakiosGPU | Mesa 编译 GLSL | 可用 |
| `hakios-metal` | Metal → `HakiosMetalCA` → `hakios-metal`（自己生成 PM4 包）→ HakiosGPU | **AIR → NIR → ir3**（进程内 Mesa 编译器） | **100–104 fps** |
| `metal2vulkan` | Metal → AdrenoMetal 前端 → metal2vulkan → **Turnip** → `hakios-drm` | AIR → Vulkan → Turnip | 50–57 fps |

内核层 `drivers/gpu/hakios.gpu.cpp` 的设计是关键：

- **请求编号沿用 Linux msm DRM 的编号**（`0x7e` = IOSurface → buffer，`0x7d` = 用户页 → buffer，即 userptr）
- 用 `IOMemoryDescriptor` 钉住调用者页，进 GPU 地址空间（自建 39 位页表）
- 每次提交按物理地址刷 cache（`dc civac`）
- 底层 `hakios.adreno.c`（3843 行）：ring buffer、fence、**GMU 固件**、SMMU、`CP_PROTECT`

显示侧是两个替身：`MiniIOSurface` + `MiniIOMobileFramebuffer`（含 `drawLayer` / `present`）。
SpringBoard 以为在跟 Apple 的 `IOMobileFramebuffer` 说话，实际接到了 Adreno 的 buffer 上。

**对 Adreno 512 的工作量**：内核层与型号基本无关，差异集中在 `adreno.c` 的
ring/微码布局、寄存器初始化序列、SMMU 版本；用户态 Mesa/freedreno **本来就支持 a5xx**。
所以是"把 a6xx 假设剥掉换成 a5xx 初始化序列"，不是从零写。

### 8.4 蜂窝

HakiOS `docs/cellular.md`：Hexagon MPSS 从 XNU 启动（TrustZone 固件、SMP2P、GLINK、QRTR，
**88 个 QMI 服务**）、RMTFS 把 UFS 分区当 modem 文件系统、IPA 包引擎（4.11）跑起来、
SIM 识别、NAS 读信号（-70 ~ -87 dBm）、QMI WDS 绑 QMAP mux 1 起 IPv4 数据会话。

**注意**：文档记录的是 **LTE 数据**。**语音通话**（VoLTE/IMS）在公开文档里没有明确证据。

### 8.5 双系统引导

`Hercules-Zhaoziyi/mi8-dualboot-lab`：一个 **UEFI 层引导菜单**（AArch64 EFI 应用 + FFS 组件），
不是改分区表。它靠 `Reboot2PayloadApp.c` 写 magic + warm reset 唤起 payload。

我们的 `Meizu15Launcher` 已经是同一层的东西（管 BootArgs、加载 kernel），
所以"菜单里加一个 Boot Android 选项"是加分支，不是从零做引导器。
三个层次：

| 层 | 做法 | 成本 | 风险 |
|---|---|---|---|
| L0 | 手动 `fastboot boot` 切换 | 0 | 无 |
| L1 | Launcher 菜单加 Android 引导（Linux arm64 boot protocol） | 中 | 低 |
| L2 | 菜单刷进 `boot` 分区，开机持久选择 | 高 | 中高（需 9009/EDL 救砖） |

**建议排在内核启动稳定之后**：现在 iOS 侧还没有可用系统，菜单里按下去一个选项还是黑屏。

---

## 9. 工程方法（这部分比技术细节更值得继承）

### 9.1 一轮迭代是什么

```
改补丁（scripts/ci/apply_nonapple_guards.py）
   → git 提交 → CI 编内核（约 7 分钟）→ 取 kernel artifact
   → 替换 inputs/kernel.macho → 提交 → CI 编 payload（约 6 分钟）
   → 下载 boot.img → fastboot boot → 拍照 → 判读 last cp
```

单轮约 20–30 分钟，其中构建占 90%。

### 9.2 三条硬性工程判据

1. **只看 artifacts，不看 run 颜色**。这个 workflow 有三处会吞错误：
   `bash xnu-build.sh | tail || echo`、`Pack the payload` 的 `continue-on-error: true`、
   upload 的 `if-no-files-found: warn`。出现过"run 全绿、artifact 为空"，
   也出现过"run 红、kernel artifact 完整"（只是 `Publish to a Release` 失败）。
2. **构建日志保留 2000 行**。曾经 `tail -300` 把 `startup.c:992: error:` 切掉，
   只剩一句 `make: *** Error 2`，白等一轮。
3. **探针必须自报号**。`cp N` + `last cp N` 让一次启动拿到整条轨迹。

### 9.3 网络与通道

`github.com:443` 经常整段不通，`api.github.com` 通常可用。
所以推送有两套：

- **git**（`out/kernelround.py` / `round.py` / `resume_kernel.py`，已改成强制直连 + 重试）
- **GitHub API 的 Git Data API**（`out/api_round.py`）：blob → tree → commit → 更新 ref，
  完全绕开 git 协议。16 MB 的内核也一样能传

### 9.4 脚本

| 脚本 | 作用 |
|---|---|
| `out/kernelround.py 0 "msg"` | 改内核的一轮（提交→内核 CI→换 inputs→payload CI→下载） |
| `out/round.py 0 "msg"` | 只改 launcher 的一轮 |
| `out/api_round.py <sha>` | 全 API 版的一轮 |
| `scripts/ci/apply_nonapple_guards.py` | **所有补丁都在这一个文件里**，按函数组织、幂等 |
| `out/dis_*.py` | 从 `kernel.macho` 反汇编 / 取符号 / 取结构偏移 |

---

## 10. 教训（写给下一个接手的人）

1. **`TopOfKernelData` 不等于 `PlaceImage()` 的 `Layout->Used`** —— 后面还会把 return stub 算进去。
2. **`INTSTACK_SIZE` 改大是有效的** —— 别信"改大没用"的旧结论，那次测量在 PAN 修复之前。
3. **补丁要插在会编译的那一支** —— `PE_slide_devicetree()` 只在 `#if XNU_MONITOR` 里，BCM2837 走 `#else`。
4. **`SecureDTInit()` 不校验指针** —— 设备树那块内存的正确性只能靠内核自己保证。
5. **ADT 不是标准 FDT** —— Apple 格式：`<uint32_t nProperties, uint32_t nChildren>`、
   属性 `char name[32] + uint32_t length`、四字节对齐。
6. **不要猜结构体成员名** —— 从 `_machine_load_context` 反汇编取偏移（`act_cpudatap`=0x1c8、`kstackptr`=0x158）。
7. **`S3_3_C4_C4_0` 是 `FPCR`**，不是 Apple 专属寄存器 —— 判"ARMv8.0 不支持"之前先解码。
8. **`_BootCpuData` 这个符号不存在**，只有 `_CpuDataEntries` / `_cpu_datap`。
9. **探针值要存进 `loader_probe_values` 再打印** —— 直接在探针处打印会被后续 console 输出挤出 224 字节窗口。
10. **green run ≠ 构建成功**；**red run ≠ 产物为空**。只看 artifacts。
