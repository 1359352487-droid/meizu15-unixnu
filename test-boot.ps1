# fastboot boot 实测脚本（不会写任何分区）
# 用法:  .\test-boot.ps1 [-Image .\artifacts\boot-meizu15.img]
param(
    [string]$Image = "$PSScriptRoot\artifacts\boot-meizu15.img",
    [string]$Fastboot = "$PSScriptRoot\..\tools\platform-tools\fastboot.exe"
)

if (-not (Test-Path $Image)) { throw "image not found: $Image" }
if (-not (Test-Path $Fastboot)) { $Fastboot = "fastboot" }

Write-Host "image : $Image"
Write-Host ("size  : {0:N0} bytes" -f (Get-Item $Image).Length)

Write-Host "`n--- devices ---"
& $Fastboot devices

Write-Host "`n--- bootloader state (need 'Device unlocked: true') ---"
& $Fastboot oem device-info

Write-Host "`n--- booting (RAM only, no partition is touched) ---"
& $Fastboot boot $Image

Write-Host @"

Now watch the phone:
  text / menu appears      -> firmware works, move to Phase 4
  screen lights, stays black, reboots -> display init issue
  no reaction, instant reboot         -> BootShim/FD load failure
  stuck in fastboot                   -> header rejected

To recover at any time: hold power 10s.
"@