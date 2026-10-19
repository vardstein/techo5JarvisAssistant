# techo5-lib.sh — shell functions shared by the initramfs init (tools/linux/init)
# and the root filesystem's boot script (tools/linux/rootfs/etc/techo5/boot.sh).
# Busybox sh. The caller may define log() before sourcing; the default goes to
# the kernel log.

type log >/dev/null 2>&1 || log() { echo "techo5: $*" > /dev/kmsg; }

# t5_board: which Echo this is, from the panel the bootloader names in the kernel command
# line. The daemon reads the same thing (internal/layout); this side needs it because one
# root filesystem serves all three boards, so the defaults below cannot be baked in per
# board. Prints nothing and returns non-zero when no panel is named.
t5_board() {
	for w in $(cat /proc/cmdline 2>/dev/null); do
		case $w in
		lcm=*checkers*) echo checkers; return 0 ;;
		lcm=*crown*) echo crown; return 0 ;;
		lcm=*cronos*) echo cronos; return 0 ;;
		esac
	done
	return 1
}

# t5_hostname: the name the Show goes by on the network, sent with every DHCP request so the router's
# lease list says which Show is which instead of "*" (GENBU-260). It is the node name echod announces
# over mDNS (layout.Slug): "Living Room" becomes living-room. Read at each lease client start, so one
# restarted after a rename sends the new name; techo5 until the Show has a name.
T5_NAME=${T5_NAME:-/data/misc/techo5/name}
t5_hostname() {
	_h=$(head -n 1 "$T5_NAME" 2>/dev/null | tr 'A-Z' 'a-z' | tr -c 'a-z0-9' '-' | tr -s '-' | sed 's/^-*//; s/-*$//')
	echo "${_h:-techo5}"
}

# t5_usb_acm: one CDC ACM serial function on the USB gadget (a COM port on the
# host). Idempotent. The 4.9.77 (TWRP) kernel has the legacy android_usb
# gadget, the 4.9.337 (LineageOS) kernel has configfs; both are handled.
# T5_PRODUCT names the USB serial gadget, which is what a computer lists the device as.
# A device may set it, and a root filesystem carrying /etc/techo5/device.conf does
# (boot.sh sources that file first); otherwise it follows the board, so a Show 8 does
# not put the Show 5's name on the gadget. The names are the product's, Genbu Show by
# OpenJade; the Echo it was is no longer what is plugged in. The host's tools find the
# device by vendor and product number, which stay as they were.
case $(t5_board) in
crown) T5_PRODUCT=${T5_PRODUCT:-Genbu Show 8} ;;
checkers|cronos) T5_PRODUCT=${T5_PRODUCT:-Genbu Show 5} ;;
*) T5_PRODUCT=${T5_PRODUCT:-Genbu Show} ;;
esac
T5_MAKER=OpenJade

t5_usb_acm() {
	A=/sys/class/android_usb/android0
	G=/sys/kernel/config/usb_gadget/g1
	# The LineageOS kernel's configfs gadget also makes android0 once bound, but with only its state
	# in it; the legacy gadget is the one with functions to write.
	if [ -e $A/functions ]; then
		# Up already is fine only under today's names: the initramfs may have brought it up under
		# older ones, and re-enabling is what makes a computer read the new names.
		[ "$(cat $A/functions 2>/dev/null)" = acm ] && [ "$(cat $A/enable 2>/dev/null)" = 1 ] &&
			[ "$(cat $A/iProduct 2>/dev/null)" = "$T5_PRODUCT" ] &&
			[ "$(cat $A/iManufacturer 2>/dev/null)" = "$T5_MAKER" ] && return 0
		echo 0 > $A/enable
		echo 1d6b > $A/idVendor
		echo 0104 > $A/idProduct
		echo "$T5_MAKER" > $A/iManufacturer
		echo "$T5_PRODUCT" > $A/iProduct
		echo genbu > $A/iSerial
		echo acm > $A/functions
		[ -e $A/f_acm/instances ] && echo 1 > $A/f_acm/instances
		echo 1 > $A/enable && log "usb: legacy gadget enabled (acm)" || log "usb: legacy gadget enable failed"
		return 0
	fi
	mountpoint -q /sys/kernel/config || mount -t configfs none /sys/kernel/config 2>/dev/null || { log "usb: configfs mount failed"; return 1; }
	if [ -d $G ] && [ -n "$(cat $G/UDC 2>/dev/null)" ]; then
		[ "$(cat $G/strings/0x409/product 2>/dev/null)" = "$T5_PRODUCT" ] &&
			[ "$(cat $G/strings/0x409/manufacturer 2>/dev/null)" = "$T5_MAKER" ] && return 0
		echo "" > $G/UDC # unbound so the new names are read on the next bind
	fi
	mkdir -p $G || return 1
	echo 0x1d6b > $G/idVendor
	echo 0x0104 > $G/idProduct
	echo 0x0200 > $G/bcdUSB
	mkdir -p $G/strings/0x409
	echo "$T5_MAKER" > $G/strings/0x409/manufacturer
	echo "$T5_PRODUCT" > $G/strings/0x409/product
	echo genbu > $G/strings/0x409/serialnumber
	mkdir -p $G/configs/c.1/strings/0x409
	echo "acm" > $G/configs/c.1/strings/0x409/configuration
	mkdir -p $G/functions/acm.usb0
	[ -e $G/configs/c.1/acm.usb0 ] || ln -s $G/functions/acm.usb0 $G/configs/c.1/acm.usb0
	UDC=$(ls /sys/class/udc 2>/dev/null | head -1)
	if [ -n "$UDC" ]; then
		echo "$UDC" > $G/UDC && log "usb: gadget bound to $UDC (acm)" || log "usb: bind to $UDC failed"
	else
		log "usb: no UDC; gadget not started"
	fi
	[ -e /dev/ttyGS0 ] || mdev -s
}

