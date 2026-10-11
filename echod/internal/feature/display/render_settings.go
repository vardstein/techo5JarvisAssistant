//go:build !dot && !spot

package display

import (
	"strings"
)

const (
	// topEdge is how far from the top a swipe down has to start to be the sheet.
	// A quarter of the panel: a finger reaching for the top lands 60-100 px down more often than on
	// the bezel (a swipe from y=81 missed the sheet on 2026-09-16).
	topEdge = 120
)

// stationLabel drops the service a station list names in each entry ("101.1 WXYZ on iHeartRadio"): on a
// list of them it is the same words on every row.
func stationLabel(name string) string {
	for _, tail := range []string{" on iHeartRadio", " on iHeart", " on TuneIn", " on Tune In"} {
		if t, ok := strings.CutSuffix(name, tail); ok {
			return t
		}
	}
	return name
}
