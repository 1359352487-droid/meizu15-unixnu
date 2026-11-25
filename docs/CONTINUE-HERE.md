# 交接：Meizu 15 上的 XNU 移植

> **版本**：2026-11-26 01:10（第六版交接文档，合并了 macOS 本地链的真机结果）
> **上一段会话的终点**：CI 链探针 **139 命中**（`bsd_init()` 门口）；本地链**已经起内核**，
> 停在 `Data abort: Translation fault, third level`，`FAR 0x1000BF0`，PC 在 DarwinBoot 的段拷贝。
> **读完这份文档，你应该能直接开始干活。** 不确定的地方先读
> `PROJECT.md`（项目全景与探针地图）、`ROADMAP.md`（分阶段路线）、
> `../TESTLOG.md`（逐轮实测记录，20 万字符，读最后 20 节就够）。
> 本地链（macOS 虚拟机、13 分钟一轮、不依赖 CI）见 §4.0 与 `docs/LOCAL-CHAIN.md`。

---

## 0. 三十秒速览

| 项 | 值 |
|---|---|
| 在做什么 | 把 Apple 开源的 XNU（`xnu-8796.121.2` = iOS 16.5 内核；目标镜像为 iOS 16.7.16 / `xnu-8796.141.3`，见 `profiles/os/ios-16.7.16-20H392`）跑在魅族 15 上 |
| 硬件 | SDM660 / Kryo 260（4×A73 + 4×A53，**全核 ARMv8.0**）/ 4 GB / 1080×1920 AMOLED / eMMC |
| 做到哪了 | **两条链都已起内核**：CI 链到 `bsd_init()` 门口（cp 139）；本地链到 DarwinBoot 往内核窗口写段，卡在窗口地址落在 DRAM 之外（§4.0） |
| 卡在哪 | 本地链：`AllocateWindow()` 选中了 `0x1000BF0` 这类**未映射低位**，写 `__BOOTDATA` 时三级翻译失败。CI 链：`vfs_mountroot()` 没有根卷 |
| 下一步 | ① 编入 §4.0 的 `AllocateWindowInDram()` 修复，让内核真正跑进 `bsd_init()`；② 挂根卷（`rd=md0` + mockfs + initrd 已就位） |
| 一轮迭代 | **本地链**：`scripts/local/build-round.sh <name>` ≈ 13 min → `fastboot boot` → 拍屏。**CI 链**：API 提交 → 内核 6–8 min → payload 6–10 min → 刷机 |
| 仓库 | `github.com/1359352487-droid/meizu15-unixnu` |
| 设备 | Meizu 15，adb 序列号 `881QAESM95THY` |

---

## 1. 目标与分层

| 层 | 目标 | 状态 |
|---|---|---|
| **L1** | 内核打印自己的日志、建立 VM、启动第一个内核线程 | ✅ 2026-11-22 00:17，探针 **117** |
| **L1.5** | `kernel_bootstrap_thread()` 走完，进入 `bsd_init()` | ✅ 2026-11-23 00:41，探针 **139** |
| **L2** | 根卷 + `bsd_init` + launchd + SpringBoard（能看见桌面） | ⬜ **当前战场** |
| **L3** | 显示、触摸、GPU、音频、Wi-Fi、蜂窝 | ⬜ 有同 SoC 参照项目 |

关于"完美运行 iOS 16"的现实边界，`ROADMAP.md` §0 有完整表格，一句话概括：

- **有先例**：桌面、触摸、Wi-Fi、Safari、扬声器、LTE 数据
- **极难**：GPU 加速（Adreno 512 没人做过，同系 Adreno 630 有人做到 60 fps）
- **不可能**：相机、指纹、App Store、iCloud、推送（需要 Apple 的设备签名）
- **无公开证据**：打电话（语音，VoLTE/IMS）

---

## 2. 当前进度：完整探针地图

全部位置都实测验证过（`reason 2` 命中或轨迹可见）。**这是这个项目最值钱的资产。**