# T5_USB_DEBUG is the flag the daemon keeps while USB debugging is switched on (Privacy & Security on
# the screen, or the switch in Home Assistant). Without it the port is closed both ways.
T5_USB_DEBUG=/data/misc/techo5/usb_debug

# t5_usb_off: nothing on the USB port. A computer sees no device at all, and nothing plugged into
# the port is let in: a keyboard or a stick on an OTG adapter stays unused.
t5_usb_off() {
	A=/sys/class/android_usb/android0
	G=/sys/kernel/config/usb_gadget/g1
	if [ -e $A/functions ]; then
		[ "$(cat $A/enable 2>/dev/null)" = 0 ] || { echo 0 > $A/enable && log "usb: legacy gadget disabled"; }
	elif [ -n "$(cat $G/UDC 2>/dev/null)" ]; then
		echo "" > $G/UDC && log "usb: gadget unbound"
	fi
	t5_usb_host 0
}

# t5_usb_host 0|1: whether devices on the port's host side are let in. New ones follow the root hub's
# default; ones already there (plugged in before boot) are put out or let back in.
t5_usb_host() {
	for d in /sys/bus/usb/devices/usb*; do
		[ -e $d/authorized_default ] && echo $1 > $d/authorized_default
	done
	for d in /sys/bus/usb/devices/*; do
		case ${d##*/} in usb*|*:*) continue ;; esac
		[ -e $d/authorized ] && echo $1 > $d/authorized
	done
}

# T5_ROOT_PW is root's password as /etc/shadow keeps it ("$6$salt$hash"), set by the installer or
# techo5-passwd; factory reset removes it (t5_factory_reset). The root filesystem is read-only, so t5_root_pw lays a copy
# of /etc/shadow with that line over it. While one is set, the serial console asks for it.
T5_ROOT_PW=/data/misc/techo5/root_pw

# t5_root_pw: puts the stored password in force, or takes it out; true while one is in force.
t5_root_pw() {
	while grep -q " /etc/shadow " /proc/mounts; do umount /etc/shadow || break; done
	rm -f /run/techo5/root_pw_on /run/techo5/shadow
	h=$(head -n 1 $T5_ROOT_PW 2>/dev/null)
	# Only the characters crypt writes, so the line can't break the file or the sed.
	case $h in
	'$6$'*) ;;
	*) return 1 ;;
	esac
	case $h in *[!A-Za-z0-9./\$=]*) log "root password: stored line is not a hash, ignored"; return 1 ;; esac
	(umask 027; sed "s|^root:[^:]*:|root:$h:|" /etc/shadow > /run/techo5/shadow) || return 1
	chown root:shadow /run/techo5/shadow 2>/dev/null
	mount --bind /run/techo5/shadow /etc/shadow || return 1
	: > /run/techo5/root_pw_on
}

# T5_RESET is the mark the screen's Factory reset leaves before it restarts (echod's resetMark).
T5_RESET=/data/misc/techo5/factory_reset
# What a mark can ask to keep, one word a line after the first. jarvis-show.py --remove-show looks for
# this line over SSH before it asks a Show to keep its Wi-Fi; an older image would erase it anyway.
T5_RESET_KEEPS=wifi

