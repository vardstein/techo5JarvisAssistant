package echod

import (
	"log/slog"
	"os"
	"os/signal"
	"runtime/debug"
	"syscall"

	"github.com/spf13/cobra"

	"github.com/HuskerMinion/techo5/echod/internal/android/logd"
	"github.com/HuskerMinion/techo5/echod/internal/board"
	"github.com/HuskerMinion/techo5/echod/internal/boot"
	"github.com/HuskerMinion/techo5/echod/internal/layout"
	"github.com/HuskerMinion/techo5/echod/internal/lib/profile"
	"github.com/HuskerMinion/techo5/echod/internal/lib/safe"
	"github.com/HuskerMinion/techo5/echod/internal/update"
)

func newRunCmd() *cobra.Command {
	c := &cobra.Command{
		Use:   "run",
		Short: "Run the device agent",
		Long: "Plays the boot animation, then stays resident. echod is installed in place of\n" +
			"Amazon's ledcontroller service, so init starts it from on post-fs-data and\n" +
			"restarts it if it exits, and nothing else drives the ring.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			h := logd.NewHandler(layout.LogTag, cmd.ErrOrStderr())
			defer h.Close()
			slog.SetDefault(slog.New(h))

			// init discards our stderr, so a panic would otherwise vanish and look like a silent
			// restart. Log it, then let it kill the process as it would have.
			defer func() {
				if r := recover(); r != nil {
					slog.Error("panic", "panic", r, "stack", string(debug.Stack()))
					h.Close()
					panic(r)
				}
			}()

			ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
			defer stop()

			signal.Ignore(syscall.SIGHUP)

			if layout.BoardFile != "" {
				cmdline, _ := os.ReadFile("/proc/cmdline")
				board.Migrate(layout.BoardFile, string(cmdline))
				// Only a board.json that agrees with echod's own finding moves this Show to the
				// releases that go by board.json alone (GENBU-191). Any other state is logged by Note
				// and keeps it on the old manifest names.
				if state := board.Note(layout.BoardFile, layout.Board, layout.Hardware); state == board.Match {
					update.GoByBoardFile()
					slog.Info("update", "manifest", update.V2Manifest)
				} else {
					slog.Warn("update: board.json is not one to go by; staying on the old manifest names",
						"board_json", state)
				}
			}
			safe.Go("profile", func() { profile.Watch(ctx) })
			return boot.Run(ctx)
		},
	}

	return c
}
