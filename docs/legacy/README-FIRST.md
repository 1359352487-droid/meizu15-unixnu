# 早上第一眼：现在是什么状态

**一句话**：本地构建链路全部打通（内核、EFI payload、boot.img 都在这台 Mac 上生成），
**只差把手机插回来验证**。设备从凌晨 2:39 起就不在 USB 上了。

## 插上手机就行

```bash
cd /Users/mac/Documents/deepseek-harness/default-workspace
./tools/platform-tools/adb -s 881QAESM95THY reboot bootloader
./tools/platform-tools/fastboot -s 881QAESM95THY boot out/boot-meizu15-payload-full25.img
```

`fastboot boot` 只写内存，不碰任何分区。

后台还有一个 `out/watch-and-flash.sh` 在等（每 20 秒查一次 USB），插上会自动刷同一版，
并把 USB 状态写进 `out/flash-log.txt`。如果它和你的手动命令撞车，以先跑起来的为准。

## 刷哪一版

默认刷 **`out/boot-meizu15-payload-full25.img`**（最强配置）：

| 特性 | 值 |
|---|---|
| 内核 | 25 驱动全开，17,498,352 B，LZMA 压缩进 payload |
| 探针 | 130–139 + 140/141/143/142 |
| 根卷 | `rd=md0` + initrd（一个打印 `M15OK` 的 launchd 替身） |

备用的几版（`boot-meizu15-payload-*`）：

| 镜像 | 什么时候用 |
|---|---|
| `…-local.img` | 怀疑“本地构建”本身有问题时——最朴素的一版 |
| `…-probes3.img` | 想排除 LZMA 解压这一层时（减配内核、不压缩） |

## 看什么

屏幕最后一行 `last cp N`：

| N | 含义 | 下一步 |
|---|---|---|
| 139 | 连 `bsd_init()` 里的 140 都没到 | 刷 `…-local.img` 对照，定位是不是本地构建的问题 |
| 140 | 进了 `vfs_mountroot()` 没回来 | 在 `mockfs_mountroot` 里插探针 |
| **141** | **mockfs 挂载成功** | P2 达成，下一步是 142（exec init） |
| 143 | 挂载失败返回 | 看 panic 文本里的 errno，多半是 md0 没建立 |
| 142 | 挂载成功且走到 exec | 看有没有 `M15OK`，那说明 launchd 跑起来了 |
| 黑屏 | 早期就死了 | 刷 `…-local.img`，再不行查 `loader_return_after=60` 那条路 |

## 这一夜做出来的东西

| 文件 | 是什么 |
|---|---|
| `out/boot-meizu15-payload-*.img` | 五个可刷镜像（见上表） |
| `out/build-round.sh` | 一键：编内核 → 打 ESP → 编固件 → retarget（约 13 分钟） |
| `out/lzma-pack.py` | 把内核压成 EFI LZMA（DarwinBoot 会解） |
| `out/make-esp.sh` | hdiutil 造 FAT16 ESP（不依赖 mtools） |
| `out/watch-and-flash.sh` | 设备插上就刷，记录 USB 状态 |
| `out/STATUS.md` | 完整的过程记录（16 条补记，从环境搭建到 LZMA） |
| `out/LOCAL-BUILD.md` | 本地编内核的可复现步骤 |
| `out/LOCAL-PAYLOAD-BUILD.md` | 本地打包 payload 的可复现步骤（为 macOS 改的 9 处） |
| `out/kernel-nodev.macho` | 减配内核副本（16,512,424 B） |

## 关键的三个发现

1. **`Meizu15Launcher.c` 是死代码**——它不在任何 FDF 里，改它的 `Request.BootArgs` 没用。
   真正生效的启动参数是 ESP 里 `unixnu.toml` 的 `args`。
2. **payload 被 LZMA 压缩在 `FVMAIN_COMPACT` 里**——所以在 FD 里明文搜 `FAT16` 是搜不到的，
   这也是早上那个 CI 镜像同样搜不到的原因。
3. **16 MB payload 上限可以绕开**——DarwinBoot 现在认 LZMA 内核（首字节 `0x5D`），
   17.5 MB 的 25 驱动内核压到 4.37 MB 就行。