```
arm_init()              10 11 30 31 32 33 34 35 12 40 41 42 43 44 13 14 15 16
arm_vm_init()           45 58 59 60 54 55 56 57 50 51 52 53 46 61
physmap（两次 slide）    82 84 86 88 80 66 68 69 70 71 67 83 85 87 89 81 62 63 64
pmap_bootstrap()        47 48 96 97 98 90 91 92 93 94 95
arm_init() 尾部         24 25 26 38 [跳 PE_init_cpu] 39 36 37 27 28
machine_startup()       72 73 74
kernel_bootstrap()      75 76 77 78 | 100 101 102 103 104 105 106 107
load_context()          108 109 110 111 112 113
kernel_bootstrap_thread()
  114 115 116 117       入口 / stall / idle_thread_create / sched_startup
  118 … 123             thread_daemon / THREAD_CALL hooks / thread_bind / mapping_adjust
                        / clock_service_create / device_service_create
  124 … 129             phys_carveout / kdp_init / SYSCTL hooks / early_random
                        / [跳 PE_init_iokit] / EARLY_BOOT hooks
  130 … 135             130 = spllo()【中断第一次打开】/ initialize_screen / commpage
                        / mac_policy_initmach / LOCKDOWN hooks / [跳 OSKext]
  136                   136 = machine_lockdown()【arm_vm_prot_finalize 全通，含换栈】
  137 138 139           [跳 PE_lockdown_iokit] / vm_set_restrictions / [跳 kperf]
                        / 139 = bsd_init() 之前   ← 现在这里
```

### 怎么读屏

刷机后屏幕**顶部**是判读区：

```
kernel returned: reason 2, log 1307 bytes     ← reason 2 = 命中；reason 0 = 崩了/超时
checkpoint 139                                 ← 请求的编号
cp 10 ... cp 139                               ← 探针轨迹（可能不完整，见 §5.1）
last cp 139                                    ← 【最可靠】最后到达的探针
v 18446744005119469344                         ← loader_probe_values 打印的数值
```

**`last cp` 是最可靠的一条** —— 它不依赖日志缓冲，只在返回时写一行。

---

## 3. 已放弃 / 已跳过的方案

### 3.1 跳过的内核调用（Apple 平台代码或缺硬件）

| 调用 | 在哪 | 为什么跳过 |
|---|---|---|
| `PE_init_cpu()` | `arm_init()` | 开源树无源码（`pexpert/pexpert/arm64/` 只有头文件），符号被 strip |
| `PE_init_iokit()` | `kernel_bootstrap_thread` 127→128 | 同上 |
| `PE_lockdown_iokit()` | 136→137 | 同上 |
| `OSKextRemoveKextBootstrap()` | 134→135 | 抛弃引导段属优化，跳过只多占几 MB |
| `kperf_init_early()` | 138→139 | PMU 性能计数器，启动不需要；**且它会让整轮不返回** |

### 3.2 "别再试"的技术方案

| 试过的 | 结论 |
|---|---|
| `git clone --depth 1 <HakiOS>` | **必须 `--branch main`** —— 默认分支已改成 `common`（Kconfig 树，没有 `xnu/apply.sh`） |
| `git push` 推代码 | `github.com:443` 长期不通；**改走 GitHub API**（`scripts/ci/api_push.py`） |
| 看 run 是不是绿的 | 这个 workflow 有三处吞错：`\| tail \|\| echo`、`continue-on-error: true`、`if-no-files-found: warn`。**只认 artifacts**；反过来红 run 也可能带着完整内核（只是 Publish 挂了） |
| `INTSTACK_SIZE` 改大"无效" | **错**。那次测量在 PAN 修复之前。改成 64×4096 后 96/97 立刻通过 |
| 猜 `machine_thread` 成员名 | `pc`、`act_cpudatap` 在本树**都不存在**。要用偏移：从 `_machine_load_context` 反汇编取（`act_cpudatap` = thread+0x1c8，`kstackptr` = thread+0x158） |
| `extern char BootCpuData[]` | 符号不存在（链接失败）。用 `cpu_datap(0)` |
| `find(root, "osfmk/arm/arm_vm_init.c")` | 路径是 **`osfmk/arm64/`** |
| 认为 `S3_3_C4_C4_0` 是 Apple 专属寄存器 | 它就是 **`FPCR`**，ARMv8.0 完整支持 |
| 跳过 `ml_static_mfree` 保护 `loader_log` | 判断错了：`loader_log` 在 `__DATA` 段（内核镜像内部），不在被释放范围。**日志断裂的真实原因仍未定位，见 §5.1** |

### 3.3 推倒的四道墙（记住这个模式）

