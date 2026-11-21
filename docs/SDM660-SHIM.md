# SDM660 转移层（translation layer）

> 代码在 [`kernel/sdm660/`](../kernel/sdm660/)，装进内核树的脚本是
> [`scripts/ci/install_sdm660_shim.py`](../scripts/ci/install_sdm660_shim.py)。
> 写于 2026-11-21，内核刚能打印日志、卡在 `cpu_timebase_init()` 的时候。

## 它是什么

XNU 的平台层（`pexpert` + `osfmk/arm64`）是按 Apple 芯片写的：AIC 中断控制器、
Apple 自己的 timebase 块、Apple 的 UART、PMGR。SDM660 上这些一个都没有，而 Apple 的
驱动又都不在开源树里。所以"让 iOS 调用这颗 SoC 的硬件"必须有一层翻译：

```
         XNU 的硬件假设                      SDM660 实际
  ┌──────────────────────────┐      ┌──────────────────────────┐
  │ AIC / rtclock_timebase…  │      │ GICv3 / CNTFRQ_EL0 / …   │
  │ PE_* / ml_* 平台调用      │ ───▶ │ sdm660_* 这一层           │
  └──────────────────────────┘      └──────────────────────────┘
```

这一层**不替换** XNU 的 pmap、调度器、异常处理，也不接管中断投递（HakiOS 已经带了
`osfmk/arm64/gic_v3_irq.c`，QEMU virt 就是 GICv3）。它做四件事：

1. 从引导器交过来的设备树里**读出**这块板真正有什么（GIC 的 dist/redist、调试 UART、
   timebase 频率），每个值都先校验再用；
2. 用内核自己的映射原语把那几段寄存器**映射**进内核地址空间；
3. 每一项**逐行报告**（所以拍一张屏就知道走到哪）；
4. 通过 QUP UART 提供**早期控制台输出** —— 这是唯一能把"每轮拍屏读日志"变成
   "直接抓串口"的东西，也是当前迭代速度的最大瓶颈。

## 默认关闭

**不传 `sdm660_shim=1` 时，这层一个寄存器都不碰。** 这一点是刻意的：
正在跑的 checkpoint 验证不能被一个还没在真机上验证过的层干扰。

| 启动参数 | 作用 |
|---|---|
| `sdm660_shim=1` | 启用整层（默认关闭） |
| `sdm660_verbose=1` | 每次设备树查找打一行 |
| `sdm660_uart=<pa>` | 直接指定 UART 物理地址（跳过设备树查找） |
| `sdm660_baud=<n>` | 波特率（默认 115200；不重配时钟，只用于日志） |
| `sdm660_uart_geni=1` / `sdm660_uart_legacy=1` | 强制 UART 的寄存器布局 |

## 文件

| 文件 | 内容 |
|---|---|
| `sdm660_shim.h` | 平台描述结构（`sdm660_board_t`）与接口 |
| `sdm660_shim.c` | 设备树解析、寄存器映射、初始化编排、`STARTUP(EARLY_BOOT, …)` 入口、统一的 `sdm660_log()` |
| `sdm660_gic.c` | 读 `GICD_PIDR2`/`GICD_TYPER`/`GICR_TYPER`，**报告**中断控制器是什么（不接管投递） |
| `sdm660_timer.c` | 核对 `CNTFRQ_EL0` 与设备树的 `timebase-frequency`，报告 `CNTPCT_EL0` |
| `sdm660_uart.c` | 早期控制台：8250 形态与 QUPv3 GENI 两种布局，带自旋上限 |

## 怎么被编进去

XNU 用 `osfmk/conf/files.arm64` 决定编译什么，一行 `path  standard` 就够：

```
osfmk/arm64/sdm660_shim.c		standard
osfmk/arm64/sdm660_uart.c		standard
osfmk/arm64/sdm660_gic.c		standard
osfmk/arm64/sdm660_timer.c		standard
```

`install_sdm660_shim.py` 做两件事（都幂等）：把 `kernel/sdm660/` 拷到
`osfmk/arm64/`，然后往 `files.arm64` 追加上面四行。内核 workflow 在
`apply_nonapple_guards.py` 之后调用它，`paths` 里也加了 `kernel/sdm660/**`
与脚本本身，所以改这一层会触发内核重建。

## 状态

**2026-11-21 18:40：已编入内核并通过构建**（`meizu15-kernel` artifact 正常生成）。

第一次进 XNU 树时踩了三个头文件问题，都记在 `TESTLOG.md` 里：

- `kern/printf.h` 不存在（`osfmk/kern/` 只有 `printf.c`，`kprintf` 声明在 `pexpert/pexpert.h`）；
- `#include <libkern/libkern.h>` 在 osfmk 里解析到 **BSD 那份**，把 `bsd/sys/types.h`
  带进来，`daddr_t` / `dev_t` / `clock_t` / `copyinstr` 全部和 osfmk 的定义冲突；
- `vsnprintf` 在两处都拿不到，所以这一层**自带格式化器**，内核侧只用
  `kprintf` 与 `va_list`。

结论：新增编译单元要在这棵树里活下来，先得过它的头文件审查 —— osfmk / bsd / pexpert
三套并行，同名头文件指向不同实现。

## 已知的空白（不要假装它已经完备）

| 项 | 现状 |
|---|---|
| **UART 地址** | 设备树里没有 `serial` 节点，所以默认按 boot-arg `sdm660_uart=<pa>` 指定。魅族 15 的调试串口地址需要实测（ABL 通常保留一个 BLSP UART 上电并把波特率配好） |
| **QUP 时钟** | 不重配时钟/电源域（那需要 RPMh 驱动）。本层假设固件已经 clock 了调试串口 —— 这是唯一能在开机第一秒就出日志的假设 |
| **GIC 节点** | ADT 里没有 `interrupt-controller`，所以 `pe_arm_map_interrupt_controller` 一直报 `failed to find`。本层会把实测到的 dist/redist/line 数打出来，用来**写那个节点**，但节点本身还没加 |
| **中断投递** | 不做。复用 HakiOS 的 `gic_v3_irq.c` |
| **PSCI / CPU 上电** | 还没有。多核 bring-up 需要它（`psci_smp_*` 在 HakiOS 里已有可用实现） |

## 判读

第一次带 `sdm660_shim=1` 启动，日志里应该出现（顺序固定）：

```
sdm660: translation layer starting
sdm660: gicv3 dist 0x17a00000, 416 lines, redist 0x17b00000 stride 0x20000, ctlr 0x..., typer 0x...
sdm660: timer CNTFRQ 0x124f800 (19200000 Hz), CNTPCT 0x...
sdm660: uart at 0x..., 115200 baud
sdm660: translation layer ready
```

缺哪一行就说明哪一项没成：

| 缺的行 | 含义 |
|---|---|
| `gicv3 …` 前面出现 "no interrupt-controller node" | ADT 还没这个节点 —— 用已知的 `0x17A00000 / 0x17B00000 / stride 0x20000` 去补 |
| `gic … does not look like a GICv3` | 映射到了，但读出来的 PIDR2 不对 → 地址或映射长度错了 |
| `CNTFRQ_EL0 reads 0` | 固件清掉了频率寄存器 —— 这是 `cpu_timebase_init` 那一段要看的同一个问题 |
| `no uart in the tree` | 用 `sdm660_uart=<pa>` 指定 |
| `uart does not answer at either layout` | 地址对但没上电，或布局第三种（需要读该 SoC 的 QUP 手册） |
