# 从这里继续：Meizu 15 上的 XNU 移植

> 写于 2026-11-23 20:54。上一段会话的终点：**探针 139 命中，内核到达 `bsd_init()`**。
> 这份文档写给"下一个接手的人/agent"，包含目标、现状、已放弃的方案、下一步，
> 以及在不同操作系统上怎么把流程跑起来。
>
> 配套文档：`PROJECT.md`（项目全景与探针地图）、`ROADMAP.md`（到可用 iOS 的分阶段路线）、
> `../TESTLOG.md`（逐轮实测记录，20 万字符，按时间倒序读最后几节即可）。

---

## 0. 一句话状态

内核能在 Meizu 15 上启动，跑完 `kernel_bootstrap_thread()` 的全部步骤，**站在 `bsd_init()` 门口**；
它现在缺的是一个能挂载的根文件系统。

---

## 1. 目标

把 Apple 开源的 XNU（`xnu-8796.121.2`，Darwin/iOS 16.5 的内核）移植到魅族 15（M8810）。

| 层 | 目标 | 状态 |
|---|---|---|
| L1 | 打印日志、建 VM、启动第一个内核线程 | ✅ 2026-11-22 00:17，探针 **117** |
| L1.5 | `kernel_bootstrap_thread()` 走完，进入 `bsd_init()` | ✅ 2026-11-23 00:41，探针 **139** |
| L2 | 根卷 + `bsd_init` + launchd + SpringBoard | ⬜ **下一个战场** |
| L3 | 显示、触摸、GPU、音频、Wi-Fi、蜂窝 | ⬜ 有参照项目 |

"完美运行 iOS 16" 的现实边界见 `ROADMAP.md` §0：桌面/触摸/Wi-Fi 有先例，
GPU 加速极难，相机/App Store/iCloud **不可能**。

---

## 2. 当前进度：探针地图

所有位置都已实测验证（`reason 2` 命中或轨迹可见）：

```
arm_init        10 11 30 31 32 33 34 35 12 40 41 42 43 44 13 14 15 16
arm_vm_init     45 58 59 60 54 55 56 57 50 51 52 53 46 61
physmap         82 84 86 88 80 66 68 69 70 71 67 83 85 87 89 81 62 63 64
pmap_bootstrap  47 48 96 97 98 90 91 92 93 94 95
arm_init 尾部   24 25 26 38 [跳过 PE_init_cpu] 39 36 37 27 28
machine_startup 72 73 74
kernel_bootstrap        75 76 77 78
                        100 101 102 103 104 105 106 107
load_context            108 109 110 111 112 113
kernel_bootstrap_thread 114 115 116 117 118 ... 123
                        124 125 126 127 [跳过 PE_init_iokit] 128 129
                        130(spllo) 131 132 133 134 [跳过 OSKext] 135
                        136(machine_lockdown) [跳过 PE_lockdown_iokit] 137
                        138 [跳过 kperf_init_early] 139  ← 现在这里
bsd_init()              ← 下一格，还没有探针
```

**判读方式**：刷机后看屏幕顶部的 `last cp N`。
- `reason 2` = 命中了请求的 checkpoint
- `reason 0` = 崩了或超时返回

---

## 3. 已放弃 / 已跳过的方案（连同原因，别重复踩）

### 3.1 跳过的内核调用（都是 Apple 平台代码或缺硬件）

| 调用 | 原因 |
|---|---|
| `PE_init_cpu()` | 开源树无源码（`pexpert/pexpert/arm64/` 只有头文件），符号被 strip |
| `PE_init_iokit()` | 同上 |
| `PE_lockdown_iokit()` | 同上 |
| `OSKextRemoveKextBootstrap()` | 抛弃引导段属优化，跳过只是多占几 MB 地址空间 |
| `kperf_init_early()` | PMU 性能计数器，启动不需要；且它会让整轮不返回 |

### 3.2 放弃的技术方案

| 方案 | 结论 |
|---|---|
| `git clone --depth 1 <HakiOS>` | **必须加 `--branch main`** —— HakiOS 默认分支改成了 `common`（Kconfig 树，没有 `xnu/apply.sh`） |
| 用 `git push` 推代码 | `github.com:443` 经常整段不通；**用 GitHub API**（`scripts/ci/api_push.py`） |
| 靠"run 是绿的"判断构建成功 | 这个 workflow 有三处吞错（`\| tail \|\| echo`、`continue-on-error: true`、`if-no-files-found: warn`）。**只认 artifacts** |
| `INTSTACK_SIZE` 改大"无效" | **错**：那次测量在 PAN 修复之前。256 KiB 之后 96/97 立刻通过 |
| 猜 `machine_thread` 的成员名（`pc`、`act_cpudatap`） | 这两个成员在本树里**不存在**；要用偏移从 `_machine_load_context` 反汇编里取（`act_cpudatap`=0x1c8、`kstackptr`=0x158） |
| `extern char BootCpuData[]` | 符号不存在；用 `cpu_datap(0)` |
| `find(root, "osfmk/arm/arm_vm_init.c")` | 路径是 **`osfmk/arm64/arm_vm_init.c`** |
| `S3_3_C4_C4_0` 当 Apple 专属寄存器 | 它就是 **`FPCR`**，ARMv8.0 支持 |
| 保护 `loader_log` 不被 `ml_static_mfree` 释放 | 判断错了：`loader_log` 在 `__DATA` 段（内核镜像内部），不在被释放范围。**这个 bug 仍未解决，见 §5.2** |

