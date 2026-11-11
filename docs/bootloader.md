# Bootloader — Meizu 15 (M1881), measured 2026-11-12

Everything on this page was read from the live bootloader, not assumed.

## Verdict

**`fastboot boot` works, and it accepts a modified (unsigned) image.**

The whole UEFI path is therefore available **without writing a single partition**.
This is the same capability the Redmi Note 7 port had to do without (that
bootloader refuses `fastboot boot`, which is why lavender must `flash boot`).

## getvar output

```
(bootloader) kernel:uefi                  the bootloader describes its kernel slot as UEFI
(bootloader) unlocked:yes
(bootloader) lock_state:unlocked
(bootloader) secure_state:secure
(bootloader) secure:yes
(bootloader) product:M1881
(bootloader) product:QC_Reference_Phone
(bootloader) variant:SDM EMMC
(bootloader) chipid:<YOUR-CHIPID>...
(bootloader) serialno:<YOUR-SERIAL>
(bootloader) max-download-size:536870912    512 MiB per transfer
(bootloader) off-mode-charge:1
(bootloader) charger-screen-enabled:1
(bootloader) battery-soc-ok:yes
(bootloader) battery-voltage:4347
(bootloader) partition-type:userdata:ext4
(bootloader) partition-size:userdata: 0x1B86BFBE00
(bootloader) partition-type:system:ext4
(bootloader) partition-size:system: 0x100000000
(bootloader) partition-size:cache: 0x20000000
```

## oem device-info

```
(bootloader) Device unlocked: true
(bootloader) Device critical unlocked: true      <-- can write ANY partition
(bootloader) Charger screen enabled: true
(bootloader) Verity mode: false                  <-- AVB/dm-verity not enforced
```

`critical unlocked` is the strongest state available: bootloader partitions
(`xbl`, `abl`, `tz`, `hyp`) are writable, not just `boot`/`system`.

## Test log

| # | Action | Result |
|---|---|---|
| 1 | `fastboot boot dump/boot.img` (stock, 64 MiB) | `Sending ... OKAY [1.381s]` / `Booting OKAY [0.016s]` → booted |
| 2 | patch `name[16]` at header offset 48 to `MEIZU15TEST`, `fastboot boot` again | same OKAY, **booted normally** → no signature check |
| 3 | `adb` reachable after both | `uptime 0 min`, `verifiedbootstate=orange` |

Both runs left the device back in Android with `verifiedbootstate=orange` and
`ro.boot.flash.locked=0`.

## What this settles

| Open question (from docs/hardware.md) | Answer |
|---|---|
| Does the bootloader accept `fastboot boot`? | **Yes** |
| Does it accept a non-Meizu-signed image? | **Yes** — a patched image booted |
| Is a RAM-only bring-up possible? | **Yes** — nothing written |
| Transfer size limit | 512 MiB (`max-download-size`) |
| Is AVB enforced? | No (`Verity mode: false`, `orange`) |

## Remaining unknowns

1. **EDL / 9008 recovery** — not tested (requires a host-side tool and a
   key combination; test only when needed).
2. **`devinfo` is 4 KiB of zeros**, so the unlock flag is not there; `sec`
   (16 KiB, non-zero) is the next candidate if lock-state provenance matters.
3. **Flashing the bootloader partitions themselves** (`xbl`/`abl`) is untested
   and is not needed for the UEFI bring-up.

## Consequence for the port plan

- Phase 0 = **complete**. No blocking bootloader constraint remains.
- The build target is `fastboot boot <esp-boot.img>` — same shape as the K20 Pro
  turnkey flow, so no partition surgery is required until an iOS root has to be
  persisted.
- 512 MiB per transfer is far more than the 192 MiB payload volume plus the
  boot image need.
