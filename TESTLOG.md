# 真机测试记录

## 2026-11-12 23:05 — 第一次 `fastboot boot` 自定义 UEFI 镜像（hdrfix）

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

主机侧观察：`boot` 返回后 150 s 内 adb 不出现、fastboot 不出现、Windows USB 枚举里没有任何 `05C6`/`18D1` 设备。

## 屏幕现场（22:58 照片，关键证据）

**固件跑起来了**：屏幕显示的是 EDK2 的异常处理器 dump，说明
`BootShim → PEI → DxeCore → BDS → SimpleInit` 全部执行到，GOP 帧缓冲与面板初始化正常。

### 第一条非致命信息

```
SetupAPSScpuPerfLevels: Failed to get the maximum performance level for CPU 0, Status: Invalid Parameter
```

CPU 性能档位表没读到（SDM660 的 RPM/DVFS 与 edk2-msm 假定不符），不致命。

### backtrace

```
PC 0x0000C4951F3C  (0x0000C48E2000 + 0x6FF3C)   [0] SimpleInitMain.dll
PC 0x0000C48E3EF0  (0x0000C48E2000 + 0x1EF0)    [0] SimpleInitMain.dll
PC 0x0000C4E6BEEC  (0x0000C4E6C000 + 0x5EEC)    [1] DxeCore.dll
PC 0x0000C4FF31BC  (0x0000C4DF3000 + 0x1C1BC)   [2] BdsDxe.dll
PC 0x0000C4C952B4  (0x0000C4C93000 + 0x32B4)    [2] BdsDxe.dll
PC 0x0000C4C6B1B4  (0x0000C4C6B000 + 0x2B14)    [3] DxeCore.dll
PC 0x0000C4FF3114  (0x0000C4DF3000 + 0x10114)   [3] DxeCore.dll
PC 0x0000C4C6BA014 (0x0000C4C6B000 + 0x101014)  [3] DxeCore.dll
PC 0x0000CE0026B0
PC 0x0000CE008150
```

模块路径全部是 `.../Build/meizu15/RELEASE_CLANG38/AARCH64/...`：`SimpleInitMain`、`DxeCore`、`BdsDxe`。

### 异常类型

```
ESR : EC 0x25  IL 0x1  ISS 0x00000004
Data abort: Translation fault, zeroth level
```

`EC=0x25` = 同 EL 数据中止；`ISS&0x3F = 0x04` = **translation fault, level 0**（页表里该地址压根没映射，不是权限问题）。

### 寄存器（照片）

```
X0  0x10                 X1  0xC4A?5953          X3  0xFFFFFFFFFFFFFFF0
X19 0x0                  LR  0x0000C4997C5C  (= 0xC48E2000 + 0xB5C5C)
FP  0x00000009FFCE250
```

`X3 = 0xFFFFFFFFFFFFFFF0` = `0 - 0x10`，`LR = SimpleInitMain + 0xB5C5C` —— 正好落在下面那条指令上。

## 反汇编定位（本地从 boot 镜像里解出来的 SimpleInitMain，8,962,048 B PE32）

### 崩溃点 A：`0xB5C24` 函数里的空指针解引用

```
0B5C24  stp x29, x30, [sp, #-0x30]!
...
0B5C34  mov x19, x0                  ; 参数 = 要释放/校验的指针
0B5C38  sub x20, x0, #0x20           ; 块头 = ptr - 0x20
0B5C50  mov w21, #0x7043
0B5C54  movk w21, #0x6468, lsl #16   ; magic "Cphd"
0B5C58  bl #0x1264                   ; FreePool 跳转桩
0B5C5C  ldur w3, [x19, #-0x10]       ; ★ x19 = 0 时访问 0xFFFFFFFFFFFFFFF0
0B5C60  cmp w3, w21                  ; 校验 magic
```

这就是 17:28 交接里记的「`SimpleInitMain` FreePool NULL 检查崩溃，RVA `0xB5C24`」——**同一个故障**，那次只记了函数入口，这次拿到了寄存器现场。

### 崩溃点 B：`0x6FF3C` 的未初始化全局写

```
06FF2C  adrp x19, #0x81f000
06FF30  ldr x0, [x19, #0xf08]        ; 懒初始化的全局指针
06FF34  cbnz x0, #0x6ff44
06FF38  bl #0x30238                  ; 分配器（自身正确保存 x19~x22）
06FF3C  str x0, [x19, #0xf08]        ; ★ 写入未映射地址
06FF40  cbz x0, #0x6ff48
```

`0x30238` 是合规 callee（序言 `stp x29,x30` / `stp x22,x21` / `stp x20,x19`），所以不是它 clobber；更可能是 `x19` 在该路径上被改写或该静态区未按 `SizeOfImage=0x88C000` 完整映射。

## 结论

| 项 | 判定 |
|---|---|
| BL 接受非签名镜像 | ✅ `Booting OKAY` |
| BootShim 搬运 + 跳转 | ✅ |
| PEI / DxeCore / 驱动派发 | ✅（DxeCore 出现在 backtrace 里） |
| 显示（GOP + 1080×1920 面板） | ✅ 屏幕能画出整屏文字 |
| BDS 加载应用 | ✅ `SimpleInitMain` 已 LoadImage 并执行 |
| SimpleInit 自身 | ❌ 空指针 / 未映射写，数据中止 |

**不是硬件问题，是 SimpleInit 这一层的软件 bug。**

## 下一步

1. 长按电源 12 s 重启回 Flyme（设备现在停在异常界面）。
2. 让 SimpleInit 崩溃可定位到源码行：CI 改 `-r DEBUG`（当前 `RELEASE_CLANG38` 只有模块名）。
3. 给 `0xB5C24` 对应的释放函数加 `if (!ptr) return;`，或修掉调用处传 NULL 的来源。
4. 复测：`fastboot boot` 同一镜像（改完重编）。
