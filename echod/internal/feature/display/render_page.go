//go:build !dot && !spot

package display

// The full-screen pages that open from the settings, Bluetooth audio and Wi-Fi, are levels of the
// settings in all but name: the same header with its back arrow, the same large rows, the same fades
// where the list runs on. Walking from the settings into one of them should not look like opening
// another program. Taps find what they hit through the zones each frame leaves, as in the settings.

// showZones makes the zones gathered for this frame the ones taps are matched to, with how far its
// list can scroll.
func (r *renderer) showZones(maxScroll int) {
	r.zmu.Lock()
	r.zones, r.pending = r.pending, r.zones
	r.cardMax, r.pickMax = maxScroll, 0
	r.zmu.Unlock()
}
