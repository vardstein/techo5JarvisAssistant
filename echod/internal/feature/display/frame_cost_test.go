//go:build !dot && !spot

package display

import (
	"fmt"
	"image"
	"os"
	"sort"
	"sync"
	"testing"
	"time"
)

// With SHOW_FRAMECOST set, the scene tests also time each scene's draw and print the slowest, so the
// cost of a frame can be read on the device's own CPU: built with GOARCH=arm64 and run from /tmp.
var frameCost = struct {
	on bool
	sync.Mutex
	took map[string]time.Duration
}{on: os.Getenv("SHOW_FRAMECOST") != "", took: map[string]time.Duration{}}

func timeDraw(name string, img *image.RGBA, s scene) {
	if !frameCost.on {
		return
	}
	const n = 5
	r := newRenderer(img)
	r.draw(s) // fonts and caches warmed, as they are after the first frame
	start := time.Now()
	for range n {
		r = newRenderer(img)
		r.draw(s)
	}
	frameCost.Lock()
	frameCost.took[name] = time.Since(start) / n
	frameCost.Unlock()
}

func TestMain(m *testing.M) {
	code := m.Run()
	printFrameCost()
	os.Exit(code)
}

func printFrameCost() {
	if !frameCost.on {
		return
	}
	names := make([]string, 0, len(frameCost.took))
	for n := range frameCost.took {
		names = append(names, n)
	}
	sort.Slice(names, func(i, j int) bool { return frameCost.took[names[i]] > frameCost.took[names[j]] })
	for _, n := range names {
		fmt.Printf("%8.1f ms  %s\n", float64(frameCost.took[n].Microseconds())/1000, n)
	}
}