# t5_factory_reset [root]: when the mark is there, erases what this device was set up with before
# anything that could write it back has started: the daemon's state and settings, its name, the Home
# Assistant key, the root password, Bluetooth pairings, the Wi-Fi it knew (Android's saved networks
# too, or t5_wifi_conf would bring them back), ssh host keys and the logs. The profile stays: it is
# what the image was installed as, not a setting. Android's other data on userdata stays as well.
# The Home Assistant key is replaced with a random one nobody holds, not removed: a missing key is an
# open device, and adding it again goes through the setup page's adoption window. setup-mode says so to
# echod, which keeps the device off mDNS until that window is open (layout.SetupPath). A mark with a line
# keep-wifi (left over SSH by jarvis-show.py --remove-show) keeps the Wi-Fi it knew, so the Show comes
# back in setup mode on the same network. The mark goes last, so a power cut halfway erases again on
# the next boot. [root] is for the tests.
t5_factory_reset() {
	r=${1:-}
	st=$r/data/misc/techo5
	[ -e "$r$T5_RESET" ] || return 1
	log "factory reset: erasing"
	for f in "$st"/* "$st"/.[!.]*; do
		[ -e "$f" ] || [ -L "$f" ] || continue
		case ${f##*/} in profile|factory_reset) continue ;; esac
		rm -rf "$f"
	done
	(umask 077; head -c 32 /dev/urandom | base64 > "$st/psk.new") && mv -f "$st/psk.new" "$st/psk"
	: > "$st/setup-mode"
	rm -rf "$r/data/techo5-linux/dropbear" "$r/data/techo5-linux/crash" \
		"$r/data/techo5-linux/techo5.log" "$r/data/techo5-linux/techo5.log".* \
		"$r/data/techo5-linux/echod.log" "$r/data/techo5-linux/boot.log"
	if grep -qx keep-wifi "$r$T5_RESET" 2>/dev/null; then
		log "factory reset: keeping the Wi-Fi"
	else
		rm -rf "$r/data/techo5-linux/wpa_supplicant.conf" "$r/data/techo5-linux/wpa_supplicant.conf".*
		rm -f "$r/data/misc/apexdata/com.android.wifi/WifiConfigStore"* "$r/data/misc/wifi/WifiConfigStore"*
	fi
	sync
	rm -f "$r$T5_RESET"
	sync
	log "factory reset: done"
}

# t5_wifi_store_records <xml>: the networks in an Android WifiConfigStore, three
# lines each on stdout - S:<name>, K:<key management>, P:<key> - with the name
# and the key still in the form Android wrote them (a quoted string, or hex for
# a name Android could not spell). P: is empty for a network with no key.
#
# Android's store is a record per network and the fields have to be read a record
# at a time. Taking the first <string name="SSID"> in the file together with the
# first <string name="PreSharedKey"> pairs the name of one network with the
# passphrase of another as soon as an open network is saved ahead of the secured
# one - the unit then cannot associate, spends its boot attempts on a network
# that was never real, and lands in rescue.
#
# The sed ahead of awk puts every element on its own line, because Android has
# written that file both pretty printed and all on one line.
t5_wifi_store_records() {
	sed 's|><|>\n<|g' "$1" | awk '
	function unent(s) {
		gsub(/&quot;/, "\"", s); gsub(/&apos;/, "\047", s)
		gsub(/&lt;/, "<", s); gsub(/&gt;/, ">", s)
		gsub(/&amp;/, "\\&", s)
		return s
	}
	function val(s) { sub(/^[^>]*>/, "", s); sub(/<\/string>.*$/, "", s); return unent(s) }
	/<Network>/ { inrec = 1; ssid = ""; key = ""; kind = "" }
	inrec && /<string name="SSID">/          { ssid = val($0) }
	inrec && /<string name="PreSharedKey">/  { key  = val($0) }
	inrec && /<string name="ConfigKey">/     { kind = val($0); sub(/^.*"/, "", kind) }
	/<\/Network>/ {
		if (inrec && ssid != "") { print "S:" ssid; print "K:" kind; print "P:" key }
		inrec = 0
	}'
}

# t5_wifi_from_store <xml>: a whole wpa_supplicant configuration on stdout, one
# network= stanza per network Android saved, in the order Android had them, so
# the unit joins whichever of them it can hear.
#
# A name goes in as hex: that is the one form wpa_supplicant reads back exactly
# as it was written, and it is the form echod's wifi package writes, which
# rewrites this same file when somebody picks a network on the screen. A key is
# Android's own text and already in the supplicant's own syntax - a quoted
# passphrase, or the 64 hex digits of a key - so it is passed through as it
# stands. A network with no key is an open one and says so; anything else with
# no key wants a certificate or a WEP key this cannot supply, and is left out.
t5_wifi_from_store() {
	printf 'ctrl_interface=/run/wpa\nupdate_config=0\n'
	t5_wifi_store_records "$1" | while IFS= read -r rec; do
		case "$rec" in
		S:*) name=${rec#S:}; continue ;;
		K:*) kind=${rec#K:}; continue ;;
		P:*) key=${rec#P:} ;;
		*)   continue ;;
		esac
		case "$name" in
		'"'*'"')
			bare=${name#\"}; bare=${bare%\"}
			hexname=$(printf %s "$bare" | od -An -v -tx1 | tr -d ' \n')
			;;
		*)
			# Android keeps a name it cannot spell as text in hex already.
			hexname=$name
			;;
		esac
		printf %s "$hexname" | grep -Eq '^([0-9a-fA-F]{2}){1,32}$' || continue
		if [ -n "$key" ]; then
			printf 'network={\n\tssid=%s\n\tpsk=%s\n}\n' "$hexname" "$key"
		elif [ "$kind" = NONE ] || [ "$kind" = OWE ]; then
			printf 'network={\n\tssid=%s\n\tkey_mgmt=NONE\n}\n' "$hexname"
		fi
	done
}

