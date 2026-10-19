//go:build !spot

package mic

import (
	"os"
	"strings"
)

// setReplayEngine honours TECHO5_AEC: "webrtc" runs the helper process (techo5-aec on PATH), anything
// else the built-in filter. A helper that will not start is a failure here, not a silent fallback: the
// point of the run is to compare engines.
func setReplayEngine(c *canceller) {
	want := os.Getenv("TECHO5_AEC")
	if want == "" || want == "builtin" {
		return
	}
	args := strings.Fields(os.Getenv("TECHO5_AEC_ARGS"))
	if len(args) == 0 {
		args = []string{"--ns", "low"}
	}
	e, err := startExternal(args...)
	if err != nil {
		panic("echo canceller helper " + want + ": " + err.Error())
	}
	c.ext, c.engine = e, "webrtc"
}
