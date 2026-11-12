# 下一步：两版 CI 镜像（SimpleInit 死后怎么办）

## 现状（2026-11-12 23:xx，真机实测）

`boot-meizu15-hdrfix.img` 用 `fastboot boot` 在真机上跑通了：

```
BootShim → PEI → DxeCore → BDS → SimpleInitMain（崩）
```

屏幕能用（1080×1920 面板、GOP 正常），崩的是 SimpleInit 自己：

```
ESR 0x25 / ISS 0x4        translation fault, level 0
PC  SimpleInitMain+0x6FF3C
LR  SimpleInitMain+0xB5C5C   ← ldur w3, [x19, #-0x10]，x19 = 0
```

即 `SimpleInit` 内部的池释放函数拿到 NULL，还去读 `[ptr-0x10]` 的 `"Cphd"` 魔数。

本地直接改二进制不可行（已试，见 `TESTLOG.md`）：

- 重压 LZMA → BL 6 秒就放弃镜像
- 内层卷不压缩 → FFS 文件 size 只有 24 位（上限 16 MiB），29.6 MB 放不进去

所以只能回 CI 重编。

## 两个新增 workflow

### 1. `build-meizu15-nosimpleinit.yml` —— 验证链路（先跑这个）

把 SimpleInit 整个编掉，看 BDS 能不能落到 UEFI Shell / Boot Manager。

- 目的：**证明 SimpleInit 之前的所有环节都是好的**，把问题范围锁死在"一个应用"上。
- 预期：屏幕进 UEFI 交互界面（文字菜单 / Shell 提示符）。
- 若还是卡：说明 BDS 之后另有问题，那就要看 `build-log-nosimpleinit` 里的日志。

判读：

| 现象 | 含义 |
|---|---|
| 出现 Shell / 菜单 | 链路全通，只剩 SimpleInit 一个应用要修 → 走路线 2 |
| 黑屏但 USB 有反应 | BDS 之后的显示/输入有问题 |
| 一切照旧（还是崩在 SimpleInit） | sed 没生效，看 workflow 里 `=== AFTER ===` 那段输出 |

### 2. `build-meizu15-debug.yml` —— 拿到源码和行号

同样的镜像，改用 DEBUG 构建：

- 崩溃报告会带 `文件:行号`，不再是裸 RVA
- 额外上传 **`simpleinit-src`** artifact：整个 `GPLDrivers/Library/SimpleInit` 源码树
- 顺手在日志里 `grep` 了 `FreePool` / `magic` / `0x64687043`，直接告诉我们在哪个文件

拿到源码后就能精确改那个释放函数（加 `if (!ptr) return;`），再编一版。

## 怎么跑

```
GitHub → Actions → 左侧选 build-meizu15-nosimpleinit → Run workflow
（跑完再点 build-meizu15-debug）
```

跑完下载 artifact 里的 `boot-meizu15.img`，然后：

```powershell
adb reboot bootloader
fastboot boot <下载的 boot-meizu15.img>
```

只写内存，不碰分区；卡住了长按电源 12 秒。

## 注意

- 两个 workflow 都依赖 `edk2-msm-overlay/` 原样；不要同时改 overlay，否则会互相干扰。
- `nosimpleinit` 里的 sed 是"能匹配才改、改几行都打印"，所以日志里 `=== BEFORE ===` / `=== AFTER ===` 的对比就是证据。
- 如果 `gSimpleInitTokenSpaceGuid` 被别处引用（PCD 解析失败），构建会直接停在 EDK2 的 PCD 检查处 —— 那种情况把日志贴回来，改成"保留 PCD、只摘组件"的版本。
