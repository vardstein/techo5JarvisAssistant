#!/system/bin/sh
# TECHO5 hardware ground-truth dump for cronos. Read-only.
# Run as root on the device:  adb root; adb push tools/hwdump.sh /data/local/tmp/; adb shell sh /data/local/tmp/hwdump.sh > dump.txt
# It never opens an audio device, so a running satellite is not disturbed.
# It deliberately avoids `dumpsys media.audio_flinger`, which crashes the vendor audio HAL on this device.

sec() { echo; echo "===== $1"; }

# The dump goes into the repo, which is public, so whatever names this one unit is taken out here and
# not by hand afterwards (GENBU-188): its serial number (kernel command line, idme), the eMMC's CID
# (which carries the card's serial), its Wi-Fi and Bluetooth MAC addresses, and the adb Wi-Fi guid
# (which has the serial in it). The key stays, so the
# dump still shows the value exists. `sh hwdump.sh --redact < old.txt` cleans a dump made before this.
redact() {
  sed -E \
    -e 's/(serialno)=[^ ]*/\1=<redacted>/g' \
    -e 's/^(serial|mac_addr|bt_mac_addr|cid)=.*/\1=<redacted>/' \
    -e 's/^(\[[^]]*(serial|mac|bdaddr|guid)[^]]*\]: \[)[^]/]*\]/\1<redacted>]/' \
    -e 's/[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}/<redacted-mac>/g'
}
if [ "$1" = "--redact" ]; then redact; exit; fi