四条都是同一个模式：**源码看起来没问题，行为却矛盾 —— 因为源码假设了 Apple 硬件有、而 ARMv8.0 没有的东西。**

| # | 墙 | 根因 | 修法 |
|---|---|---|---|
| 1 | **`PAN`** | ARMv8.0 没有 PAN 寄存器（ARMv8.1 才有），`msr pan` = 未定义指令 | `scripts/ci/disable_pan.py` |
| 2 | **`TopOfKernelData` 的 `0x100` 尾巴** | `DarwinBoot.c` 追加 return stub 后 `Used` 只按 16 字节对齐，导致 `arm_vm_physmap_slide` 的 `orig_va` 不对齐 | `Used = ALIGN_VALUE(StubOffset + StubSpan, SIZE_16KB)` |
| 3 | **中断栈耗尽** | `pmap_bootstrap()` 的 prologue 需要 ~0x240 字节，64 KiB 栈到那时已用尽 | `INTSTACK_SIZE`/`EXCEPSTACK_SIZE` → 64*4096（256 KiB） |
| 4 | **设备树从未被 rebase** | `PE_slide_devicetree()` 只在 `#if XNU_MONITOR` 分支里，BCM2837 走 `#else` | 在 `#else` 分支、第二次 slide 之后补 `SecureDTInit()` |

---

## 4.0 本地链：真机已经起内核了（2026-11-26 定案）

CI 链之外还有一条**完全本地**的链（macOS 虚拟机，`docs/LOCAL-CHAIN.md`），13 分钟一轮、
不需要 API、不需要等 artifact。它已经跑到"固件把内核搬到 DRAM"这一步，卡点比 CI 链更靠前，
但信息量大得多。

### 4.0.1 本地链产物谱系

| 镜像 | 内核 | 探针 | `rd=md0` | initrd | 真机结果 |
|---|---|---|---|---|---|
| `…-local.img` | 减配 16.5 MB | — | — | — | `Shell>` |
| `…-probes3.img` | 减配 16.5 MB | ✓ | ✓ | ✓ | `Shell>` |
| `…-full25.img` | 25 驱动 17.5 MB（LZMA） | ✓ | ✓ | ✓ | `Shell>` |
| **`…-logical.img`** | 25 驱动（LZMA） | ✓ | ✓ | ✓ | **起内核 → Data abort** |

`logical.img` 逐层结构（2026-11-26 在 Windows 上复核过，可完全复算）：

```
boot hdr (ANDROID!, 4 KiB page) → gzip → outer FV 32 MiB (_FVH@0x28)
  → GUID EE4E5898-3914-4259-9D6E-DC7BD79403CF (EFI LZMA) @0x11218
    → 解压 40,535,880 B → payload FV (_FVH@0x30)
      → FAT16 卷 @0x17D0730（512 B/扇区, 8 扇区/簇, 15,564,828 B）
        /EFI/UniXNU/Payloads/kernel.macho        4,368,146 B  (LZMA, 解压 17,498,352 B)
        /EFI/UniXNU/Payloads/kernel.macho.adt       13,996 B
        /EFI/UniXNU/Payloads/launchd.macho          35,040 B  (initrd, 打印 M15OK)
        /EFI/UniXNU/unixnu.toml                        314 B  (args 里有 rd=md0)
        /EFI/UniXNU/LAUNCHER.EFI + /EFI/BOOT/BOOTAA64.EFI   32,768 B ×2
```

### 4.0.2 卡点定案：窗口落在了 DRAM 之外

真机屏（`logical.img`）：

```
Data abort: Translation fault, third level
PC 0x0000000C91F0584   (Meizu15Launcher.dll)
FAR 0x000000001000BF0  ESR EC 0x25 (data abort, same EL, third level)
```

把镜像里的内核解压后读段表（`Kernel.Base = 0xFFFFFFF007004000`）：

| 段 | delta (vmaddr−Base) | fileoff | filesize |
|---|---|---|---|
| `__TEXT` | 0 | 0 | 0x1A4000 |
| `__DATA_CONST` | 0x1A4000 | 0x1A4000 | 0xEC000 |
| `__TEXT_EXEC` | 0x290000 | 0x290000 | 0x8D4000 |
| `__KLD` | 0xB64000 | | 0x4000 |
| `__LASTDATA_CONST` | 0xB68000 | | 0x4000 |
| `__KLDDATA` | 0xB6C000 | | 0x4000 |
| `__DATA` | 0xB70000 | 0xB70000 | 0x58000 |
| `__BOOTDATA` | **0xFB0000** | 0xBC8000 | 0xC4000 |
| `__LINKINFO` | 0x1074000 | 0xC8C000 | 0x90000 |
| `__LINKEDIT` | 0x1104000 | 0xD1C000 | 0x3940F0 |