# t5_wifi_conf <file>: write a wpa_supplicant configuration from the networks
# Android had saved on userdata, unless <file> exists already. Nothing is typed
# and nothing leaves the device.
t5_wifi_conf() {
	out=$1
	dir=$(dirname "$out")
	mkdir -p -m 700 "$dir"
	chmod 700 "$dir" 2>/dev/null || true
	if [ -s "$out" ]; then
		chmod 600 "$out" 2>/dev/null || return 1
		return 0
	fi
	xml=/data/misc/apexdata/com.android.wifi/WifiConfigStore.xml   # Android 11
	[ -r "$xml" ] || xml=/data/misc/wifi/WifiConfigStore.xml         # older Android
	if [ ! -r "$xml" ]; then
		# Nothing to take one from: a unit installed from TWRP, or one that has moved house. An empty
		# configuration still lets the supplicant start, so the screen's Wi-Fi page can find and join a
		# network; without one the page would have nothing to talk to.
		umask 077
		printf 'ctrl_interface=/run/wpa\nupdate_config=0\n' > "$out"
		chmod 600 "$out" 2>/dev/null || return 1
		umask 022
		log "wifi: no saved network and no Android store; waiting for one from the screen"
		return 1
	fi
	umask 077
	t5_wifi_from_store "$xml" > "$out.tmp"
	umask 022
	n=$(grep -c '^network={' "$out.tmp" 2>/dev/null)
	if [ "${n:-0}" -lt 1 ]; then
		rm -f "$out.tmp"
		log "wifi: none of Android's saved networks in $xml is one this can join"
		return 1
	fi
	mv -f "$out.tmp" "$out"
	chmod 600 "$out" 2>/dev/null || return 1
	log "wifi: configuration written from $n of Android's saved networks"
}

# t5_ipv6_private <interface>: keep this device's IPv6 addresses from spelling out its MAC.
#
# Linux builds an interface identifier out of the hardware address by default, so a global address
# reads as the prefix the ISP handed out followed by the device's own MAC (EUI-64: the first octet's
# universal bit flipped and ff:fe pushed through the middle). That address names the hardware, and
# goes on naming it through every prefix the ISP ever changes - a satellite in somebody's house
# should not be announcing which one it is. A random identifier says nothing and costs nothing:
# nothing here is reached over IPv6, the daemon is found over IPv4 and mDNS.
#
# addr_gen_mode 3 is the random one and wants Linux 4.5; the Dot's 3.18 kernel has no addr_gen_mode
# at all, so temporary addresses (RFC 4941) are set either way for anything the device itself starts,
# and the kernel that can do no better says so in the log rather than quietly differing.
#
# Before the link comes up, because the identifier is chosen when the address is generated and an
# address already there does not change.
t5_ipv6_private() {
	ifc=$1; mode=
	for w in default "$ifc"; do
		d=/proc/sys/net/ipv6/conf/$w
		[ -d "$d" ] || continue
		[ -e "$d/use_tempaddr" ] && echo 2 > "$d/use_tempaddr" 2>/dev/null
		[ -e "$d/addr_gen_mode" ] || continue
		echo 3 > "$d/addr_gen_mode" 2>/dev/null
		mode=$(cat "$d/addr_gen_mode" 2>/dev/null)
	done
	case "$mode" in
	3) log "ipv6: addresses take a random identifier, not this device's MAC" ;;
	"") log "ipv6: this kernel has no addr_gen_mode; addresses carry this device's MAC" ;;
	*) log "ipv6: addr_gen_mode stayed $mode; addresses may still carry this device's MAC" ;;
	esac
}

