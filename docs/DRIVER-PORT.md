# 从"苹果驱动"到"魅族 15（SDM660）驱动"：到底要写什么、先写什么

> 写于 2026-11-21，内核已经能启动、能打印日志、卡在 `cpu_timebase_init()` 的时刻。
> 这份文档回答一个具体问题：要让 iOS 16 的内核真正**调用**这块手机的硬件，
> 需要把哪些"苹果专属 → SDM660"替换掉，顺序是什么，哪些现在根本不用碰。

---

## 0. 先纠正一个前提

**开源 XNU 里几乎没有设备驱动可"移植"。** Apple 的硬件驱动（IOKit 家族：
`AppleAIC`、`AppleS5L*`、`AppleDCP`、`AppleANS`、`AppleSDX`…）全在闭源的
`kernelcache` 里，开源树只有平台层和极少量参考代码：

| 开源树里有的 | 说明 |
|---|---|
| `pexpert/pexpert/arm64/BCM2837.h` | 树莓派 3 的 GPIO / mini UART 寄存器定义（**我们现在编的就是这个平台**） |
| `pexpert/pexpert/arm64/AIC.h` | Apple 中断控制器（AIC）的头，实现闭源 |
| `pexpert/pexpert/arm64/arm64/board_config.h` | 板级开关（`XNU_MONITOR` 等） |
| `osfmk/arm/pmap/*`、`arm_vm_init.c` | VM / 页表，**这部分是开源的，也是我们正在打通的** |
| `osfmk/arm64/cpu.c`、`machine_routines*` | CPU / 时间基抽象，调用 Apple 的 `rtclock_timebase_func` |

所以"移植驱动"在这个项目里的真实含义是：**在 `pexpert` / `osfmk` 层写裸机驱动**
（直接操作寄存器），或者像 HakiOS 那样在 `drivers/` 下写"内核态驱动"，
而不是把某个 `IOKit` 驱动搬过来。

---

## 1. 已经能复用的三样东西

| 能力 | 来源 | 状态 |
|---|---|---|
| **GICv3 中断控制器** | HakiOS 的 `xnu/files/osfmk/arm64/gic_v3_irq.c` | 存在；QEMU `virt` 机器就用 GICv3，而 **SDM660 也是 GICv3** → 结构可直接用，缺的是 ADT 节点 |
| **ARM generic timer** | 架构标准（`CNTPCT_EL0` / `CNTV_*`） | 正在验证（当前卡点就是 `cpu_timebase_init()` 里的 `ml_get_hwclock()`） |
| **Framebuffer** | UEFI 已经交给我们（`DarwinBoot` 的 return stub 用它画条码/日志） | 可用 → 早期日志**不需要显示驱动** |

---

## 2. 替换矩阵：Apple 假设 vs SDM660 实物

| 功能 | XNU 里的"苹果路径" | 魅族 15 上是什么 | 我们有吗 | 当前目标需要吗 |
|---|---|---|---|---|
| 中断控制器 | AIC（`AIC.h`，实现闭源） | **GICv3**（dist `0x17A00000`，redist `0x17B00000`，stride `0x20000`） | HakiOS 有驱动，ADT 缺节点 | ✅ **需要** |
| 时间基 / 时钟 | `rtclock_timebase_func`（Apple 抽象） | ARM generic timer（`CNTPCT_EL0`，19.2 MHz） | 验证中 | ✅ **需要** |
| 串口日志 | Apple UART / BCM mini UART | 高通 **QUP**（GENI，`0x0C1xxxxx` 系列） | 无 | ⚪ 可选（framebuffer 已够） |
| 存储 | Apple NAND / NVMe | **eMMC**，`sdhci-msm-v5` @ `0xC0C4000` | HakiOS 只有 **UFS**（`hakios.ufs.c` / `hakios.ufshc.c`） | ❌ 不用（阶段 6 之后） |
| 文件系统 | APFS（闭源） | 同左（iOS 的根卷就是 APFS） | HakiOS 的 `hakios.apfs_check.c` 只能看，不能挂 | ❌ 不用 |
| 显示 | Apple DCP / IOMobileFramebuffer | **MDSS + DSI 面板** | 无 | ❌ 不用 |
| GPU | Apple AGX | **Adreno** | HakiOS 有 `hakios.adreno.c` / `hakios.gpu.cpp` | ❌ 不用 |
| 电源 / 时钟域 | Apple PMGR | **RPMh / RPM**（`apps_rsc`） | 无 | ❌ 不用 |
| 输入 | Apple 触摸（`AppleMultitouch`） | **FocalTech / Goodix** | HakiOS 有 `hakios.focaltech_fw.h`、`hakios.goodix.c` | ❌ 不用 |
| 无线 | Apple WiFi/BT | **WCN3990（QCA）** | HakiOS 有整套 `drivers/wifi/*` | ❌ 不用 |
| 基带 | Apple baseband | **高通 MPSS**（QMI / IPA） | HakiOS 有 `hakios.mpss.c`、`qmi.c`、`ipa.c` | ❌ 不用 |