**窗口需求 = 0x14980F0 = 21.6 MB。**

结论：

1. `0x1000BF0` 落在 `__BOOTDATA` 段体内（相对段首 0xBF0）。
2. 反推窗口基址 = `0x1000BF0 − 0xFB0000 = 0x50BF0`——**不是 4 KB 对齐**，而
   `AllocatePages()` 返回的地址必然页对齐，所以出问题的不是"段偏移"，而是窗口本身/
   `PlaceImage()` 里的非对齐字段。
3. 无论怎么算，**`0x1000BF0` < `0x80000000`，SDM660 上没有任何 DRAM**。这个值正好与
   `AllocateWindow()` 注释里记录的历史现象同族（`0x08000000` / `0x00D00000` / `0x000D0000`）：
   EDK2 的内存图把这些低地址描述为 `EfiConventionalMemory`，但 CPU 页表没有映射它。
4. LZMA 与这无关：`ParseKernel(Image, ImageSize)` 是在解压**之后**调用的，段表来自解压后的镜像。

### 4.0.3 已写入仓库的修复（未编、未上机）

`edk2-msm-overlay/DarwinBootPkg/Driver/DarwinBoot.c`：

- 新增 `AllocateWindowInDram()`：让固件分配器自己给一块，**校验必须整体落在
  `[0x80000000, 0x14C7E0000)`**（= `board.toml` 的 dram span），不合格就 `FreePages` 重试，
  最多 16 次；失败才回退旧的 `AllocateWindow()`。
- 窗口低于 DRAM → 屏上画 `WINDOW <addr> BELOW DRAM` 并 `goto Fail`（不再靠三级翻译异常）。
- 段拷贝循环逐段校验首尾都在 DRAM 内，越界画 `SEG <n> -> <addr> OUTSIDE DRAM`。
- 成功路径在屏上画 `LOAD <addr> USED <n> WANT <n>`，一屏就知道窗口落在哪。

**验收**：屏上出现 `LOAD 8…`（地址以 `8` 开头），然后 `last cp` 从 139 变成 150/151（见 §8）。

### 4.0.4 两处必须记住的分叉

- **探针编号撞车**：仓库 `apply_nonapple_guards.py` 的 140–146 已被 `arm_vm_prot_finalize()`
  占用（`PROT_FINALIZE_PROBES`）；本地那版把 140/141/142/143 插在 `bsd_init()` 里，**同一个号
  在两个树里指两个地方**。已统一改成 **150/151/152**（`add_bsd_init_probes()`）。
- **`Meizu15Launcher` 现在在固件里**：`meizu15.fdf.inc` 加了
  `INF …/Meizu15Launcher.inf`，所以 `Request.BootArgs` 里的 `rd=md0` 与 ESP 的
  `unixnu.toml` 是**两份都会生效**的参数，改一处不生效时先看另一处。

---

## 4. 下一步

### 4.1 主线：先让内核真正跑进 `bsd_init()`

`bsd_init()` 里第一件大事是 `vfs_mountroot()`。现在没有可挂的卷 —— 但**更靠前的阻塞**是
§4.0.2 的窗口地址问题：本地链现在根本到不了内核的第一条指令。顺序因此变成：

1. **编入 §4.0.3 的窗口修复**，刷一次 `logical` 版，屏上应出现 `LOAD 8…`，且 `last cp` 走到
   150/151/152。
2. **确认根卷路径**：`rd=md0` 已在 `unixnu.toml` 与 launcher 两处；`mockfs` 已在
   `configs/meizu15_defconfig` 里打开（`CONFIG_XNU_SOURCE_MOCKFS=y`）；initrd 是
   `launchd.macho`（打印 M15OK 的静态替身）。三项都在物里，缺的是"内核跑起来"这一步。
3. **eMMC / APFS 是后面的事**：`sdhci-msm-v5 @ 0xC0C4000`，参照 `qine233/UniXNU-clover`。

**只读勘察命令（不动设备，随时可跑）**：

