# Meizu 15 (M8810) — hardware facts pinned from the phone

All values below were read off the live device over `adb` on 2026-11-12.
Nothing here is copied from another board. Sources are named per line so any
value can be re-verified in one command.

Device: serial `<YOUR-SERIAL>`, Flyme 7.0.2.2A / Android 7.1.1 / SDK 25,
build `NGI77B` (2018-08-31), security patch 2018-04-05.

---

## 1. SoC and platform

| Item | Value | Source |
|---|---|---|
| SoC | SDM660, `soc_id=317`, family Snapdragon | `/sys/devices/soc0/{machine,family,soc_id}` |
| CPU | 4x Cortex-A73 (MPIDR 0x100..0x103) + 4x Cortex-A53 (0x0..0x3) | `/proc/cpuinfo`, stock FDT `/cpus` |
| ISA | **ARMv8.0-A** (Kryo 260): **no PAN, no LSE** | SoC fact; see `configs/meizu15_defconfig` |
| PSCI | `enable-method = "psci"`, `method = "smc"` | stock FDT `/psci` |
| PMIC | PM660 + PM660A (RPM era) | FDT `model` = `SDM 660 PM660 + PM660A MTP` |
| FDT model | `Qualcomm Technologies, Inc. SDM 660 PM660 + PM660A MTP` | `/proc/device-tree/model` |
| FDT compatible | `qcom,sdm660-mtp qcom,sdm660 qcom,mtp` | `/proc/device-tree/compatible` |

## 2. Interrupts and timers  (matches the SDM660 reference exactly)

| Item | Value | Source |
|---|---|---|
| GIC | `arm,gic-v3` | FDT `soc/interrupt-controller@17a00000/compatible` |
| GIC distributor | `0x17a00000` / `0x10000` | same node `reg` |
| GIC redistributor | `0x17b00000` / `0x100000`, stride `0x20000` | same node `reg` |
| APSS window | base `0x17000000`, size `0x1C00000` | encloses dist + 1 MiB redist region |
| Arch timer | `arm,armv8-timer`, **19,200,000 Hz** (`0x124f800`) | FDT `soc/timer/{compatible,clock-frequency}` |

## 3. DRAM

```
memory/reg (2 address cells, 2 size cells):
  bank0  base 0x0000000080000000  size 0x0000000080000000   (2 GiB)
  bank1  base 0x0000000100000000  size 0x000000004c7e0000   (~1.19 GiB)
  span end = 0x14c7e0000
  usable span = 0x14c7e0000 - 0x80000000 = 0xCC7E0000  (~3.19 GiB)
```

This is the **4 GB SKU**. The Redmi Note 7 reference board reports
`0xfe4c0000` (6 GB SKU) — that value is wrong here.

Source: `cat /proc/device-tree/memory/reg | od -A n -t x4`

## 4. reserved-memory  (all 9 regions, decoded from stock FDT)

| Region | Address | Size | Note |
|---|---|---|---|
| `splash_region@9d400000` | `0x9d400000` | `0x2400000` | label `cont_splash_mem`; **address identical to the SDM660 reference board** |
| `pstore_mem_region@9ff00000` | `0x9ff00000` | `0x200000` | pstore / ramoops |
| `adsp_fw_region@92a00000` | `0x92a00000` | `0x1e00000` | ADSP firmware |
| `modem_fw_region@8ac00000` | `0x8ac00000` | `0x7e00000` | modem firmware |
| `cdsp_fw_region@94a00000` | `0x94a00000` | `0x600000` | CDSP firmware |
| `pil_mba_region@94800000` | `0x94800000` | `0x200000` | PIl MBA |
| `removed_regions@85800000` | `0x85800000` | `0x3700000` | holes removed from the linear map |
| `wlan_msa_mem@85700000` | `0x85700000` | `0x100000` | WLAN MSA |
| `wlan_msa_guard@85600000` | `0x85600000` | `0x100000` | WLAN MSA guard |

The splash region **must** appear in the boot image's DTB or the stock
bootloader powers the panel off before anything can draw.

## 5. Storage

| Item | Value | Source |
|---|---|---|
| Type | **eMMC** (`type=MMC`), not UFS | `/sys/block/mmcblk0/device/type` |
| Part name | `DV6DMB` (Samsung) | `/sys/block/mmcblk0/device/name` |
| CID | `150100445636444d420237a6acf83500` | `/sys/block/mmcblk0/device/cid` |
| Capacity | 122,138,624 KiB (~128 GB) | `/proc/partitions` |
| Logical block | 512 B | `/sys/class/block/mmcblk0/queue/logical_block_size` |
| Total sectors | 244,277,248 | `/sys/class/block/mmcblk0/size` |
| eMMC host ctrl | `sdhci@c0c4000` — `0xc0c4000`/`0x1000`, `qcom,sdhci-msm-v5` | stock FDT `soc/sdhci@c0c4000` |
| second host | `sdhci@c084000` (unused for internal storage) | stock FDT `soc/` |
| UFS blocks | `ufshc@1da4000`, `ufsice@1db0000`, `ufsphy@1da7000` present in FDT but **unused** — the phone ships eMMC | stock FDT `soc/` |
| RPMB | `mmcblk0rpmb` present | `/proc/partitions` |

