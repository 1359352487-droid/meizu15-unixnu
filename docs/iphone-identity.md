# Device identity: iPhone 8 Plus (iPhone10,2 / D21AP)

Decision: this port claims an **iPhone 8 Plus** identity, not the iPhone X that
the lavender port uses.

## Why

The panel decides it.

| | iPhone 8 Plus | iPhone X | Meizu 15 (this phone) |
|---|---|---|---|
| Native panel | **1080×1920** | 1125×2436 | **1080×1920** |
| Internal render | 1242×2208 → downsampled to 1080×1920 | 1125×2436 | — |
| Logical points | 414×736 | 375×812 | — |
| Scale factor | **3x** | 3x | 3x (480 dpi) |
| Aspect | **16:9 (1.778)** | 19.5:9 (2.165) | **16:9 (1.778)** |
| Home button | **yes, mechanical** | no | **yes** (Goodix GF52xx + `fp-keys`) |
| SoC class | A11 / t8015 | A11 / t8015 | SDM660 (target: XNU ARM64) |

Every display-relevant number matches the 8 Plus exactly. Claiming an iPhone X
on a 16:9 panel means SpringBoard lays out for 19.5:9 and the image is
letterboxed or stretched — the lavender board gets away with it only because the
Redmi Note 7 panel is 1080×2340 (19.5:9).

Convenient side effect: iOS on a real 8 Plus already renders at 1242×2208 and
downsamples to 1080×1920. Nothing about that pipeline is unusual to the OS.

## What changed

`board.toml`

```toml
[ipsw]
url = ".../iPhone_5.5_P3_16.7.16_20H392_Restore.ipsw"
product = "iPhone10,2"
version = "16.7.16"
build = "20H392"
device_class = "d21ap"
chip_id = 0x8015
board_id = 0x0E     # placeholder, see "Unverified" below
```

IPSW sha256: `b3882291a92ecf8763239a026813b3bafae66b7e317dee67011d90c202ccafef`

Build `20H392` is shared with the iPhone X, so the kernel generation is the same;
only the device tree identity, panel geometry and button set differ.

`scripts/make-meizu15-adt.py` gained `--iphone-model {x,8,8plus}` (default
`8plus`). Generated and verified:

| ADT property | Value |
|---|---|
| `model` | `iPhone10,2` |
| `target-type` / `target-sub-type` | `D21` / `D21AP` |
| `product-name` | `iPhone 8 Plus` |
| `product-description` | `iPhone 8 Plus` |
| `compatible-device-fallback` | `iPhone10,5` |
| `button-names` | `hold voldown volup **menu**` |
| `home-button-type` | **1** (mechanical button present; iPhone X profile uses 2) |
| `artwork-device-subtype` | 1920 |
| `artwork-scale-factor` | 3 |

## The Home button — the real work

Declaring the button in the ADT makes iOS *believe* a Home button exists. It
does not make presses arrive.

On real A11 hardware the Home button is handled by Apple's **Secure Processor
Unit** (`AppleSPU`) plus the Taptic Engine. There is no SEP on this phone, and no
`AppleSPU` driver can bind here. So the button event has to be manufactured:

| Path | Effort | Result |
|---|---|---|
| **A. AssistiveTouch** (software Home) | ~0 | Works immediately once the desktop is up; on-screen floating button |
| **B. XNU platform driver** reading the Home-key GPIO and posting an `IOHIDEvent` | 1–3 weeks | Real hardware button acts as Home |

For **B**, the ingredients are already identified:
- Android input device `fp-keys` (`/dev/input/event3`) is the Home-key source
- Fingerprint IC is `ro.meizu_fp_model = goodix_GF52xx`
- GPIO pin number and interrupt are readable from this phone's live FDT
  (`/proc/device-tree`, `gpio-keys` / `goodix_fp` nodes)

The driver must register with `IOHIDFamily`, publish a keyboard-like device with
the Home usage, and inject events on the GPIO interrupt. `AppleSPU` itself is
not needed — the IOHID path is what SpringBoard ultimately listens to.

## Unverified

| Item | How to settle |
|---|---|
| `board_id = 0x0E` | read `ApBoardID` for `d21ap` from this IPSW's `BuildManifest.plist` |
| `kernelcache_sha256` | currently the d22ap hash; recompute from the d21ap `KernelCache` |
| `early_security_init` address | must be derived from the d21ap kernelcache executable segment |
| Whether SpringBoard honours `home-button-type 1` without SEP arbitration | first boot after the ADT is accepted |