# t5_wifi_up <module.ko> <wpa.conf>: load the vendor driver if wlan0 is not
# there yet, associate, and take a DHCP lease (udhcpc stays running to renew
# it). Sets IP. The vendor driver's first full scan alone takes several
# seconds; association is allowed WIFI_WAIT seconds (60).
t5_wifi_up() {
	mod=$1; conf=$2; IP=
	if ! ip link show wlan0 >/dev/null 2>&1; then
		[ -e "$mod" ] || { log "wifi: driver not found at $mod"; return 1; }
		insmod "$mod" 2>/tmp/insmod.err || { log "wifi: insmod failed: $(cat /tmp/insmod.err)"; return 1; }
		n=0; while [ $n -lt 10 ] && ! ip link show wlan0 >/dev/null 2>&1; do sleep 1; n=$((n+1)); done
		ip link show wlan0 >/dev/null 2>&1 || { log "wifi: driver loaded but no wlan0"; return 1; }
		log "wifi: driver loaded, wlan0 present"
	fi
	[ -r "$conf" ] || { log "wifi: no configuration"; return 1; }
	mkdir -p /run/wpa
	# The Echo Spot's bcmdhd is a USB device that downloads its firmware at insmod, drops off the bus
	# and comes back: wlan0 exists before it can be opened, and bringing it up then fails with EBUSY,
	# which leaves wpa_supplicant unable to start. Wait for the open to succeed. The Show's mt76x8 is
	# up on the first try.
	t5_ipv6_private wlan0
	n=0; until ip link set wlan0 up 2>/tmp/ifup.err; do
		n=$((n+1)); [ $n -ge 30 ] && { log "wifi: wlan0 would not come up: $(cat /tmp/ifup.err)"; return 1; }
		sleep 1
	done
	[ $n -gt 0 ] && log "wifi: wlan0 up after ${n}s"
	if ! pidof wpa_supplicant >/dev/null; then
		wpa_supplicant -B -i wlan0 -c "$conf" -P /run/wpa.pid > /tmp/wpa.log 2>&1
	fi
	# No network to wait for: the supplicant is up for the screen to add one, and waiting out
	# WIFI_WAIT would only hold the rest of the boot back.
	# The lease client is started too, in the background: a network joined from the screen asks it for
	# an address (lib/wifi renews it), and it keeps trying until there is one.
	if ! grep -q '^network={' "$conf"; then
		pidof udhcpc >/dev/null || udhcpc -i wlan0 -b -R -O ntpsrv -x "hostname:$(t5_hostname)" -p /run/udhcpc.pid -s "${UDHCPC_SCRIPT:-/usr/share/udhcpc/default.script}" > /tmp/udhcpc.log 2>&1
		log "wifi: no network saved yet"
		return 1
	fi
	n=0; while [ $n -lt ${WIFI_WAIT:-60} ]; do
		wpa_cli -p /run/wpa -i wlan0 status 2>/dev/null | grep -q '^wpa_state=COMPLETED' && break
		sleep 1; n=$((n+1))
	done
	if ! wpa_cli -p /run/wpa -i wlan0 status 2>/dev/null | grep -q '^wpa_state=COMPLETED'; then
		log "wifi: not associated ($(wpa_cli -p /run/wpa -i wlan0 status 2>/dev/null | grep wpa_state))"
		# The lease client is left asking anyway, in the background so the boot is not held: the
		# supplicant goes on trying, and a network it finds later, or one joined from the screen
		# (lib/wifi only pokes a running udhcpc), has to get an address when it does, not at the
		# keeper's next restart. -A 5 asks again five seconds after a round goes unanswered.
		pidof udhcpc >/dev/null || udhcpc -i wlan0 -b -R -O ntpsrv -x "hostname:$(t5_hostname)" -A 5 -p /run/udhcpc.pid -s "${UDHCPC_SCRIPT:-/usr/share/udhcpc/default.script}" > /tmp/udhcpc.log 2>&1 &
		return 1
	fi
	if ! pidof udhcpc >/dev/null; then
		udhcpc -i wlan0 -b -R -O ntpsrv -x "hostname:$(t5_hostname)" -t 10 -p /run/udhcpc.pid -s "${UDHCPC_SCRIPT:-/usr/share/udhcpc/default.script}" > /tmp/udhcpc.log 2>&1
	fi
	n=0; while [ $n -lt 30 ]; do
		IP=$(ip -4 addr show wlan0 2>/dev/null | sed -n 's/.*inet \([0-9.]*\).*/\1/p' | head -1)
		[ -n "$IP" ] && break
		sleep 1; n=$((n+1))
	done
	[ -n "$IP" ] || { log "wifi: associated but no lease"; return 1; }
	log "wifi: $IP on wlan0"
}

# t5_ip: the current IPv4 address on wlan0, empty if none.
t5_ip() { ip -4 addr show wlan0 2>/dev/null | sed -n 's/.*inet \([0-9.]*\).*/\1/p' | head -1; }

