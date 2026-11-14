# 离线核对成品镜像（不需要手机）

`fastboot boot` 一次要传 12.5 MB，这台机器的 USB 只有 ~110 KB/s，一轮两分钟；
而且手机没有串口，出了问题只能看屏幕。所以**能离线证明的事情不要拿到手机上证明**。

payload 卷藏在 `SECTION COMPRESS` 里，`strings`、`mdir`、`check_payload_fv.py`
都看不到它的目录内容。要真正读回成品镜像里的文件树，得走完整条链：

```
boot.img
  └ Android boot header（4096 B 页）
      └ gzip 成员 → FV 33,554,432 B
          └ FFS 3F1A9C22-5B41-4A0E-9C77-2D618B4FE01A
              └ COMPRESSED section（PI_STD）
                  └ 48 MiB FAT16 → 文件树
```

## 一条命令

```bash
python3 scripts/ci/verify-payload-image.py artifacts/boot-meizu15-payload-debug.img \
        --uefiextract /path/to/UEFIExtract
```

`UEFIExtract` 只负责 `SECTION COMPRESS` 的解压（Tiano/UEFI 压缩算法），
其余（boot header、gzip、FAT 目录解析、断言）都是本仓库的代码：

| 文件 | 作用 |
|---|---|
| `scripts/ci/unpack-bootimg-fv.py` | boot.img → 固件卷 |
| `scripts/ci/fat_ls.py` | FAT16/FAT32 目录树（含长文件名） |
| `scripts/ci/verify-payload-image.py` | 串起来并断言布局，非零退出即不合格 |

拿 `UEFIExtract`（约 1 MB，不要装整个 UEFITool）：

```
https://github.com/LongSoft/UEFITool/releases
  UEFIExtract_NE_A76_win64.zip      Windows
  UEFIExtract_NE_A76_x64_linux.zip  Linux / CI
```

## 断言的内容

| 路径 | 为什么必须有 |
|---|---|
| `/bootaa64.efi` | prober 只探测 `dir[0]`（`/`），启动器必须落在卷根 |
| `/startup.nsh` | 菜单倒计时选中的是 `UEFI Shell`，Shell 执行它才到得了启动器 |
| `/EFI/BOOT/BOOTAA64.EFI` | 同一份启动器，标准位置，给别的固件/以后用 |
| `/EFI/UniXNU/unixnu.toml` | 启动配置 |
| `/EFI/UniXNU/Payloads/kernel.macho` | 15.8 MB arm64 内核 |
| `/EFI/UniXNU/Payloads/kernel.macho.adt` | 13,996 B 的 ADT |

另外两条更强的一致性检查：

1. `/bootaa64.efi` 与 `/EFI/BOOT/BOOTAA64.EFI` **逐字节相同**，且都以 `MZ` 开头；
2. `/startup.nsh` 的内容里必须出现 `bootaa64.efi` —— 否则菜单落到 Shell 就断了。

## 2026-11-14 对 58ae92b 的实测输出

```
image      : 12,480,512 bytes, 1 gzip member(s) -> FV 33,554,432 bytes
payload FAT: 50,331,648 bytes  sha256 9048a8856d126097…
volume     : FAT16, 512 B/sector, 2 sectors/cluster, 10 entries

  FILE      20,480  cluster=15463  /bootaa64.efi
  DIR            0  cluster=2      /efi
  DIR            0  cluster=15442  /efi/boot
  FILE      20,480  cluster=15443  /efi/boot/bootaa64.efi
  DIR            0  cluster=3      /efi/unixnu
  DIR            0  cluster=4      /efi/unixnu/payloads
  FILE  15,791,328  cluster=6      /efi/unixnu/payloads/kernel.macho
  FILE      13,996  cluster=15428  /efi/unixnu/payloads/kernel.macho.adt
  FILE         273  cluster=5      /efi/unixnu/unixnu.toml
  FILE          19  cluster=15483  /startup.nsh
```

`/startup.nsh` 只有 19 字节，内容 `fs0:\bootaa64.efi` —— 与 `58ae92b` 的改动一致
（旧版是 28 字节的 `fs0:\EFI\BOOT\BOOTAA64.EFI`）。

启动器本体（20,480 B，`MZ` 头，sha256 `f41f69e71b2b38d7…`）里的 UTF-16 字符串：

```
Meizu15Launcher: looking for %s
\EFI\UniXNU\Payloads\kernel.macho
Meizu15Launcher: no volume carries the payload (%r)
Meizu15Launcher: payload volume found, ADT %u bytes
Meizu15Launcher: DarwinBoot is not in this firmware (%r)
Meizu15Launcher: loader revision 0x%x, booting
Meizu15Launcher: the loader returned %r
\EFI\UniXNU\Payloads\kernel.macho.adt
```

即镜像里装的就是我们的启动器，路径找的是 `/EFI/UniXNU/Payloads/kernel.macho`。

## CI 里也跑这一套

`build-meizu15-uefi-payload.yml` 的 `debug` job 在 retarget 之后有一步
`Verify the finished image from the outside`：现场下载 Linux 版 UEFIExtract，
对**成品 boot.img** 跑同一个脚本，并把输出存成 `payload-image.txt` artifact。

之前的 `Assert the payload volume layout` 只检查打包中途用 `mdir` 造的卷；
这一步检查的是最终交付的那份字节，两者不重复。
