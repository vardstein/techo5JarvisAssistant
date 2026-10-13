package firmware

import (
	"context"
	"log/slog"
	"strings"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/question"
	"github.com/HuskerMinion/techo5/echod/internal/feature/remind"
	"github.com/HuskerMinion/techo5/echod/internal/i18n"
	"github.com/HuskerMinion/techo5/echod/internal/lib/safe"
)

// Saying that an update is ready. Home Assistant shows its update card, and the screen says so under the
// clock, but nobody opens either to look: the device says it out loud, and again every remindEvery until
// the update is installed, so it cannot be missed. Not in quiet hours, and not when there is nothing to
// say it with; the next check tries again, so a version found at night is said in the morning. With
// automatic installs on there is nothing for anybody to do, so that is said once.
//
// Otherwise it is asked, through Home Assistant, which opens the microphone after the question: "yes"
// installs it there and then, and "not now" leaves it for the next reminder. An answer nobody could make
// out is asked for once more. With no Home Assistant to ask through, it is only said.
var (
	say     = func(words string) { safe.Go("update notice", func() { remind.Say(words) }) }
	ask     = remind.Ask
	canSay  = remind.CanSay
	quiet   = config.Quiet
	install = func(u *Firmware) { safe.Go("update install", func() { u.Install(context.Background()) }) }
)

const (
	// remindEvery is how long after saying an update is ready it is said again, while it still waits.
	remindEvery = 4 * time.Hour
	// answerWithin is how long the question waits for its answer.
	answerWithin = time.Minute
)

func (u *Firmware) tell() { u.tellOf(u.channelOffered(), time.Now()) }

// tellOf says that v is ready, if it is due and can be said now.
func (u *Firmware) tellOf(v string, now time.Time) {
	if v == "" || quiet() || !canSay() {
		return
	}
	auto := u.AutoInstall()
	c := config.Get().Update
	if v == c.Told && (auto || now.Sub(c.ToldAt) < remindEvery) {
		return
	}
	if err := config.Set().Update().Told(v, now); err != nil {
		slog.Error("saving the update notice failed", "err", err)
		return
	}
	slog.Info("saying an update is ready", "version", v, "again", v == c.Told)
	if !auto {
		question.Ask("update", answerWithin, func() {
			slog.Info("installing the update, as asked", "version", v)
			say(i18n.T("Okay, installing it now."))
			install(u)
		}, func() {
			say(i18n.T("Okay, I'll remind you later."))
		}, func() {
			ask(i18n.T("Sorry, I didn't catch that. Should I install the update now, yes or no?"))
		})
		if ask(i18n.F("An update for this Show is ready, version {version}. Want me to install it now?", "version", spoken(v))) {
			return
		}
		question.Withdraw("update")
	}
	say(notice(v, auto))
}

// notice is what is said: the version, and what happens next.
func notice(version string, auto bool) string {
	if auto {
		return i18n.F("An update for this Show is ready, version {version}. It installs by itself tonight.", "version", spoken(version))
	}
	return i18n.F("An update for this Show is ready, version {version}. You can install it in Settings, under Updates.", "version", spoken(version))
}

// spoken is a version as it reads aloud: "1.0.1 dev 6", not "v1.0.1-dev.6".
func spoken(v string) string {
	v = strings.TrimPrefix(v, "v")
	core, pre, ok := strings.Cut(v, "-")
	if !ok {
		return core
	}
	return core + " " + strings.ReplaceAll(pre, ".", " ")
}