# t5_wifi_prefer5: keep the link on the network's strongest worthwhile radio, 5 GHz first.
#
# The supplicant picks a radio at connect time and often takes 2.4 GHz, where the Bluetooth half of the
# same chip shares its antenna and spectrum: on the bench that was 1-4 MB/s against 3-12 MB/s on 5 GHz
# with earbuds connected (2026-09-16). It can also take a far 5 GHz radio: the bench once booted onto one
# at -70 dBm, took 70 s to get a lease and ran at 20-40 Mbit/s, next to a -34 dBm 2.4 GHz radio.
#
# Called by the keeper every minute. A 5 GHz radio at T5_5G_MIN dBm or better is strong: the link moves
# to the strongest one when it is on 2.4 GHz or on a weak 5 GHz radio. With no strong 5 GHz radio, a
# weak 5 GHz link moves to 2.4 GHz when that is T5_2G_GAIN dB stronger and at T5_2G_MIN or better. Until
# the link is on a strong 5 GHz radio it keeps scanning, every five minutes, so a 5 GHz radio that comes
# back (or was never listed: a connected supplicant stops scanning) is found. A move pins the running
# supplicant to the chosen channel, nothing is saved; if it does not associate there within 30 s it
# goes back to every band and leaves it for 30 minutes.
t5_wifi_prefer5() {
	w="wpa_cli -p /run/wpa -i wlan0"
	link=$(iw dev wlan0 link 2>/dev/null)
	freq=$(echo "$link" | sed -n 's/.*freq: \([0-9]*\).*/\1/p'); freq=${freq%%.*}
	sig=$(echo "$link" | sed -n 's/.*signal: \(-*[0-9]*\).*/\1/p')
	[ -n "$freq" ] && [ -n "$sig" ] || return 0
	strong=${T5_5G_MIN:--65}
	# On a strong 5 GHz radio already: nothing to look for.
	[ "$freq" -gt 4000 ] && [ "$sig" -ge "$strong" ] && return 0
	now=$(cut -d. -f1 /proc/uptime)
	mkdir -p /run/techo5
	[ "$now" -ge "$(cat /run/techo5/prefer5-after 2>/dev/null || echo 0)" ] || return 0
	id=$($w list_networks 2>/dev/null | awk -F'\t' 'NR>1 && $4 ~ /CURRENT/ {print $1}')
	ssid=$($w status 2>/dev/null | sed -n 's/^ssid=//p')
	[ -n "$id" ] && [ -n "$ssid" ] || return 0

	# The strongest radio of this network on each band, as "signal freq".
	best5=$($w scan_results 2>/dev/null | awk -F'\t' -v s="$ssid" 'NR>1 && $5 == s && $2 > 4000 {print $3, $2}' | sort -n | tail -1)
	best2=$($w scan_results 2>/dev/null | awk -F'\t' -v s="$ssid" 'NR>1 && $5 == s && $2 < 4000 {print $3, $2}' | sort -n | tail -1)
	sig5=${best5%% *}; f5=${best5##* }
	sig2=${best2%% *}; f2=${best2##* }

	# Keep looking: the list ages out, and radios come and go.
	if [ "$now" -ge "$(cat /run/techo5/prefer5-scan 2>/dev/null || echo 0)" ]; then
		$w scan >/dev/null 2>&1
		echo $((now + 300)) > /run/techo5/prefer5-scan
	fi

	target=
	if [ -n "$sig5" ] && [ "$sig5" -ge "$strong" ] && [ "$f5" != "$freq" ]; then
		target=$f5
		log "wifi: on $freq MHz at $sig dBm while '$ssid' has 5 GHz at $sig5 dBm; moving to $f5 MHz"
	elif [ "$freq" -gt 4000 ] && [ -n "$sig2" ] && [ "$sig2" -ge "${T5_2G_MIN:--60}" ] &&
		[ $((sig2 - sig)) -ge "${T5_2G_GAIN:-20}" ]; then
		target=$f2
		log "wifi: on a weak 5 GHz radio ($sig dBm) while '$ssid' has 2.4 GHz at $sig2 dBm; moving to $f2 MHz"
	fi
	[ -n "$target" ] || return 0

	$w set_network "$id" freq_list "$target" >/dev/null 2>&1
	$w reassociate >/dev/null 2>&1
	n=0
	while [ $n -lt 30 ]; do
		sleep 2; n=$((n+2))
		f=$(iw dev wlan0 link 2>/dev/null | sed -n 's/.*freq: \([0-9]*\).*/\1/p')
		if [ "${f%%.*}" = "$target" ] && $w status 2>/dev/null | grep -q '^wpa_state=COMPLETED'; then
			log "wifi: on $target MHz"
			return 0
		fi
	done
	log "wifi: no association on $target MHz in 30 s; back to every band for 30 minutes"
	$w set_network "$id" freq_list "" >/dev/null 2>&1
	$w reassociate >/dev/null 2>&1
	echo $((now + 1800)) > /run/techo5/prefer5-after
}

# t5_ntp_peers: ntpd's -p arguments, best first: the servers the network names
# in its DHCP lease (option 42, written by udhcpc.sh), the NTP server
# (pool.ntp.org unless NTP_SERVER says otherwise), and the default gateway.
# A network that keeps devices off the internet usually still says where the
# time is, or answers for it on the router. One that does not answer costs
# nothing.
t5_ntp_peers() {
	seen=" "
	for p in $(cat /run/techo5/ntp-servers 2>/dev/null) "${NTP_SERVER:-pool.ntp.org}" \
		$(ip route show default 2>/dev/null | awk '/^default/ {print $3; exit}'); do
		case "$seen" in *" $p "*) continue ;; esac
		seen="$seen$p "
		echo "-p $p"
	done
}

# t5_ntp: set the clock once from NTP (the RTC is not trusted), then write it
# to the RTC and to userdata so the next boot starts closer. Bounded: an
# unreachable server must not hold the boot.
t5_ntp() {
	# shellcheck disable=SC2046 # the peers are separate words on purpose
	timeout -s KILL ${NTP_WAIT:-40} ntpd -n -q $(t5_ntp_peers) > /tmp/ntpd.log 2>&1 || { log "clock: ntp failed"; return 1; }
	hwclock -w 2>/dev/null
	t5_clock_save
	log "clock: $(date)"
}

# t5_clock_start: set the clock now and keep it set: one bounded NTP query, then ntpd
# running. Once at a time: the boot script, the network keeper and udhcpc.sh (as soon as a
# lease is bound) all call it, and whichever comes first does it.
T5_NTP_LOCK=${T5_NTP_LOCK:-/run/techo5/ntp-start}
t5_clock_start() {
	pidof ntpd >/dev/null && return 0
	mkdir -p "${T5_NTP_LOCK%/*}"
	t5_up=$(cut -d. -f1 /proc/uptime)
	if ! mkdir "$T5_NTP_LOCK" 2>/dev/null; then
		# Another caller is at it. One that was killed on the way leaves the mark behind, so it
		# only counts for as long as the NTP query can take (uptime: the clock itself jumps).
		t5_at=$(cat "$T5_NTP_LOCK/at" 2>/dev/null)
		case "$t5_at" in '' | *[!0-9]*) return 0 ;; esac
		[ $((t5_up - t5_at)) -le $((${NTP_WAIT:-40} + 20)) ] && return 0
	fi
	echo "$t5_up" > "$T5_NTP_LOCK/at"
	t5_ntp
	# shellcheck disable=SC2046
	pidof ntpd >/dev/null || ntpd $(t5_ntp_peers) > /dev/null 2>&1
	rm -rf "$T5_NTP_LOCK"
}

