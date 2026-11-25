#!/bin/bash
# meizu15: when the phone shows up on USB, flash the locally built image once and record
# what the device does afterwards (USB re-enumeration is the only signal available without
# a camera: the panel reading itself has to be looked at by a human).
set -u
cd "$(dirname "$0")/.."
ADB=./tools/platform-tools/adb
FB=./tools/platform-tools/fastboot
IMG=out/boot-meizu15-payload-full25.img
LOG=out/flash-log.txt
SER=881QAESM95THY
DONE=out/.flashed

[ -f "$DONE" ] && { echo "$(date) already flashed, watching only" >> "$LOG"; }

usb_state() {
	if $ADB devices 2>/dev/null | grep -q "$SER"; then echo adb
	elif $FB devices 2>/dev/null | grep -q "$SER"; then echo fastboot
	elif ioreg -p IOUSB -w0 -l 2>/dev/null | grep -q '"USB Product Name" = "15"'; then echo usb-other
	else echo none
	fi
}

echo "$(date '+%F %T') watcher started, state=$(usb_state)" >> "$LOG"
last=""
while true; do
	s=$(usb_state)
	if [ "$s" != "$last" ]; then
		echo "$(date '+%F %T') state -> $s" >> "$LOG"
		last="$s"
	fi
	if [ ! -f "$DONE" ] && [ "$s" = adb ]; then
		echo "$(date '+%F %T') flashing $IMG" >> "$LOG"
		$ADB -s "$SER" reboot bootloader >/dev/null 2>&1
		for i in $(seq 1 40); do
			sleep 2
			$FB devices 2>/dev/null | grep -q "$SER" && break
		done
		if $FB devices 2>/dev/null | grep -q "$SER"; then
			out=$($FB -s "$SER" boot "$IMG" 2>&1 | tail -2)
			echo "$(date '+%F %T') fastboot boot: $out" >> "$LOG"
			touch "$DONE"
			# watch what the device does for the next three minutes
			for t in 15 30 60 120; do
				sleep "$t"
				echo "$(date '+%F %T')  +${t}s state=$(usb_state)" >> "$LOG"
			done
		else
			echo "$(date '+%F %T') fastboot never appeared" >> "$LOG"
		fi
	fi
	sleep 20
done
