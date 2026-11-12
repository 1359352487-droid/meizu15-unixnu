# 真机测试记录

## 2026-11-12 23:0x — 第一次 `fastboot boot` 自定义 UEFI 镜像

命令：

```powershell
adb reboot bootloader                       # 4 s 后进入 fastboot
fastboot boot meizu15\artifacts\boot-meizu15-hdrfix.img
```

应答（原样）：

```
Sending 'boot.img' (6080 KB)                       OKAY [  0.129s]
Booting                                            OKAY [  0.016s]
Finished. Total time: 0.164s
```

观察（`boot` 返回后连续 150 s 轮询）：

| t | adb | fastboot | 说明 |
|---|---|---|---|
| +5 s | — | — | （一次误匹配：`List of devices attached` 里的 `of devices`） |
| +15 s | — | — | 静默 |
| +30 s | — | — | 静默 |
| +60 s | — | — | 静默 |
| +90 s | — | — | 静默 |
| 之后 | 无 | 无 | Windows USB 枚举里 **没有任何 05C6/18D1 设备** |

结论：**bootloader 接受了镜像并执行了跳转**（`Booting OKAY` 只在跳转前返回），此后设备既没回到 Android、也没回到 bootloader、USB gadget 整个消失 —— 设备停在镜像里。屏幕状态未知（需人眼确认）。

### 与 HANDOVER 判读表的对应

| 现象 | 原判读 |
|---|---|
| 立刻重启回 BL/Android | BootShim 或 FD 加载失败 |
| 亮屏全黑数秒后重启 | 显示初始化卡住 |
| **不重启、USB 全无、长时间静默（本次）** | **新的第四类：卡在 PEI/DXE 早期，连 USB gadget 都没起来** |

### 恢复

长按电源键 12 s 强制断电重启，回到 boot5 + Flyme。

### 待做的对照实验

1. `boot-meizu15-raw.img`（未压缩 payload，33,554,576 B）—— 排除 BL 的 gzip 解压路径。
2. `boot-meizu15.img`（CI 原始头部：`kernel_addr=0`, `tags=0`, cmdline 空）—— 排除头部差异。
3. 带 UART 的 DEBUG 构建：`./build.sh -d meizu15 -u -r DEBUG`，靠 `console=ttyMSM0,115200` 抓早期输出（需拆机接串口）。