```bash
adb -s 881QAESM95THY shell cat /proc/partitions
adb -s 881QAESM95THY shell ls -l /dev/block/by-name/
adb -s 881QAESM95THY shell 'cat /sys/class/block/mmcblk0p*/start /sys/class/block/mmcblk0p*/size'
fastboot getvar all 2>&1 | tee out/getvar.txt
```

`board.toml` 里已经 pin 了部分几何（`userdata` = `mmcblk0p57`, start 13369344, size 230907871,
整盘 244277248 扇区；`xbl_a` = 0x20000 / 7168 扇区），**还缺的是完整 by-name 名单**，
补进去就能把 P2 的分区表这块收尾。

### 4.1.1 参照项目（都已确认可达）

- `appleiPodTouch4/UniXNU-r11` —— OPPO R11，SDM660 / 1080×1920 AMOLED / eMMC，最像。
- `qine233/UniXNU-clover` —— 小米平板 4 LTE，SDM660，**eMMC + mockfs 根已跑通**。

看它们的 `board.toml`、`patches/`、eMMC/SDHCI 驱动、mockfs 的 `vfsops` 注册落点。


### 4.2 支线 A：修日志通道（不阻塞主线，但迟早要修）

见 §5.1。进了 `bsd_init` 之后会很需要完整轨迹。

### 4.3 支线 B：UART 串口控制台（投产比最高）

现在每轮 20–30 分钟，其中一半耗在"拍照 → 人读 → 判读"。串口通了日志实时进电脑。
并行会话已经在写 `kernel/sdm660/sdm660_uart.c`（默认关闭，`sdm660_shim=1` 才启用，不干扰现有探针）。

---

## 5. 已知未解决问题

### 5.1 日志在 `cp 70` 之后断裂

`reason 2, checkpoint 139` 时 `log 1307 bytes`，轨迹停在 `cp 70`，
中间 82–98、100–138 全部缺失，但 `cp 139` 那 7 字节确实写进去了。

已排除：
- 缓冲区容量（1.5 MB，`LOADER_LOG_HEAD` 256 KB，远未到环模式）
- "低地址被 `ml_static_mfree` 释放"（`loader_log` 在 `__DATA` 段）

**已做过的实验（2026-11-22 23:xx，记录在 `TESTLOG.md` 末尾）**：把
`arm_vm_prot_finalize()` 里两个 `ml_static_mfree` 改成注释（`keep_loader_log_memory()`，
探针 141/145 标位），再请求 139 —— **`log` 仍然停在 `cp 70`**。所以这个假设也被推翻了，
真实原因**仍未定位**。

**下一步的排查手段（便宜且独立于假设）**：`loader_probe_values[6]` 已在用 0–4，剩一个槽。
在 130/135/139 三处把 `loader_log` 的写指针 / 长度字段塞进 `[5]`，返回屏上的 `v` 值就能区分：

- `v` 随探针增长 → 写侧正常，问题在返回后的打印循环（`DarwinBoot.c` 一侧）；
- `v` 冻结在 1300 → 写侧真的没写，问题在结构体/指针（重点看第二次 `physmap_slide` 之后
  `loader_log` 的基址是否仍然是内核里的那个 VA）。

**影响**：`last cp` 通道正常，判读不受影响；但看不到中间轨迹。进 `bsd_init()` 之后会越来越需要它。

### 5.2 `payload-fv.txt` 报 `payload file NOT found in the outer volume`

但 `UEFIExtract` 能完整提取 payload FAT 卷（含 16,283,328 字节 kernel），镜像正常引导。
**判断：检查脚本误报，不影响功能。**

### 5.3 图像大小偶尔差 4 KiB

`5,574,656` vs `5,578,752`。出现过几次，都"能用"。
没有深究，记在这里以免下次当成新问题。

---

## 6. 环境与工具

### 6.1 凭据与设备

| 项 | 值 |
|---|---|
| 仓库 | `1359352487-droid/meizu15-unixnu`（分支 `main`） |
| Token | 环境变量 `GH_TOKEN`，或 `~/.gh-token`（需要 `repo` / `contents: write`） |
| 设备 | Meizu 15，adb 序列号 `881QAESM95THY` |
| 上游 HakiOS | `oosexgang/hakios-xnu-kernel`，**必须 `--branch main`** |

### 6.2 这台机器（Windows）

