# 交接文档 — Meizu 15 (M8810) 跑 iOS 16

> 生成于 2026-11-12 凌晨。这份文档的目标是：**换一台电脑、换一个 agent，能从这个仓库继续干下去，不需要重新摸一遍。**

---

## 0. 一句话状态

在 Meizu 15 上跑 XNU 的**前置工程已完成**：硬件摸清、引导链验证通过、原厂固件拆解完毕、**并且已经构建出一个 `fastboot boot` 可加载的 UEFI 镜像**（`boot-meizu15.img`，6,225,920 字节，头部字段与原厂完全一致）。

剩下的只有一步：**插上手机实测**。

---

## 0.1 最终产物（可启动镜像）

工作流 `build-meizu15-edk2msm` 的最新成功产物（run `43087541204`）：

```
boot-meizu15.img     6,225,920 B
  magic        ANDROID!
  kernel_size  6,218,804
  ramdisk_size 0
  page_size    4096        <- 与原厂 dump/boot.img 一致
  hdr_version  0           <- 与原厂一致
  kernel       位于 0x1000（第 1 页），内容是 gzip（与原厂一致）
SDM660_UEFI.fd      33,554,432 B   (32 MiB，EDK2 固件卷)
```

这份头部的每一项都对着原厂 `boot.img` 核过，不是猜的。`kernel` 段里是 `BootShim.bin + SDM660_UEFI.fd` 再 gzip，开机时 bootloader 解压 → 跳到 BootShim → BootShim 把卷复制到 `FD_BASE=0xCE000000` → 跳进 UEFI。

### 怎么拿到它

```
GitHub → Actions → build-meizu15-edk2msm → 选最新绿色的 run → Artifacts → boot-meizu15
```

### 加载地址已核对（`FD_BASE`）

BootShim 会把固件卷复制到 `FD_BASE = 0xCE000000`，再跳过去。这个地址对本机是安全的，算过：

```
FD 区    0xCE000000 .. 0xD0000000   (32 MiB)
bank0    0x80000000 .. 0x100000000  -> 完全落在 bank0 内
最近保留区 pstore 结束于 0xA0000000  -> 间隙 736 MiB
splash / adsp / modem / cdsp / pil_mba / wlan_msa / pstore 全部无重叠
```

这个值和 edk2-msm 给 whyred 等 SDM660 机型用的一样。如果实测发现 bootloader 自己占用了这段（表现是立刻重启、BootShim 都没跑完），备选地址按「避开 0x85000000-0xA0000000 的保留区、且留在 bank0」来挑，`0xB0000000` 或 `0xC0000000` 都可试，改 `configs/devices/meizu15.conf` 不够——`FD_BASE` 来自 `configs/sdm660.conf`，要在 overlay 里复制一份覆盖。

### 怎么测（需要插手机）

```powershell
# 1. 手机进 fastboot
adb reboot bootloader
# 2. 确认状态
fastboot devices
fastboot oem device-info       # 要看到 Device unlocked: true
# 3. 只加载不写盘（变砖风险为零，重启即恢复）
fastboot boot boot-meizu15.img
```

**预期**：`Sending 'boot.img' OKAY` → 屏幕亮起，出现 UEFI 字符界面 / SimpleInit 菜单 / 乌屏后自动重启（三种都算"固件跑起来了"）。

**恢复**：按住电源键 10 秒强制重启，回到 Flyme。因为 `fastboot boot` 只写内存，任何情况下分区都没动。

**判读**：

| 现象 | 含义 |
|---|---|
| 屏幕出现文字/菜单 | 固件完全跑通 —— 直接进 Phase 4 |
| 屏幕亮起但全黑，几秒后重启 | UEFI 启动了，卡在显示初始化（`DisplayDxe`/面板时序） |
| 完全无反应，立刻重启 | BootShim 或 FD 加载失败（地址/页大小/重定位） |
| 停在 fastboot 界面不动 | bootloader 拒绝了镜像头 |

---

## 1. 目标