# The Echo has no battery for its RTC: after a power cut it starts in 2010, and until NTP
# answers every certificate looks not yet valid. The last time it knew is kept on userdata
# (as fake-hwclock does) and put back early in the boot. It only ever moves the clock
# forward: an older saved time, or one an RTC that did keep running is already past, is left
# alone, and a forward clock is what NTP corrects next anyway.
T5_CLOCK=${T5_CLOCK:-/data/misc/techo5/clock}

# t5_clock_save: write the time now, if it is later than the one saved.
t5_clock_save() {
	[ -d "${T5_CLOCK%/*}" ] || return 0
	t5_now=$(date +%s)
	t5_saved=$(cat "$T5_CLOCK" 2>/dev/null)
	case "$t5_saved" in '' | *[!0-9]*) t5_saved=0 ;; esac
	[ "$t5_now" -gt "$t5_saved" ] || return 0
	echo "$t5_now" > "$T5_CLOCK.tmp" && mv -f "$T5_CLOCK.tmp" "$T5_CLOCK"
}

# t5_clock_restore: set the clock to the saved time, when that is later than the clock.
t5_clock_restore() {
	t5_saved=$(cat "$T5_CLOCK" 2>/dev/null)
	case "$t5_saved" in '' | *[!0-9]*) return 0 ;; esac
	[ "$t5_saved" -gt "$(date +%s)" ] || return 0
	date -s "@$t5_saved" > /dev/null 2>&1 && log "clock: $(date), the last time saved"
}

# t5_dropbear <keydir> [extra args]: SSH on port 22 with host keys kept in
# <keydir> (on userdata, so the host key is stable across images and slots).
# /etc/dropbear is a symlink to <keydir> in the rootfs; in the initramfs it is
# a directory and gets links to the keys.
t5_dropbear() {
	keydir=$1; shift
	mkdir -p "$keydir" /etc/dropbear
	for t in rsa ed25519; do
		[ -e "$keydir/dropbear_${t}_host_key" ] || dropbearkey -t $t -f "$keydir/dropbear_${t}_host_key" >/dev/null 2>&1
		[ -L /etc/dropbear ] || ln -sf "$keydir/dropbear_${t}_host_key" /etc/dropbear/dropbear_${t}_host_key
	done
	pidof dropbear >/dev/null && return 0
	dropbear -R -p 22 "$@" > /tmp/dropbear.log 2>&1 && log "ssh: dropbear listening on :22" || { log "ssh: dropbear failed to start"; return 1; }
}

