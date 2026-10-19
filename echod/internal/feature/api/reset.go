package api

import (
	"errors"
	"fmt"
	"log/slog"
	"os"
	"regexp"
	"strings"
	"syscall"
	"time"

	esphome "github.com/ygelfand/go-esphome-device"

	"github.com/HuskerMinion/techo5/echod/internal/lib/safe"
)

// Resetting the device to setup, for a Show deleted in Home Assistant (GENBU-223): Home Assistant asks
// over the device's own encrypted link, so nothing else on the network can. The device leaves the
// boot script's factory reset mark, answers, and restarts; the boot script then erases what the house
// gave it (t5_factory_reset), the same as the screen's Factory reset. With keep_wifi it keeps the Wi-Fi
// it knew and comes back in setup mode on the same network, where a Home Assistant can add it again
// through the setup page's adoption window (adopt.go).
//
// The answer is Home Assistant's proof that the device heard it before its ESPHome entry goes. Nothing
// is erased here, while the daemon runs and could write some of it back.

// resetWord is what the action's confirm argument has to say, as for leaving.
const resetWord = "reset"

// resetDelay lets the answer reach Home Assistant before the restart takes the connection.
const resetDelay = 2 * time.Second

// resetLib is the boot script that erases by the mark. A variable for the tests.
var resetLib = "/lib/techo5-lib.sh"

// The mark is wherever the boot script looks for it (T5_RESET), not a path of our own, so the two can't
// drift apart. A system old enough not to read keep-wifi would erase the Wi-Fi anyway, and one with no
// mark at all (the Android builds) would erase nothing.
var (
	resetMarkRe = regexp.MustCompile(`(?m)^T5_RESET=(/[A-Za-z0-9._/-]+)$`)
	keepsWifiRe = regexp.MustCompile(`(?m)^T5_RESET_KEEPS=.*\bwifi\b`)
)

// reboot restarts the device the plain way, as the screen's reset does. A variable for the tests.
var reboot = func() {
	syscall.Sync()
	if err := syscall.Reboot(syscall.LINUX_REBOOT_CMD_RESTART); err != nil {
		slog.Error("reset to setup: restart failed; the mark stays and erases on the next start", "err", err)
	}
}

func resetAction() *esphome.Action {
	return &esphome.Action{
		Name:    "reset_to_setup",
		Args:    []esphome.Arg{{Name: "confirm", Type: esphome.ArgString}, {Name: "keep_wifi", Type: esphome.ArgBool}},
		Answers: true,
		Run: func(c esphome.Call) (any, error) {
			if strings.ToLower(strings.TrimSpace(c.String("confirm"))) != resetWord {
				return nil, errors.New(`nothing was changed: set confirm to "reset" to erase this device back to setup`)
			}
			keep := c.Bool("keep_wifi")
			if err := markReset(resetLib, keep); err != nil {
				return nil, err
			}
			slog.Warn("reset to setup asked by Home Assistant; restarting to erase", "keep_wifi", keep)
			safe.Go("reset to setup", func() {
				time.Sleep(resetDelay)
				reboot()
			})
			return map[string]any{"reset": true, "keep_wifi": keep}, nil
		},
	}
}

// markReset leaves the mark the boot script erases by, once it knows the script will do what was
// asked. Nothing is changed when it can't.
func markReset(libPath string, keepWifi bool) error {
	lib, err := os.ReadFile(libPath)
	m := resetMarkRe.FindSubmatch(lib)
	if err != nil || m == nil {
		return errors.New("nothing was changed: this device's system can't be reset from Home Assistant")
	}
	if keepWifi && !keepsWifiRe.Match(lib) {
		return errors.New("nothing was changed: this device's system can't keep its Wi-Fi through a reset; " +
			"update it first, or reset without keeping the Wi-Fi")
	}
	mark := "home-assistant\n"
	if keepWifi {
		mark += "keep-wifi\n"
	}
	if err := os.WriteFile(string(m[1]), []byte(mark), 0o600); err != nil {
		return fmt.Errorf("nothing was changed: leaving the reset mark failed: %w", err)
	}
	return nil
}
