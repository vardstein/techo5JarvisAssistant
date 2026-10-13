#!/bin/sh
# udhcpc hook for the TECHO5 root filesystem. The root is read-only, so the
# resolver file lives on /run (/etc/resolv.conf is a symlink to it); busybox's
# stock script would try to write a temporary file next to it.
RESOLV=/run/resolv.conf

case "$1" in
deconfig)
	ip link set "$interface" up
	ip -4 addr flush dev "$interface"
	;;
bound|renew)
	ifconfig "$interface" "$ip" netmask "${subnet:-255.255.255.0}" ${broadcast:+broadcast $broadcast}
	if [ -n "$router" ]; then
		while ip route del default dev "$interface" 2>/dev/null; do :; done
		for r in $router; do
			ip route add default via "$r" dev "$interface" && break
		done
	fi
	{
		[ -n "$domain" ] && echo "search $domain"
		for d in $dns; do echo "nameserver $d"; done
	} > "$RESOLV.tmp" && mv -f "$RESOLV.tmp" "$RESOLV"
	# The time servers this network names (DHCP option 42), for t5_ntp_peers. When
	# they change, a running ntpd is asking the last network's: stop it, and it is
	# started again with these below.
	mkdir -p /run/techo5
	echo $ntpsrv > /run/techo5/ntp-servers.tmp
	if ! cmp -s /run/techo5/ntp-servers.tmp /run/techo5/ntp-servers; then
		mv -f /run/techo5/ntp-servers.tmp /run/techo5/ntp-servers
		killall ntpd 2>/dev/null
	else
		rm -f /run/techo5/ntp-servers.tmp
	fi
	# The clock, as soon as there is a network to ask: a unit that joined late (or at
	# somebody else's house) does not wait for the network keeper's next minute, and
	# runs on the time it saved until then. In the background: this hook holds udhcpc.
	( . /lib/techo5-lib.sh; t5_clock_start ) > /dev/null 2>&1 &
	;;
esac
exit 0
