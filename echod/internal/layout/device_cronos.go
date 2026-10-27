//go:build !dot && !spot

package layout

import (
	"os"

	"github.com/HuskerMinion/techo5/echod/internal/board"
)

// The Echo Show 5 2nd gen on LineageOS 18.1: nothing of Amazon's is taken over. The daemon is an
// init service of its own (tools/init/techo5.rc) with its state on /data. There are no vendor boot
// hooks to keep current, so AnimationScripts is empty and update.Ensure has nothing to write.
const (
	// On Android the daemon is /system/bin/techo5, which is what tools/init/techo5.rc runs and what
	// the updater replaces in place (remounting / rw, as the Dot does for /system). On the Linux image
	// it is /usr/local/bin/techo5 under busybox init (tools/linux/rootfs); see layout.Dir.
	AndroidDir = "/system/bin"
	BinaryName = "techo5"
	StateDir   = "/data/misc/techo5"

	Service     = AndroidDir + "/" + BinaryName
	ServiceName = "techo5"

	StockLabel = "u:object_r:system_file:s0"

	StartAnimation = ""
	StopAnimation  = ""

	FirewallHook = ""

	// LogTag is the daemon's logcat tag: `adb logcat -s techo5`.
	LogTag = "techo5"

	// Manufacturer and System are what Home Assistant and the other house services are told made
	// this and what it runs: the hardware is Amazon's, everything on it is OpenJade's.
	Manufacturer = "OpenJade"
	System       = "Genbu Show"

	// DefaultName is the fallback display name when a device has none recorded.
	DefaultName = "Echo Show"

	// LogPath is where techo5-run sends the daemon's output, and BootLog what the boot script kept
	// of its own run. Both are what a diagnostics bundle reads.
	LogPath = "/data/techo5-linux/techo5.log"
	BootLog = "/run/boot.log"
)

var AnimationScripts = []string{}

// Board and Model say which Echo with a screen this is. One build serves all three: the 1st gen
// Show 5 (checkers, 2019), the 2nd gen Show 5 (cronos, 2021) and the 1st gen Show 8 (crown, 2019)
// run the same kernel commit on the same SoC, and the parts that differ — the speaker codec, the
// mute driver, the camera sensor, the microphone array, the panel — ask at run time.
// techo5-checkers docs/hardware.md has what is known of the 1st gen Show 5; the Show 8 is in
// TECHO5-CROWN crown-port-notes.md, off the repository because the dump beside it is unredacted.
//
// The board is board.json's: what the installer wrote of this unit from the board catalogue, or what
// board.Migrate wrote on a unit installed before there was one. Where it is missing, unreadable or
// names a board this build doesn't carry, it is the one board whose android_product the kernel
// command line names, the rule Migrate writes by, so the next start reads the same from the file.
// Nothing else is gone by (GENBU-193). A unit that gives neither has no board: it runs on the values
// the board predicates fall back to, which are the 2nd gen Show 5's, says so, and doesn't update.
var (
	known, _         = board.Known()
	Board, BoardFrom = showBoard(known, BoardFile, kernelCmdline())
	// Hardware is the Echo for people, Model the same with the board's codename for whoever reads a
	// diagnostics bundle. Both are the catalogue's, from the board's own file.
	Hardware = hardwareOf(Board)
	Model    = modelOf(Board)
	// OTABoard is the board updates go by: board.json's alone. A unit that knows its board only from
	// the kernel command line doesn't update until it has the file (GENBU-193).
	OTABoard = otaBoard(Board, BoardFrom)
)

// BoardFile is board.json, what the installer wrote of this unit's board from the catalogue.
const BoardFile = StateDir + "/board.json"

const (
	boardCheckers = "checkers"
	boardCrown    = "crown"

	// unknownHardware is what a unit with no board is called.
	unknownHardware = "Echo Show"
)

// Checkers is whether this is the 1st gen Echo Show 5.
func Checkers() bool { return Board == boardCheckers }

// Crown is whether this is the 1st gen Echo Show 8.
func Crown() bool { return Board == boardCrown }

// Display is the board's panel size, the 2nd gen Show 5's where there is no board.
func Display() (w, h int) {
	if b, ok := known[Board]; ok {
		return b.Display.Width, b.Display.Height
	}
	return 960, 480
}

// showBoard names the board and where it came from, out of board.json at p and the kernel command
// line, and nothing else: a board this build doesn't carry is no answer.
func showBoard(known map[string]board.Board, p, cmdline string) (string, string) {
	if b, err := board.Load(p); err == nil {
		if _, ok := known[b.ID]; ok {
			return b.ID, board.FromFile
		}
	}
	if b, ok := board.ByProduct(known, board.Product(cmdline)); ok {
		return b.ID, board.FromCmdline
	}
	return "", ""
}

func hardwareOf(id string) string {
	if b, ok := known[id]; ok {
		return b.Hardware()
	}
	return unknownHardware
}

func modelOf(id string) string {
	if id == "" {
		return unknownHardware + " (unknown board)"
	}
	return hardwareOf(id) + " (" + id + ")"
}

func otaBoard(id, from string) string {
	if from != board.FromFile {
		return ""
	}
	return id
}

func kernelCmdline() string {
	b, _ := os.ReadFile("/proc/cmdline")
	return string(b)
}