```
git      D:\WorkBuddy\resources\vendor\PortableGit\cmd\git.exe   （不在 PATH）
adb      C:\Users\54714\Desktop\搞机工具箱9.91\adb.exe
fastboot C:\Users\54714\Desktop\搞机工具箱9.91\fastboot.exe
token    C:\Users\54714\Documents\deepseek-harness\default-workspace\.gh-token
```

### 6.3 macOS（如果换机器）

```bash
brew install python3 android-platform-tools
echo "<PAT>" > ~/.gh-token
```

脚本在 `scripts/ci/` 下是便携的（路径从 `__file__` 推、token 走环境变量、不依赖 git）。
**注意**：macOS 若是虚拟机，adb/fastboot 可能看不到手机（USB 直通问题），
那种情况只能做"改代码 + 跑 CI + 下载镜像"。

### 6.4 CI

| Workflow | 时长 | 产物 |
|---|---|---|
| `build-meizu15-xnu-hakios-v2.yml` | 6–8 min | `meizu15-kernel`（5.8 MB） |
| `build-meizu15-uefi-payload.yml` | 6–10 min | `meizu15-uefi-payload-debug-full`（5.2 MB） |

---

## 7. 一轮迭代怎么做

```bash
# 1. 改补丁（所有内核补丁都在这一个文件里）
$EDITOR scripts/ci/apply_nonapple_guards.py

# 2. 用 API 提交（不用 git push）
python scripts/ci/api_push.py scripts/ci/apply_nonapple_guards.py "startup: probe xxx"

# 3. 跑完整一轮：等内核 → 换 inputs → 提交 → 等 payload → 下载到 out/
python scripts/ci/api_round.py <上一步的 sha>

# 4. 刷机
& "$env:BOX\adb.exe" -s 881QAESM95THY reboot bootloader
Start-Sleep 20
& "$env:BOX\fastboot.exe" -s 881QAESM95THY boot out\boot-meizu15-payload-debug.img

# 5. 拍照，读 last cp N
```

**只改引导参数（launcher）时**，不需要重编内核：

```
edk2-msm-overlay/Platform/Meizu/sdm660/Meizu15Launcher/Meizu15Launcher.c
  Request.BootArgs = L"-v loader_return_after=60 checkpoint=139";
```

改完同样用 `api_push.py` 提交，CI 会重新打 payload（内核不变）。

---

## 8. 写新探针的规矩

1. **编号别撞**：10–45 是 HakiOS 上游的；46–71 `arm_vm_init`；**80–89 是索引探针**
   （`loader_checkpoint(80 + ptov_index)`，区分第几次调用）；90–98 `pmap_bootstrap`；
   100–139 `kernel_bootstrap`；**140–146 已被 `arm_vm_prot_finalize()` 占用**
   （`PROT_FINALIZE_PROBES`，140 入口 / 141 第一个 free / 142 RXN / 143 cpu_stack_alloc /
   144 replace_bootstack / 145 第二个 free / 146 预期收尾）。
   **`bsd_init()` 的探针用 150/151/152**（`add_bsd_init_probes()`），**150 以上才是空的**。
   2026-11-26 之前本地那版把 140–143 插在 `bsd_init()` 里，与上面撞号，已纠正。
2. **锚点必须唯一**：`text.count(needle) != 1` 就 `raise SystemExit`，不要猜。
   同名调用出现两次（如 `PE_init_cpu`）要用多行上下文组合。
3. **幂等**：插入前检查标记是否已在。
4. **先在真文件上验证**：`out/` 里有源码副本（`startup.c`、`arm_vm_init.c`、`cpu.c` 等），
   写个小脚本跑一遍确认位置和幂等，再提交。**这一步能省掉整轮 20 分钟的白跑。**
5. 改 `scripts/ci/*.py` 会触发内核重建（它在 workflow 的 `paths` 里）。

---

## 9. 给新会话的第一条建议

**先确认环境是通的，不要一上来就改代码：**

```bash
python scripts/ci/api_round.py d91ce2d2
```

它会等构建、下载镜像到 `out/`。刷上去拍张屏，看到 `last cp 139` + `reason 2`，
说明整条流水线正常，可以从 §4.1 开始。

**然后读 `PROJECT.md`**（项目全景、引导链、外部参照项目）。
那份文档比这份更详细地讲了"为什么这样设计"，这份只讲"现在在哪、下一步做什么"。