### 3.3 推倒的四道墙（记住它们的模式，后面还会遇到）

1. **`PAN`** —— ARMv8.0 没有这个寄存器，`msr pan` 直接未定义指令异常
2. **`TopOfKernelData` 的 `0x100` 尾巴** —— `DarwinBoot.c` 的 `Used` 要按 16 KiB 对齐
3. **中断栈耗尽** —— `INTSTACK_SIZE`/`EXCEPSTACK_SIZE` → 64*4096
4. **`XNU_MONITOR` 分支** —— `PE_slide_devicetree()` 只在 `#if XNU_MONITOR` 里，BCM2837 走 `#else`，**设备树从没被 rebase**；修法是在 `#else` 里补 `SecureDTInit()`

模式：**源码看起来没问题，行为却矛盾——因为源码假设了 Apple 硬件有、ARMv8.0 没有的东西。**

---

## 4. 下一步

### 4.1 主线：P2 —— 给内核一个根卷

`bsd_init()` 里第一件大事是 `vfs_mountroot()`。现在没有任何可挂的卷。

1. **读 Meizu 15 的 GPT**（只读，不动设备）
   ```bash
   fastboot getvar all 2>&1 | tee out/getvar.txt
   # 或从 adb 里读 /proc/partitions、/dev/block/by-name/
   ```
2. **参照同 SoC 项目**（先读代码，不要写）：
   - `appleiPodTouch4/UniXNU-r11` —— OPPO R11，SDM660、1080×1920 AMOLED、eMMC，状态 `available`
   - `qine233/UniXNU-clover` —— 小米平板 4 LTE，eMMC + mockfs 根，APFS 探测中
   - 路径：它们的 `board.toml`、`patches/`、eMMC/SDHCI 驱动
3. **两条根卷路线**
   - `mockfs`（clover 已走通）：先用 eMMC 后端 + 内存文件系统把 `bsd_init` 推过去
   - `APFS`：最终目标，需要读 Apple 的 `SystemOS`/`Data` 卷

### 4.2 支线：修日志通道（不阻塞主线，但早晚要修）

见 §5.2。

### 4.3 支线：UART 串口控制台（投入产出比最高）

现在每轮 20–30 分钟，一半耗在"拍照 → 人读 → 判读"。串口一旦通，日志实时进电脑。
并行会话已经在写 `kernel/sdm660/sdm660_uart.c`（默认关闭，`sdm660_shim=1` 才启用）。

---

## 5. 已知问题（未解决，写清楚免得下一个人重新发现）

### 5.1 `payload-fv.txt` 报 `payload file NOT found in the outer volume`

但 `UEFIExtract` 能完整提取出 payload FAT 卷（含 16,283,328 字节的 kernel），
镜像也能正常引导。**判断：检查脚本的误报，不影响功能，低优先级。**

### 5.2 日志在 `cp 70` 之后断裂

`reason 2, checkpoint 139` 时 `log 1307 bytes`，轨迹停在 `cp 70`，
中间 82–98、100–138 全部缺失，但 `cp 139` 那 7 个字节确实写进去了。

已排除：缓冲区容量（1.5 MB）、环模式（`LOADER_LOG_HEAD` 256 KB）、
"低地址被 `ml_static_mfree` 释放"（`loader_log` 在 `__DATA` 段）。

**影响**：`last cp` 通道正常（它只在返回时写一行），所以判读不受影响；
但看不到中间轨迹。进了 `bsd_init` 之后会需要完整轨迹。

### 5.3 macOS 虚拟机与 USB

如果 macOS 跑在虚拟机里，**adb/fastboot 可能看不到手机**（USB 直通问题）。
那台机器只能做"改代码 + 跑 CI + 下载镜像"，刷机和拍照仍需在有 USB 的主机上。

---

## 6. 环境与工具

### 6.1 仓库与凭据

| 项 | 值 |
|---|---|
| 仓库 | `github.com/1359352487-droid/meizu15-unixnu` |
| Token | 环境变量 `GH_TOKEN`，或放在 `~/.gh-token`（`repo` 或 `contents: write`） |
| 设备 | Meizu 15，adb 序列号 `881QAESM95THY` |
| 上游 | `oosexgang/hakios-xnu-kernel`，**必须 `--branch main`** |
| XNU | `xnu-8796.121.2`（HakiOS 的 `xnu/apply.sh` 自己拉） |

### 6.2 macOS

