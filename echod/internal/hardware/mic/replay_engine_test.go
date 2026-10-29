//go:build !spot

package mic

import (
	"os"
	"strconv"
	"strings"

	"github.com/HuskerMinion/techo5/echod/internal/lib/echoref"
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

// replayPlayed honours TECHO5_PLAYED: a mono 16-bit file at three times the capture rate, what the
// speaker was given while the take was recorded. It is pushed into a Tap of c's own the way the
// playback loop pushes, TECHO5_PLAYED_SKIP of its samples dropped first so it starts where the take
// does, and kept TECHO5_PLAYED_LEAD capture samples ahead of the capture (default 3000) for the queues
// in between. What it returns is to be called once before each block; nil without the file.
func replayPlayed(c *canceller) func() {
	name := os.Getenv("TECHO5_PLAYED")
	if name == "" {
		return nil
	}
	b, err := os.ReadFile(name)
	if err != nil {
		panic("TECHO5_PLAYED: " + err.Error())
	}
	skip, _ := strconv.Atoi(os.Getenv("TECHO5_PLAYED_SKIP"))
	lead, err := strconv.Atoi(os.Getenv("TECHO5_PLAYED_LEAD"))
	if err != nil {
		lead = 3000
	}
	b = b[min(len(b), 2*skip):]

	tap := echoref.NewTap()
	c.align = echoref.NewAligner(tap)
	const period = 768
	stereo := make([]byte, 4*period)
	var captured, written int
	return func() {
		captured += FrameSamples
		for written/3 < captured+lead {
			clear(stereo)
			for i := range period {
				if o := 2 * (written + i); o+1 < len(b) {
					copy(stereo[4*i:], b[o:o+2])
					copy(stereo[4*i+2:], b[o:o+2])
				}
			}
			tap.Push(stereo)
			written += period
		}
	}
}