在 Meizu 15（M8810，SDM660）上启动 XNU（iOS 16.7.16 用户空间），最终到达 SpringBoard 桌面。参照物是 Redmi K20 Pro 的 [UniXNU-raphael](https://github.com/JohnLeeeeeee678/UniXNU-raphael)（已到桌面）。

**设备身份选用 iPhone 8 Plus（iPhone10,2 / D21AP）**，理由见 `docs/iphone-identity.md`：
- 面板 1080×1920 @3x，和 Meizu 15 完全一致（iPhone X 是 1125×2436，比例不对）
- 有 Home 键（Goodix GF52xx + `fp-keys` 输入设备）

---

## 2. 设备现状（实测，非推测）

| 项 | 值 | 验证方式 |
|---|---|---|
| 型号 | Meizu 15，`M8810`，product `meizu_15_CN` | `getprop` |
| SoC | SDM660，`soc_id=317` | `/sys/devices/soc0/` |
| 系统 | Flyme 7.0.2.2A / Android 7.1.1 / `NGI77B` | `getprop` |
| 安全补丁 | 2018-04-05 | `getprop` |
| **BL** | **`lock_state: unlocked`，`critical unlocked: true`** | `fastboot oem device-info` |
| **AVB** | **`Verity mode: false`** | 同上 |
| **`fastboot boot`** | **可用，且接受被修改过的镜像** | 实测（见 §4） |
| `max-download-size` | 512 MiB | `fastboot getvar all` |
| DRAM | 2 GiB @ `0x80000000` + 1.19 GiB @ `0x100000000` = span `0xCC7E0000` | FDT `memory/reg` |
| GIC | GICv3，dist `0x17a00000`/`0x10000`，redist `0x17b00000`/`0x100000` | FDT |
| 定时器 | 19,200,000 Hz | FDT `timer/clock-frequency` |
| 存储 | eMMC，Samsung `DV6DMB`，`mmcblk0`，128 GB | `/sys/block/` |
| eMMC 控制器 | `sdhci@c0c4000`，`qcom,sdhci-msm-v5` | FDT |
| 面板 | 1080×1920 AMOLED（480 dpi），MDSS DSI，`labibb` 双电源轨 | `wm size`/`wm density`/dmesg |
| 触控 | **Synaptics DSX @ i2c-4 `0x20`** | dmesg `synaptics_dsx_i2c 4-0020` |
| 指纹 | Goodix `GF52xx`，`ro.meizu_fp_model` | `getprop` |
| splash 区 | `0x9d400000` / `0x2400000`，label `cont_splash_mem` | FDT `reserved-memory` |
| root | Flyme 官方 root 已开（`ro.flyme.root.state=1`），`su` 需屏幕点允许 | 实测 |

分区（512 B LBA）：

| 名 | 设备 | start LBA | sectors |
|---|---|---|---|
| xbl_a | p1 | 131072 | 7168 |
| xbl_b | p2 | — | 7168 |
| abl | p18 | 501760 | 2048 |
| ddr | p27 | 656400 | 2048 |
| misc | p43 | 860192 | 2048 |
| **boot** | **p50** | **1179648** | **131072（64 MiB）** |
| recovery | p51 | 1310720 | 131072 |
| system | p53 | 3538944 | 8388608 |
| **userdata** | **p57** | **13369344** | **230907871**（~110 GiB） |

by-name 路径是 `/dev/block/platform/soc/*/by-name/`，**不是** `/dev/block/by-name/`。

> 完整表格：`docs/hardware.md`

---

## 3. 已完成的四件事

### 3.1 分区备份（`dump/`，但被 .gitignore 排除）

12 个分区，145 MB：`boot recovery abl xbl_a xbl_b misc ddr devinfo sec toolsfv devcfg dsp`。

用户**另有解 BL 之前的全分区备份**，所以变砖不是风险。

### 3.2 引导链验证（`docs/bootloader.md`）

```
fastboot boot dump/boot.img          → Sending OKAY / Booting OKAY → 正常进系统
改 name 字段再 fastboot boot          → 同样 OKAY → 不验签
```

结论：**UEFI 阶段完全不需要写分区**，全程 `fastboot boot`。

### 3.3 固件拆解（`docs/xbl-firmware.md` + `docs/dxe-drivers.md`）

```
xbl_a.img (ELF, AArch64, 16 个 program header)
└── LOAD #11 @0x7e380 (vaddr 0x9fc00000, 1,792,000 B)
    └── UEFI FV (FFS2)
        ├── PEI_CORE                 155,672 B
        ├── SEC                      7,504 B
        └── FV_IMAGE               1,415,801 B  ← 24 字节 GUID 头 + gzip
            └── 解压后 3,944,968 B 的 DXE FV
                └── 67 个 PE32（65 DRIVER + 1 DXE_CORE + 1 APP）
```

关键驱动：`SdccDxe`(eMMC)、`DisplayDxe`(MDSS DSI)、`PmicDxe`(PM660)、`DALSys`、`ClockDxe`、`QcomBds`、`ButtonsDxe`、`DALTLMM`、`I2C`、`SPI`、`SPMI`、`DiskIoDxe`+`PartitionDxe`+`Fat`、Arm64 三件套。

> 全部 67 个已提取到 `dump/dxe/`（3.73 MB），并能用 `scripts/extract-xbl-dxe.py` 从 `xbl_a.img` **完整重建**（已实测）。

### 3.4 自建 EDK2 包（`edk2-firmware-build/`）

`Meizu15Pkg` 能把 67 个 stock 模块打包成一个**合法的 5 MiB 固件卷**：

```
MEIZU15PKG_UEFI.fd   5,242,880 B
  valid_header: True
  FFS files: 68  (DRIVER 65 | DXE_CORE 1 | APP 1 | PAD 1)
```

**但这是纯 DXE 卷，没有 SEC/PrePi 入口点，也没有 PEI 阶段 —— 不能直接启动。** 这是它和"可启动固件"的区别。

---

## 4. CI 调试踩过的 7 个坑（都已在 commit 里解决）

| # | 报错 | 根因 | 解法 |
|---|---|---|---|
| 1 | `fatal: could not read Username for github.com` | `UnitTestFrameworkPkg` 指向 `github.com/Zeex/subhook`，仓库已消失 | 只 init Brotli 两个 submodule |
| 2 | `syn.h:173: too many arguments to function 'fpReach'` | `edk2-stable202408` 的 Pccts 是 K&R 风格，GCC 13 拒绝 | 换 `edk2-stable202608` |
| 3 | `Failed building wheel for pylibfdt` | runner Python 3.14，`PyInt_AsLong` 早已删除 | 从 pip-requirements 里剔掉 pylibfdt |
| 4 | `PYTHON_COMMAND: unbound variable` | `set -u` + `edksetup.sh` 的 bug | 显式 export，并在 source 前后 `set +u` |
| 5 | `Tool chain [GCC5] is not defined` | 新版 EDK2 只有 `GCC` / `GCCNOLTO` | 改名 + `GCC_AARCH64_PREFIX` |
| 6 | `error F003: Don't Find common rule RULE.COMMON.DXE_CORE` | **DXE_CORE 不能是 binary-only INF** | 全部改走 FDF 的 `FILE` 记录，`[Components]` 留空 |
| 7 | `TypeError: ... NoneType` / `PE32 is not found` | FDF 语法：要 `SECTION PE32 = <file>`，类型名是 **`DRIVER`** 不是 `DXE_DRIVER`，且必须有 `SECTION UI` | 三条一起改 |

第 6、7 条的教训值钱：**EDK2 不允许把 `DXE_CORE` 当二进制 INF 打包**，而 FDF 里 `FILE` 记录的正确写法是：

```
FILE DRIVER = <guid> {
  SECTION PE32 = <path to .efi>
  SECTION UI   = "<name>"
}
```

---

## 5. 当前攻坚点：两条路线

### 路线 A（已完成 80%，卡在入口点）

自建 `Meizu15Pkg`，产出 DXE 卷。**缺 SEC + PrePi + PEI**，即"BL 跳进来之后的第一条指令"。

用户手上的 `xbl_a` 里有 `PEI_CORE`，但把它和 EDK2 的 `PeiMain` 拼成完整链条，需要重写整个平台初始化，工作量大。

### 路线 B（当前首选）

**用 [edk2-porting/edk2-msm](https://github.com/edk2-porting/edk2-msm)（2608★）。**

它是社区维护的 Qualcomm EDK2 移植框架，**已经有完整的 `Silicon/Qualcomm/sdm660` 层**，而且下面这些 SDM660 机型都已有移植：

```
Platform/Xiaomi/sdm660/  clover  jason  nitrogen  wayne  whyred
Platform/Qualcomm/sdm660/ (参考实现)
```

**每个机型只要两个小文件**：
- `Platform/Xiaomi/sdm660/whyred.dsc`（1067 B）
- `Platform/Xiaomi/sdm660/whyred.fdf.inc`（694 B）
- `configs/devices/whyred.conf`（~200 B）

而且 `build.sh` 会自动：编译 BootShim（ELF 入口）→ 构建 EDK2 → 用 mkbootimg 打包成 `boot-<device>.img`。

**已为本仓库准备好 overlay**：

```
edk2-msm-overlay/
├── configs/devices/meizu15.conf            SOC=sdm660, vendor=Meizu, 面板/DPI/版本
└── Platform/Meizu/sdm660/
    ├── meizu15.dsc                         1080x1920, 480dpi
    └── meizu15.fdf.inc                     ACPI 表（复用 SoC 级）
```

对应工作流：`.github/workflows/build-edk2msm.yml`（clone → overlay → `./build.sh -d meizu15` → 产出 `boot-meizu15.img`）。

**这条路的未知数**：edk2-msm 自述是 "Broken edk2 port"，SDM660 在 whyred 上能跑到什么程度未知；我们的 Meizu 15 硬件（面板初始化、触控）和 whyred 不同，可能卡在显示。

---

## 6. 新机器上怎么接着干

### 6.1 拉代码

```bash
git clone https://github.com/1359352487-droid/meizu15-unixnu.git
cd meizu15-unixnu
```

### 6.2 恢复被 .gitignore 排除的东西

仓库里**没有**分区镜像和提取好的驱动（含设备序列号，不适合公开）。重建：

```bash
# 需要手机
adb shell "echo 'dd if=/dev/block/platform/soc/*/by-name/xbl_a of=/sdcard/xbl_a.img' | su"
adb pull /sdcard/xbl_a.img dump/
pip install uefi_firmware
python3 scripts/extract-xbl-dxe.py dump/xbl_a.img --out-dir dump/dxe
python3 scripts/make-pkg.py --manifest dump/dxe_manifest.json \
        --dxe-dir dump/dxe --pkg-root edk2-firmware-build --force
```

`dump/dxe_manifest.json` **已在仓库里**，记录了每个模块的 GUID / 类型 / 大小 —— 即使不重新提取，也能据此核对。

### 6.3 工具链

Windows 上我用了 `MinGit`（`tools/git/cmd/git.exe`，2.56.1）和 workspace 自带的 Python 3.12（含 `dulwich`、`uefi_firmware`）。

**注意代理**：本机 `github.com:443` 直连被掐，必须走本地代理。已在仓库 `.git/config` 里写了：

```
http.proxy = http://127.0.0.1:7897
https.proxy = http://127.0.0.1:7897
```

换机器后这行要改成新机器的代理，或者删掉。

### 6.4 两个工作流

| 工作流 | 干什么 | 状态 |
|---|---|---|
| `build-meizu15-uefi` | 自建 Meizu15Pkg → 5 MiB DXE 卷 | **成功** |
| `build-meizu15-edk2msm` | edk2-msm 路线 → 可启动 boot.img | **进行中** |

手跑：仓库 → Actions → 左侧选 workflow → `Run workflow`。

推送会自动触发（`paths` 命中对应目录）。

---

## 7. 下一步（按优先级）

1. **等 `build-meizu15-edk2msm` 出结果。** 成功 → 拿到 `boot-meizu15.img` → `fastboot boot` 实测。失败 → 看日志修。
2. **如果 edk2-msm 那条路走通**，屏幕上会先出现 Ronin/SimpleInit 的菜单或 UEFI shell，那就说明固件活了。
3. **如果卡在显示**：把 `PcdMipiFrameBufferWidth/Height` 和面板初始化对上 Meizu 15（1080×1920，AMOLED 双电源轨）。
4. **UEFI 通了之后**，才轮到 XNU：编译 ARMv8.0 安全的内核（`CONFIG_XNU_NATIVE_PAN=n`，见 `configs/meizu15_defconfig`），ADT 用 `scripts/make-meizu15-adt.py --iphone-model 8plus` 生成。
5. **最后才是 eMMC 驱动** —— XNU 侧需要 `NativeEmmc`，全世界都还没有。

---

## 8. 事实与陷阱清单（别忘）

- `su` 不认 `-c`，要用 `echo 'cmd' | su` 或 `su 0 cmd`；每次可能弹 Flyme 授权窗
- `dd` 的 `bs=1M` 不认，用 `bs=1048576`
- `/dev/block/by-name/` 不存在，用 `/dev/block/platform/soc/*/by-name/`
- `devinfo` 分区 4 KiB **全是零**，BL 锁标志不在那
- `toolsfv` 分区名字骗人，里面**没有固件**
- FDT 完整可读：`/proc/device-tree`；整份 334 KB 在 `/sys/firmware/fdt`（需 root）
- SDM660 是 **ARMv8.0**（A73/A53）：`PAN` 和 `LSE` 都不能编进 XNU
- Lavender 的 `dram_size = 0xfe4c0000` 是 6 GB 机型，**这台是 `0xCC7E0000`**
- Meizu 15 触控是 **Synaptics**，Lavender 是 Novatek，HakiOS 只有 Goodix —— 三个都不一样

---

## 9. 仓库文件清单

```
meizu15-unixnu/
├── board.toml                       板级定义（UniXNU 用）
├── configs/meizu15_defconfig        ARMv8.0 安全内核配置
├── docs/
│   ├── hardware.md                  全部硬件实测数据（最有用）
│   ├── bootloader.md                BL 能力验证
│   ├── xbl-firmware.md              XBL 结构 + 压缩卷分析
│   ├── dxe-drivers.md               67 个驱动清单
│   └── iphone-identity.md           iPhone 8 Plus 身份的选择依据
├── scripts/
│   ├── make-meizu15-adt.py          ADT 生成器（--iphone-model {x,8,8plus}）
│   ├── extract-xbl-dxe.py           从 xbl_a 抠驱动
│   ├── make-pkg.py                  生成 EDK2 包装
│   └── push-api.py                  REST API 推送（记录了 409 坑）
├── edk2-firmware-build/Meizu15Pkg/  67 个 .inf + .efi + dec/dsc/fdf
├── edk2-msm-overlay/                给 edk2-msm 的三个 overlay 文件
├── .github/workflows/
│   ├── build-uefi.yml               自建包（已成功）
│   └── build-edk2msm.yml            edk2-msm 路线（进行中）
└── dump/dxe_manifest.json           67 个模块的元数据（唯一进仓库的 dump 产物）
```

---

## 10. 参考项目

| 项目 | 用途 |
|---|---|
| [Chromitsune/UniXNU-Lavender](https://github.com/Chromitsune/UniXNU-Lavender) | 唯一的 SDM660 XNU 移植（未完成，停在 Phase 3） |
| [edk2-porting/edk2-msm](https://github.com/edk2-porting/edk2-msm) | **当前主攻**，有完整 sdm660 silicon 层 |
| [JohnLeeeeeee678/UniXNU-raphael](https://github.com/JohnLeeeeeee678/UniXNU-raphael) | K20 Pro 的 turnkey 方案，已到 SpringBoard |
| [marnov999/raphael-ios-gpu](https://github.com/marnov999/raphael-ios-gpu) | 另一条 shim 路线，更激进 |
| [oosexgang/hakios-xnu-kernel](https://github.com/oosexgang/hakios-xnu-kernel) | HakiOS 主仓，移植方法论写得好 |
| [yang232138/uefi-lmi](https://github.com/yang232138/uefi-lmi) | Mu-Silicium + BootShim + GitHub Actions 打包 boot.img 的样板 |
| [Project-Silicium/Mu-Silicium](https://github.com/Project-Silicium/Mu-Silicium) | Mu-Silicium 主仓 |

---

## 11. 安全提醒

**那个 GitHub fine-grained token 应该已经撤销。** 如果没撤，去 https://github.com/settings/personal-access-tokens 删掉 `meizu15-push`。它曾经明文出现在对话里。

仓库是 public 的，里面**没有**设备序列号（已脱敏为 `<YOUR-SERIAL>` / `<YOUR-CHIPID>`），也没有分区镜像。
