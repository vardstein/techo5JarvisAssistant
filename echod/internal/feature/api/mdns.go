package api

import (
	"context"
	"log/slog"
	"time"

	"github.com/ygelfand/go-esphome-device/mdns"

	"github.com/HuskerMinion/techo5/echod/internal/hardware/metrics"
	"github.com/HuskerMinion/techo5/echod/internal/layout"
)

// mdnsRetry is how long to wait between attempts. There is no attempt limit: wifi can come back long
// after boot, and an advert that never reappears is a device that has to be found by address.
const mdnsRetry = 3 * time.Second

// advertMode is what the advert says, and whether there is one. A device in setup mode with a key
// nobody holds keeps off mDNS: Home Assistant would show it under Discovered and ask for that key, and
// while that card is open it ignores the device turning up again once it can be added (it already has
// a flow for that MAC). Opening a window puts it on the zero key, and it is advertised as one Home
// Assistant can give a key to.
func (a *API) advertMode() (show, unkeyed bool) {
	a.mu.Lock()
	unkeyed = a.unkeyed
	a.mu.Unlock()
	return unkeyed || !a.InSetup(), unkeyed
}

// advertise publishes the addresses the device actually has, and republishes them when they change.
//
// echod starts from init, well before wifi has associated. The addresses are passed rather than left
// to the library, which falls back to loopback when nothing routable exists — a record Home Assistant
// discovers and then cannot connect to, which is worse than no record at all. Discovery is a
// convenience and a device reachable by address works without it, so this only logs.
func (a *API) advertise(ctx context.Context, port int) {
	quiet := false
	for attempt := 1; ; attempt++ {
		show, unkeyed := a.advertMode()
		if !show {
			if !quiet {
				slog.Info("in setup mode: not advertising over mdns until a window for adding it is open")
				quiet = true
			}
			if !pause(ctx, mdnsRetry) {
				return
			}
			continue
		}
		quiet = false

		ips := metrics.Addresses()
		if len(ips) == 0 {
			if attempt == 1 {
				slog.Info("waiting for an address before advertising over mdns")
			}
			if !pause(ctx, mdnsRetry) {
				return
			}
			continue
		}

		adv, err := mdns.Advertise(mdns.Config{
			Name:          a.name,
			FriendlyName:  a.srv.Info.FriendlyName,
			Port:          port,
			MACAddress:    a.srv.Info.MACAddress,
			Version:       a.srv.Info.Version,
			Platform:      layout.Platform,
			Board:         layout.Board,
			Encrypted:     !unkeyed,
			Provisionable: unkeyed,
			IPs:           ips,
		})
		if err != nil {
			// Only the first failure is worth a warning: after that it is the expected state of a
			// device waiting for its network, and saying so every few seconds buries everything else.
			if attempt == 1 {
				slog.Warn("mdns advertise failed, retrying", "err", err)
			} else {
				slog.Debug("mdns advertise failed", "attempt", attempt, "err", err)
			}
			if !pause(ctx, mdnsRetry) {
				return
			}
			continue
		}

		slog.Info("advertising over mdns", "name", a.name, "port", port, "addrs", metrics.AddressKey(ips),
			"unkeyed", unkeyed)

		// The registration stands until the addresses it was made with are no longer the ones the
		// device has (a lease that changed, a network that arrived late), or the device took a key,
		// lost one, or went into setup mode.
		for {
			if !pause(ctx, mdnsRetry) {
				adv.Close()
				return
			}
			if metrics.AddressKey(metrics.Addresses()) != metrics.AddressKey(ips) {
				slog.Info("addresses changed, re-advertising over mdns", "was", metrics.AddressKey(ips))
				break
			}
			if s, u := a.advertMode(); !s || u != unkeyed {
				break
			}
		}
		adv.Close()
	}
}

// pause sleeps unless ctx ends first, reporting whether there is any point carrying on.
func pause(ctx context.Context, d time.Duration) bool {
	select {
	case <-ctx.Done():
		return false
	case <-time.After(d):
		return true
	}
}
