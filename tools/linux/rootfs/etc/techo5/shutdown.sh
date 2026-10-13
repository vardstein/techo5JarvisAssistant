#!/bin/sh
# TECHO5 root filesystem: shutdown (see /etc/inittab). A clean stop for the
# daemon first — it gates the amplifier before letting go of the speaker, and
# a stop on purpose is not a failed trial — then everything else.
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
pid=$(pidof techo5)
if [ -n "$pid" ]; then
	kill -TERM $pid
	n=0; while [ $n -lt 10 ] && pidof techo5 >/dev/null; do sleep 1; n=$((n+1)); done
fi
killall dropbear ntpd udhcpc wpa_supplicant fbprobe 2>/dev/null
cp /run/boot.log /data/techo5-linux/boot.log 2>/dev/null
# The time, for the next boot (see t5_clock_restore).
. /lib/techo5-lib.sh
t5_clock_save
sync
umount -a -r 2>/dev/null
sync