**结论：为了走到"第一个内核线程"，只需要两样 —— GICv3 和 generic timer。**

---

## 3. 为什么先不要写驱动

内核现在连 `machine_startup()` 都还没进去。这个阶段的失败**看起来像驱动问题，
实际上都是内核自身的假设问题**，今天已经踩了四个：

| 看着像"缺驱动" | 实际是 |
|---|---|
| `reason 0`，一行日志都没有 | `PAN` 寄存器在同一位置不存在（ARMv8.0 vs v8.2） |
| 第二次 slide 的 `orig_va` 永远差 `0x100` | 引导器把 return stub 计入 `Used` 时只对齐到 16 字节 |
| `pmap_bootstrap()` 第一条语句都进不去 | 中断栈 64 KiB 被 `arm_vm_init()` 的调用链吃光 |
| 设备树查一次就崩 | BCM2837 走 `#else` 分支，`PE_slide_devicetree()` 从来不执行 |

写驱动不会让这四个问题少一个。**先把内核跑到能调度，再谈硬件。**

---

## 4. 真要做的时候，顺序与落点

### 4.1 GICv3（最先，且几乎免费）

1. 给 `inputs/meizu15-8plus.adt` 补 XNU 期望的节点形状（`interrupt-controller`，
   带 `reg` = dist/redist 地址、`interrupt-controller` 空属性、`#interrupt-cells`）。
   现在屏幕上那两条 `pe_arm_map_interrupt_controller: failed to find the
   interrupt-controller` 就是它缺失的直接证据。
2. 复用 HakiOS 的 `gic_v3_irq.c`（它已经在 QEMU virt 上工作过）。
3. 验收：中断能进来，`CPUs` 之间的 IPI 能通。

### 4.2 串口（强烈建议，早做）

现在所有日志都靠 `loader_log` + framebuffer，**必须拍照**才能读，这是当前迭代速度
的最大瓶颈（每轮 20–40 分钟里有一半时间花在"拍屏→人读"上）。
接上 QUP 之后可以 `fastboot oem` 或 USB 串口直接抓日志。
落点：`pexpert/pexpert/arm64/` 下新增一个板级 UART 后端，照 `BCM2837.h` +
`serial_init()` 的接法写。

### 4.3 eMMC（能挂根卷才有意义）

`sdhci-msm-v5` @ `0xC0C4000`。HakiOS 的 `hakios.ufshc.c` 是 UFS 控制器抽象，
**不能直接搬**，但它的"命令队列 / DMA 描述符 / 时钟门控"结构可以照抄。
一块 eMMC 到能读写块设备，是一到两周量级。

### 4.4 显示 / 输入 / 电源 / 无线 / 基带

这些才是真正的"驱动移植"，每一项都是独立项目：Adreno 需要自己的微码与 ABI，
WCN3990 需要固件与 `wlan` 协议栈，MPSS 需要 QMI。**在本项目定义的目标里全部不需要。**

---

## 5. 复用 HakiOS 的正确姿势

HakiOS 是"内核态裸机驱动 + 设备配置"的模型：`drivers/*/hakios.*.c` 是驱动，
`drivers/*/conf/*.conf` 是编译开关，`devices/<device>/device.conf` 选驱动。
**它当前只配了 `qemu` 一个设备**（走 virtio），真机驱动（UFS / Adreno / WiFi / modem）
是摆在那里的参考实现。

读它的价值在于：它已经替我们解决了"没有 IOKit 怎么活"这个问题 —— 锁、内存、
线程、中断注册，全都在 XNU 的约束下写过一遍。这比读 Linux 驱动更有用。

但要注意两个不匹配：

- **存储**：魅族 15 是 eMMC，HakiOS 写的是 UFS；
- **SoC**：HakiOS 的参考驱动面向更晚的骁龙（Adreno/MPSS 代际），SDM660 的寄存器布局要另查。

---

## 6. 一句话总结

**现在不写驱动**：先把 `cpu_timebase_init()` → `machine_startup()` → 第一个内核线程打通。
之后第一件事是补 **GIC 的 ADT 节点**（几乎免费，且卡着中断），第二件是 **QUP 串口**
（把"拍屏读日志"变成"直接抓串口"，迭代速度翻倍）。存储、显示、无线、基带都在本项目目标之外。