## 6. Partition table  (GPT, 512-byte LBA)

by-name root: `/dev/block/platform/soc/*/by-name/`  (note: **not** `/dev/block/by-name/`)

| Name | Device | start LBA | sectors | size |
|---|---|---|---|---|
| `xbl_a` | mmcblk0p1 | 131072 | 7168 | 3.5 MiB |
| `xbl_b` | mmcblk0p2 | — | 7168 | 3.5 MiB |
| `tz_a` | mmcblk0p3 | — | 4096 | 2 MiB |
| `abl` | mmcblk0p18 | 501760 | 2048 | 1 MiB |
| `ddr` | mmcblk0p27 | 656400 | 2048 | 1 MiB |
| `misc` | mmcblk0p43 | 860192 | 2048 | 1 MiB |
| **`boot`** | **mmcblk0p50** | **1179648** | **131072** | **64 MiB** |
| **`recovery`** | **mmcblk0p51** | **1310720** | **131072** | **64 MiB** |
| `custom` | mmcblk0p52 | 1441792 | 2097152 | 1 GiB |
| `system` | mmcblk0p53 | 3538944 | 8388608 | 4 GiB |
| `rawdump` | mmcblk0p54 | 11927552 | 262144 | 128 MiB |
| `logdump` | mmcblk0p55 | 12189696 | 131072 | 64 MiB |
| `cache` | mmcblk0p56 | 12320768 | 1048576 | 512 MiB |
| **`userdata`** | **mmcblk0p57** | **13369344** | **230907871** | **~110 GiB** |

`userdata`: first_lba `0xCC0000`, last_lba `0xE8F5FDE` (244,277,214).
33 sectors of backup GPT follow it.

A/B slots exist only for `xbl`, `tz`, `hyp`, `rpm`, `pmic`, `vbmeta`
(`*_a` / `*_b`); `boot`, `system`, `recovery` are single-slot.

Other names present: `apdp bluetooth cmnlib cmnlib64 devcfg devinfo dip dpo dsp frp
fsc fsg ftest keymaster keystore limits logfs mdtp modem modemst1 modemst2 msadp
param persist private proinfo reserved rstinfo sec splash ssd sti storsec toolsfv`.

## 7. Display and touch  (**differs from the SDM660 reference board**)

| Item | Meizu 15 | Redmi Note 7 (reference) |
|---|---|---|
| Display stack | `mdss_dsi`, `mdss_mdp@c900000`, `mdss_dsi@0` | same SoC block |
| Resolution | **1080x1920** | 1080x2340 |
| Power rails | `dsi_panel_pwr_supply_labibb_amoled` (AMOLED, needs LAB/IBB) | LCD rails |
| Panel model | not captured (dmesg ring had rolled past the early probe) | NT36672A |
| Touch | **Synaptics DSX**, `synaptics_dsx_i2c` on **i2c-4 @ 0x20** | Novatek NVT @ 0x62 on `i2c@c175000` |
| Second input IC | Goodix (`gf_drv_tee`) also loads — home-key fingerprint | — |

`fb0 virtual_size = 1080,3840` -> double-buffered 1080x1920.

DSI/I2C controllers in FDT: `i2c@c175000 c176000 c177000 c178000`,
`qcom,mdss_dsi@0`, `qcom,mdss_dsi_pll@c994400`, `qcom,mdss_mdp@c900000`.

## 8. Boot state at time of capture

```
ro.boot.flash.locked       = 0            bootloader unlocked
ro.boot.verifiedbootstate  = orange       verified boot not enforcing
ro.boot.veritymode         = logging      dm-verity downgraded
SELinux                    = Permissive
ro.flyme.root.state        = 1            Flyme official root enabled
/system/xbin/su            setuid root, 10344 B, Meizu signed (com.meizu.su)
```

Flyme's `su` does **not** accept `-c`; it launches
`com.meizu.settings/com.meizu.settings.root.FlymeRootRequestActivity`
and waits for a user prompt on screen. Use `echo 'cmd' | su` or `su 0 cmd`,
and expect one approval dialog per invocation unless the shell UID is
whitelisted in Flyme's root manager.

## 9. Open questions (unresolved)

| Question | How to settle it |
|---|---|
| Does the Meizu bootloader accept `fastboot boot`? | `fastboot boot test.img` in bootloader |
| Does it accept a non-Meizu-signed `boot` image on `flash`? | `fastboot flash boot <modified>` then restore |
| Panel model / init sequence | `dmesg` right after a cold boot, or unpack the stock `boot.img` DTB |
| eMMC HS400 tuning values for DV6DMB | not obtainable from userspace; needs driver bring-up |
| What `devinfo`/`sec` hold re: the unlock flag | compare a pre-unlock vs post-unlock dump of those partitions |