```bash
# 依赖
brew install python3 android-platform-tools
# 若没有 android-platform-tools：brew install --cask android-platform-tools

# 凭据
echo "<PAT>" > ~/.gh-token

# 一轮（全部走 GitHub API，不需要 git push）
python3 scripts/ci/api_push.py scripts/ci/apply_nonapple_guards.py "probe xxx"
python3 scripts/ci/api_round.py <那个提交的 sha>
# 产物在 out/boot-meizu15-payload-debug.img

# 刷机（需要 USB 直通）
adb -s 881QAESM95THY reboot bootloader
fastboot -s 881QAESM95THY boot out/boot-meizu15-payload-debug.img
```

### 6.3 Windows（上一段会话用的环境）

```powershell
# git（不在 PATH 里）
D:\WorkBuddy\resources\vendor\PortableGit\cmd\git.exe
# adb / fastboot
C:\Users\54714\Desktop\搞机工具箱9.91\adb.exe
C:\Users\54714\Desktop\搞机工具箱9.91\fastboot.exe
# 脚本同样在 scripts/ci/ 下
python scripts\ci\api_push.py scripts\ci\apply_nonapple_guards.py "probe xxx"
python scripts\ci\api_round.py <sha>
```

### 6.4 CI

| Workflow | 作用 | 时长 | 产物 |
|---|---|---|---|
| `build-meizu15-xnu-hakios-v2.yml` | 编内核 | 6–8 min | `meizu15-kernel`（5.8 MB，`kernel.macho.gz`） |
| `build-meizu15-uefi-payload.yml` | 打包 payload | 6–10 min | `meizu15-uefi-payload-debug-full`（5.2 MB，`boot.img`） |

**只看 artifacts，不看 run 颜色。** 这个 workflow 会吞错误。

---

## 7. 一轮迭代是怎么做的

```
1. 改 scripts/ci/apply_nonapple_guards.py（所有内核补丁都在这一个文件里）
2. python3 scripts/ci/api_push.py scripts/ci/apply_nonapple_guards.py "message"
3. python3 scripts/ci/api_round.py <sha>      # 等内核 → 换 inputs → 提交 → 等 payload → 下载
4. fastboot boot out/boot-meizu15-payload-debug.img
5. 拍照，读 last cp N
6. 按 N 决定下一轮埋哪一段探针
```

**改 launcher 的 checkpoint**（只改引导参数，不重编内核）：

```bash
python3 - <<'PY'
import re, pathlib
p = pathlib.Path("edk2-msm-overlay/Platform/Meizu/sdm660/Meizu15Launcher/Meizu15Launcher.c")
raw = p.read_bytes().decode("utf-8")
raw = re.sub(r'L"-v loader_return_after=60 checkpoint=\d+"',
             'L"-v loader_return_after=60 checkpoint=139"', raw)
p.write_bytes(raw.encode("utf-8"))     # 注意保持 CRLF
PY
python3 scripts/ci/api_push.py edk2-msm-overlay/Platform/Meizu/sdm660/Meizu15Launcher/Meizu15Launcher.c "launcher: checkpoint 139"
```

只改 launcher 时不需要 `api_round.py` 的前半段（内核没变），但直接跑它也没关系 ——
它会发现 `kernel.macho` 没变并停下，然后需要手动等 payload（或用 `api_round.py` 重新跑一遍）。

---

## 8. 写新探针的规矩

1. **编号不要撞**：10–45 是 HakiOS 上游的；46–71 `arm_vm_init`；80–89 是**索引探针**
   （`loader_checkpoint(80 + ptov_index)`）；90–98 `pmap_bootstrap`；100–139 `kernel_bootstrap`。
   140+ 可用。
2. **锚点必须唯一**：`text.count(needle) != 1` 就 `raise SystemExit`，别猜。
   `PE_init_cpu` / `PE_init_iokit` 这类调用在同一个文件里出现两次，要用上下文组合。
3. **幂等**：插入前检查标记是否已存在。
4. **在真文件上验证**：`out/` 里有源码副本（`startup.c`、`arm_vm_init.c`、`cpu.c`…），
   写个测试脚本跑一遍，确认插入位置和幂等性，再提交。
5. **改 `scripts/ci/*.py` 会触发内核重建**（它在 workflow 的 `paths` 里）。

---

## 9. 和上游（HakiOS）的关系

- 我们**消费** HakiOS 的 patch（`xnu/apply.sh`），不修改它
- **必须钉 `main` 分支**：默认分支已是 `common`（Kconfig 树，没有 `xnu/apply.sh`）
- 上游一直在更新；如果 `main` 也变了导致构建失败，用 CI 日志里的 `error:` 定位，
  必要时 pin 到具体 commit（`git clone` 后 `git -C hakios checkout <sha>`）

---

## 10. 给接手者的第一条建议

先跑一次完整流程确认环境是通的（不用改任何代码）：

```bash
python3 scripts/ci/api_round.py $(git rev-parse --short HEAD)
```

它会等构建、下载镜像到 `out/`。刷上去拍张屏，如果看到 `last cp 139` 和 `reason 2`，
说明一切正常，可以从 §4.1 开始。