# Bluetooth: the vendor driver gives a raw H4 channel (/dev/stpbt); btbridge
# turns it into hci0 through the kernel's vhci driver; BlueZ and bluez-alsa
# sit on top. Needs a kernel with CONFIG_BT + CONFIG_BT_HCIVHCI — without
# /dev/vhci this quietly does nothing, so an older boot image keeps working.
t5_bt_up() {
	mod=$1; logdir=${2:-/tmp}
	[ -e /dev/vhci ] || { log "bt: no /dev/vhci (kernel without Bluetooth); skipping"; return 1; }
	# BT_UART (device.conf): a Broadcom controller on a tty, which btbridge patches and brings up itself
	# (the Echo Spot); otherwise the MediaTek driver module and its /dev/stpbt.
	if [ -n "${BT_UART:-}" ]; then
		[ -e "$BT_UART" ] || { log "bt: no $BT_UART"; return 1; }
	elif [ ! -e /dev/stpbt ]; then
		[ -e "$mod" ] || { log "bt: driver not found at $mod"; return 1; }
		insmod "$mod" 2>/tmp/insmod-bt.err || { log "bt: insmod failed: $(cat /tmp/insmod-bt.err)"; return 1; }
		n=0; while [ $n -lt 10 ] && [ ! -e /dev/stpbt ]; do sleep 1; n=$((n+1)); done
		[ -e /dev/stpbt ] || { log "bt: driver loaded but no /dev/stpbt"; return 1; }
	fi
	command -v btbridge >/dev/null || { log "bt: no btbridge"; return 1; }
	# The factory address from IDME; the firmware otherwise comes up with a random one.
	addr=$(tr -d '\n\0' < /proc/idme/bt_mac_addr 2>/dev/null)
	if [ -n "${BT_UART:-}" ]; then
		(while true; do btbridge -uart "$BT_UART" ${BT_HCD:+-hcd "$BT_HCD"} ${BT_BAUD:+-baud "$BT_BAUD"} ${addr:+-bdaddr "$addr"} >> "$logdir/btbridge.log" 2>&1; sleep 2; done) &
	else
		(while true; do btbridge ${addr:+-bdaddr "$addr"} >> "$logdir/btbridge.log" 2>&1; sleep 2; done) &
	fi
	n=0; while [ $n -lt 20 ] && [ ! -d /sys/class/bluetooth/hci0 ]; do sleep 1; n=$((n+1)); done
	[ -d /sys/class/bluetooth/hci0 ] || { log "bt: bridge up but no hci0"; return 1; }
	# Pairings and bluez-alsa's state must survive reboots and slot changes: keep
	# them on userdata (the root is read-only).
	mkdir -p /run/dbus /data/misc/techo5/bluetooth /data/misc/techo5/bluealsa
	mountpoint -q /var/lib/bluetooth || mount --bind /data/misc/techo5/bluetooth /var/lib/bluetooth
	# Alpine's bluez-alsa was built with /usr/var as its state directory.
	for d in /var/lib/bluealsa /usr/var/lib/bluealsa; do
		[ -d "$d" ] && { mountpoint -q "$d" || mount --bind /data/misc/techo5/bluealsa "$d"; }
	done
	if ! pidof dbus-daemon >/dev/null; then
		dbus-daemon --system --nofork --nopidfile >> "$logdir/dbus.log" 2>&1 &
		sleep 1
	fi
	bd=$(command -v bluetoothd || echo /usr/lib/bluetooth/bluetoothd)
	[ -x "$bd" ] && "$bd" -n >> "$logdir/bluetoothd.log" 2>&1 &
	# Seen on the bench: after the first power-on the controller answers commands but never
	# reports an inquiry result or an advertisement until it has been powered off and on once.
	n=0; while [ $n -lt 10 ] && ! timeout 3 btmgmt info 2>/dev/null | grep -q "current settings: powered"; do sleep 1; n=$((n+1)); done
	timeout 5 btmgmt power off >/dev/null 2>&1; sleep 1; timeout 5 btmgmt power on >/dev/null 2>&1
	# bluez-alsa registers its A2DP endpoints with bluetoothd, so it must come after it.
	command -v bluealsa >/dev/null && bluealsa -p a2dp-source >> "$logdir/bluealsa.log" 2>&1 &
	log "bt: hci0 up; bluetoothd and bluealsa started"
	return 0
}
