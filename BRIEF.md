# 项目简报 — Meizu 15 跑 iOS 16

> 这份是**压缩版**，一次读完就能接手。完整细节在 `HANDOVER.md`，实测数据在 `docs/`。

---

## 一句话

在 Meizu 15（M8810，SDM660）上启动 XNU / iOS 16 用户空间。**前置工程已全部完成**，第一份可 `fastboot boot` 的 UEFI 镜像已产出并逐层验证；剩下的唯一动作是插手机实测。

---

## 思路

参照 [UniXNU-raphael](https://github.com/JohnLeeeeeee678/UniXNU-raphael)（K20 Pro，已到 SpringBoard）。启动链：

```
XBL → UEFI 固件卷 → BootShim → PEI → DXE → BDS → DarwinBoot → XNU → launchd → SpringBoard
```

我们要造的是中间那层 **UEFI 固件**，然后用用户态补驱动。

**设备身份选 iPhone 8 Plus（iPhone10,2 / D21AP）**：面板 1080×1920 @3x 与本机像素级一致；有 Home 键（Goodix GF52xx + `fp-keys`）。iPhone X 是 1125×2436，比例不对。

---

## 设备事实（全部实测）

| 项 | 值 |
|---|---|
| 机型 | Meizu 15，`M8810`，Flyme 7.0.2.2A / Android 7.1.1 / `NGI77B` |
| SoC | SDM660，ARMv8.0（A73/A53，**无 PAN、无 LSE**） |
| **BL** | `unlocked` + `critical unlocked: true`，`Verity mode: false` |
| **`fastboot boot`** | **可用，且接受改过的镜像**（实测：改 name 字段照样启动） |
| DRAM | 2 GiB @ `0x80000000` + 1.19 GiB @ `0x100000000`，span `0xCC7E0000` |
| GIC | GICv3，dist `0x17a00000`，redist `0x17b00000`，timer 19,200,000 Hz |
| 存储 | eMMC Samsung `DV6DMB`，`sdhci@c0c4000` |
| 面板 | 1080×1920 AMOLED 480dpi，MDSS DSI，`labibb` 双电源轨 |
| 触控 | **Synaptics DSX @ i2c-4 `0x20`**（不是 Goodix，也不是 Novatek） |
| boot 分区 | p50，LBA 1179648，131072 sectors（64 MiB） |

by-name 在 `/dev/block/platform/soc/*/by-name/`，**不是** `/dev/block/by-name/`。

---

## 已经做出来的东西

### 1. 原厂固件拆解完毕

```
xbl_a.img → LOAD#11 @0x7e380 → UEFI FV
  ├── PEI_CORE  155,672 B
  ├── SEC         7,504 B
  └── FV_IMAGE  1,415,801 B (gzip) → 解压 3,944,968 B → 67 个 PE32
```

67 个驱动已提取（3.73 MB），可用 `scripts/extract-xbl-dxe.py` **完整重建**。

### 2. 可启动镜像（核心成果）

`artifacts/boot-meizu15.img`，6,225,920 B，由 CI `build-meizu15-edk2msm` 构建。

逐层验证过：

```
头部      page_size 4096 / hdr_version 0 / kernel gzip   ← 与原厂逐项一致
kernel段  gzip(5,885,703) + DTB(333,101)
解压      BootShim(144 B) + outer FV(33,554,432 B)
卷内部    PEI_CORE(PE32 42,912 + DEPEX) + FV_IMAGE(GUIDED 5,831,512)
```

结构和原厂 `xbl_a` 同构——**启动链完整**。

加载地址 `0xCE000000` 算过：在 bank0 内，与 7 个 reserved-memory 区零重叠，最近隔 736 MiB。

### 3. 两条路线的取舍

| 路线 | 结果 |
|---|---|
| 自建 `Meizu15Pkg` | 能出 5 MiB 合法 DXE 卷，但**没有 SEC/PrePi 入口**，用不了 |
| **`edk2-msm` + overlay** | **成功**，产出可启动 boot.img |

换路原因：EDK2 不允许 `DXE_CORE` 当二进制 INF（`error F003`）。而 `edk2-porting/edk2-msm`（2608★）已有完整 `Silicon/Qualcomm/sdm660`，5 个 SDM660 机型都移植过，每机型只要 1 KB 的 `.dsc` + 700 B 的 `.fdf.inc`。

我们只加了：

```
edk2-msm-overlay/
├── configs/devices/meizu15.conf              屏/DPI/版本
├── Platform/Meizu/sdm660/
│   ├── meizu15.dsc                           1080x1920, 480dpi
│   ├── meizu15.fdf.inc                       ACPI 表
│   ├── platform.sh.inc                       覆盖打包 hook
│   ├── FdtBlob_compat/meizu15.dtb            333,101 B（从 boot.img 挖的）
│   └── tools/mkbootimg4096.py                自写的 boot 打包器
```

---

## 11 个坑（照这个顺序会踩到）

**自建包那条路：**

| 报错 | 根因 |
|---|---|
| `could not read Username for github.com` | `Zeex/subhook` 仓库已消失，`--recursive` 拉全量挂掉 |
| `syn.h: too many arguments to 'fpReach'` | Pccts 是 K&R 风格，新 GCC 拒绝 → 换 `edk2-stable202608` |
| `Failed building wheel for pylibfdt` | runner 是 Python 3.14，`PyInt_AsLong` 早删了 |
| `PYTHON_COMMAND: unbound variable` | `set -u` + `edksetup.sh` 的 bug |
| `Tool chain [GCC5] is not defined` | 新版只有 `GCC` / `GCCNOLTO` |
| `F003: RULE.COMMON.DXE_CORE` | **DXE_CORE 不能是 binary-only INF** |
| `TypeError: ... NoneType` | FDF 要 `SECTION PE32 = <file>`，类型名是 `DRIVER` 不是 `DXE_DRIVER`，且必须有 `SECTION UI` |

**edk2-msm 那条路：**

| 报错 | 根因 |
|---|---|
| Pccts 再次拒绝编译 | 宿主 GCC 太新 → 装 `gcc-12` 并切默认 |
| `build/rootfs.c is not found` | `--skip-rootfs-gen` 是陷阱：跳过生成但构建仍要它 |
| `llvm-ar: not found` (Error 127) | CLANG38 用 llvm-ar 做归档 |
| `abootimg: command not found` | 固件编好了，最后一步没打包 |
| **`Build done` 但没产物** | **`abootimg` 不支持 `--pagesize`，传了就打印帮助并 exit 0** → 自写打包器 |

最后那个最阴：假成功。日志写着 `Build done`，artifact 里只有一个 `.fd`。

---

## 怎么测（明天第一件事）

```powershell
adb reboot bootloader
fastboot boot artifacts\boot-meizu15.img     # 或直接跑 .\test-boot.ps1
```

**只写内存，不碰分区。** 按住电源 10 秒就能回 Flyme。何况还有解 BL 前的全分区备份。

| 现象 | 含义 |
|---|---|
| 出文字 / 菜单 | 全通了，进 Phase 4 |
| 亮了但全黑，几秒后重启 | 卡显示初始化（面板时序 / 双电源轨） |
| 完全无反应立刻重启 | BootShim 或 FD 加载失败 |
| 停在 fastboot 不动 | bootloader 拒绝了头部 |

---

## 下一步

1. **实测**（上面那条），看落在哪个现象
2. 黑屏 → 调 `PcdMipiFrameBufferWidth/Height` 和面板 init sequence
3. 通了 → 才轮到 XNU：CI 上编 ARMv8.0 安全内核（`CONFIG_XNU_NATIVE_PAN=n`），ADT 用 `scripts/make-meizu15-adt.py --iphone-model 8plus`
4. 最后是 eMMC 驱动——XNU 侧需要 `NativeEmmc`，**全世界都还没有**，这是真正的无人区

---

## 换机器的注意事项

- `dump/*.img` 被 gitignore（含序列号），重建命令在 `HANDOVER.md` §6.2
- **代理**：这台机 `github.com:443` 直连被掐，走 `127.0.0.1:7897`。仓库 `.git/config` 里写死了这个地址，换机器要改或删
- `tools/git/cmd/git.exe` 是 MinGit，workspace 自带 Python 3.12（含 `uefi_firmware`、`dulwich`）
- EDK2 **不能本地编**：Windows 上没 WSL、没 aarch64 交叉 gcc、没 Docker，只能走 CI

---

## 别忘的陷阱

- `su` 不认 `-c`，用 `echo 'cmd' | su`；每次可能弹 Flyme 授权窗
- `dd` 的 `bs=1M` 不认，用 `bs=1048576`
- `devinfo` 分区 4 KiB **全是零**，锁标志不在那
- `toolsfv` 分区名骗人，里面没有固件
- Lavender 的 `dram_size = 0xfe4c0000` 是 6 GB 机型，**这台是 `0xCC7E0000`**
- 触控：Lavender 是 Novatek，HakiOS 只有 Goodix，**这台是 Synaptics** —— 三个都不一样
- SDM660 是 ARMv8.0，**PAN 和 LSE 都不能编进 XNU**

---

## 文件在哪

```
meizu15-unixnu/
├── BRIEF.md                     ← 你正在读的
├── HANDOVER.md                  完整版（13 KB，含全部实测数据）
├── artifacts/boot-meizu15.img   可启动镜像 6,225,920 B
├── test-boot.ps1                一键测试（不 flash）
├── docs/                        hardware / bootloader / xbl-firmware / dxe-drivers / iphone-identity
├── scripts/                     make-meizu15-adt.py / extract-xbl-dxe.py / make-pkg.py
├── edk2-msm-overlay/            给 edk2-msm 的 6 个文件
├── edk2-firmware-build/         自建 Meizu15Pkg（67 个 stock 驱动，备料）
└── .github/workflows/           build-edk2msm.yml（主力）/ build-uefi.yml
```

**目前这镜像里是 edk2-msm 的开源驱动，不是那 67 个原厂驱动。** 触控、指纹、eMMC 真要能用，最后得把 `SdccDxe` / `DisplayDxe` / Synaptics 接回去——`edk2-firmware-build/` 就是为这一步备的料。