{

sec "identity"
getprop ro.product.device; getprop ro.build.display.id; getprop ro.build.version.release; getprop ro.build.version.sdk
getprop ro.hardware; getprop ro.board.platform; getprop ro.boot.hardware; getprop ro.bootloader
uname -a
cat /proc/version 2>/dev/null

sec "cpu"
cat /proc/cpuinfo | grep -E 'processor|model name|Features|Hardware|CPU part' | sort | uniq -c
ls /sys/devices/system/cpu | grep -E '^cpu[0-9]'
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_frequencies 2>/dev/null

sec "memory"
grep -E 'MemTotal|MemFree|MemAvailable|SwapTotal' /proc/meminfo
cat /sys/kernel/mm/lmk/minfree 2>/dev/null

sec "storage / partitions"
cat /proc/partitions
ls -l /dev/block/platform/*/by-name 2>/dev/null || ls -l /dev/block/by-name 2>/dev/null
df -h 2>/dev/null | grep -vE 'tmpfs|overlay'
for f in name cid; do echo "$f=$(cat /sys/block/mmcblk0/device/$f 2>/dev/null)"; done
cat /sys/block/mmcblk0/size 2>/dev/null

sec "kernel cmdline"
cat /proc/cmdline

sec "modules"
lsmod 2>/dev/null || cat /proc/modules

sec "audio: /proc/asound"
cat /proc/asound/cards 2>/dev/null
cat /proc/asound/devices 2>/dev/null
cat /proc/asound/pcm 2>/dev/null
ls -l /dev/snd 2>/dev/null
for d in /proc/asound/card*/pcm*; do
  [ -d "$d" ] || continue
  echo "--- $d: $(cat $d/info 2>/dev/null | tr '\n' ' ')"
  for sub in $d/sub*; do [ -d "$sub" ] && { echo "    $(cat $sub/info 2>/dev/null | grep -E 'stream|subname' | tr '\n' ' ')"; cat $sub/hw_params 2>/dev/null | sed 's/^/    hw_params: /'; }; done
done

sec "audio: mixer controls (tinymix)"
TM=""
for c in tinymix /system/bin/tinymix /vendor/bin/tinymix; do command -v $c >/dev/null 2>&1 && { TM=$c; break; }; done
if [ -n "$TM" ]; then $TM 2>/dev/null | head -400; else echo "tinymix not available"; fi

sec "audio: policy / HAL files"
ls -l /vendor/etc/audio_policy_configuration.xml /vendor/etc/mixer_paths*.xml /system/etc/audio_policy* /vendor/etc/audio* 2>/dev/null
ls /vendor/lib/hw/ 2>/dev/null | grep -i audio

sec "audio: AudioService (safe dumpsys)"
dumpsys audio 2>/dev/null | grep -iE 'mic mute|FromSwitch|Devices|device=|stream|ringer|volume' | head -60

sec "input devices"
getevent -pl 2>/dev/null

sec "input: key layouts"
ls -l /system/usr/keylayout /vendor/usr/keylayout /data/system/devices/keylayout 2>/dev/null
for f in /system/usr/keylayout/gpio* /vendor/usr/keylayout/gpio* /data/system/devices/keylayout/*; do [ -f "$f" ] && { echo "--- $f"; cat "$f"; }; done

sec "leds"
for l in /sys/class/leds/*; do [ -e "$l" ] && echo "$l: brightness=$(cat $l/brightness 2>/dev/null) max=$(cat $l/max_brightness 2>/dev/null) trigger=$(cat $l/trigger 2>/dev/null | tr -d '\n')"; done

sec "gpio"
ls /sys/class/gpio 2>/dev/null
cat /sys/kernel/debug/gpio 2>/dev/null | head -80

sec "sensors (light, etc.)"
ls /sys/bus/iio/devices 2>/dev/null
for d in /sys/bus/iio/devices/*; do [ -d "$d" ] && echo "$d: $(cat $d/name 2>/dev/null) $(ls $d | grep -E 'in_illuminance|in_intensity|raw' | tr '\n' ' ')"; done
ls /sys/class/sensor* /sys/devices/platform/*als* /sys/devices/platform/*light* 2>/dev/null
dumpsys sensorservice 2>/dev/null | grep -iE 'light|proximity|als|lux' | head -10

sec "i2c"
ls /sys/bus/i2c/devices 2>/dev/null
for d in /sys/bus/i2c/devices/*; do [ -d "$d" ] && echo "$d: $(cat $d/name 2>/dev/null)"; done

sec "display"
cat /sys/class/graphics/fb0/modes /sys/class/graphics/fb0/virtual_size /sys/class/graphics/fb0/bits_per_pixel 2>/dev/null
ls /dev/dri 2>/dev/null
dumpsys display 2>/dev/null | grep -iE 'mBaseDisplayInfo|density|DisplayDeviceInfo' | head -5
for b in /sys/class/backlight/*; do [ -d "$b" ] && echo "$b: brightness=$(cat $b/brightness 2>/dev/null) max=$(cat $b/max_brightness 2>/dev/null)"; done

sec "camera / shutter"
ls /dev/video* 2>/dev/null
getevent -pl 2>/dev/null | grep -iA3 -E 'shutter|camera'

sec "network"
cat /sys/class/net/wlan0/address 2>/dev/null
ls /sys/class/net
getprop | grep -iE 'wifi\.|bt\.|bluetooth' | head -20
ls /vendor/firmware 2>/dev/null | head -30
ls /vendor/lib/modules /system/lib/modules 2>/dev/null

sec "idme / factory data"
ls /proc/idme 2>/dev/null
for f in board_id product_name productid productid2 serial mac_addr bt_mac_addr; do [ -r /proc/idme/$f ] && echo "$f=$(cat /proc/idme/$f)"; done

sec "thermal"
for t in /sys/class/thermal/thermal_zone*; do [ -d "$t" ] && echo "$t: $(cat $t/type 2>/dev/null) $(cat $t/temp 2>/dev/null)"; done

sec "processes (vendor daemons)"
ps -A -o PID,USER,NAME 2>/dev/null | grep -vE 'kworker|ksoftirq|migration|rcu_|kthread|irq/|cpuhp|watchdog' | head -120

sec "init services"
getprop | grep -E '^\[init\.svc\.' | sed 's/\[init\.svc\.//' | head -100

sec "selinux"
getenforce
} | redact
