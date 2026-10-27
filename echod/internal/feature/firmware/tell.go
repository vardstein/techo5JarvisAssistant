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
	"github.com/HuskerMinion/techo5/echod/internal/lib/hass"
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
// out is asked for once more; when that one answers nothing either, the device says so and leaves it for
// the next reminder (GENBU-252). With no Home Assistant to ask through, it is only said.
//
// A Show whose quiet hours are Never still asks nothing between nightWithout's hours: Never is about its
// own noises, and nobody wants an update question at five in the morning (GENBU-264). A new dev build
// does not start the reminders over either; dev makes several a day, and each one asked about at once
// had a Show asking more often than every remindEvery.
//
// A yes is for the house, not only this Show (GENBU-125): Genbu is asked to install the update on every
// other Show that has one waiting too, so nobody has to answer each of them. Without Genbu that call
// goes nowhere, and only this Show updates.
//
// A Show following the house asks about the house update instead, when Genbu names it as the one to ask
// (GENBU-119): a yes moves the house, and this Show moves with the others.
var (
	say         = func(words string) { safe.Go("update notice", func() { remind.Say(words) }) }
	ask         = remind.Ask
	canSay      = func() bool { return remind.CanSay() && i18n.Known() } // never a guessed language
	quiet       = askQuiet
	install     = func(u *Firmware) { safe.Go("update install", func() { u.Install(context.Background()) }) }
	updateHouse = func() { safe.Go("house update", func() { installHouse(context.Background()) }) }
)

const (
	// remindEvery is how long after saying an update is ready it is said again, while it still waits.
	remindEvery = 4 * time.Hour
	// answerWithin is how long the question waits for its answer.
	answerWithin = time.Minute
	// houseWithin is how long asking Genbu for the house-wide update may take.
	houseWithin = 30 * time.Second
	// nightWithout is when a Show whose quiet hours are Never asks nothing out loud.
	nightWithout = "22-8"
)

func askQuiet() bool { return quietAt(time.Now()) }

// quietAt is whether asking out loud waits at now: the Show's quiet hours, or nightWithout when it has
// none.
func quietAt(now time.Time) bool {
	sp := config.Get().Speaker
	if sp.QuietHours == "" {
		return config.InWindow(nightWithout, now)
	}
	return sp.Quiet(now)
}

// devBuild is whether v is a CI dev build, vX.Y.Z-dev.N.
func devBuild(v string) bool {
	_, pre, _ := strings.Cut(v, "-")
	return strings.HasPrefix(pre, "dev.")
}

func (u *Firmware) tell() { u.tellOf(u.channelOffered(), time.Now()) }

// tellOf says that v is ready, if it is due and can be said now.
func (u *Firmware) tellOf(v string, now time.Time) { u.tellAbout(v, now, false) }

// tellHouse says that v is ready for the house: a yes asks Genbu to move the house, and nothing installs
// here first.
func (u *Firmware) tellHouse(v string, now time.Time) { u.tellAbout(v, now, true) }

func (u *Firmware) tellAbout(v string, now time.Time, house bool) {
	if v == "" || quiet() || !canSay() {
		return
	}
	auto := !house && u.AutoInstall()
	c := config.Get().Update
	if v == c.Told && (auto || now.Sub(c.ToldAt) < remindEvery) {
		return
	}
	if v != c.Told && c.Told != "" && devBuild(v) && now.Sub(c.ToldAt) < remindEvery {
		return // the newer dev build is said when the wait is up
	}
	if err := config.Set().Update().Told(v, now); err != nil {
		slog.Error("saving the update notice failed", "err", err)
		return
	}
	slog.Info("saying an update is ready", "version", v, "again", v == c.Told, "house", house)
	if !auto {
		question.Ask("update", answerWithin, func() {
			slog.Info("installing the update, as asked", "version", v)
			say(i18n.T("Okay, installing it now."))
			if !house {
				install(u)
			}
			updateHouse()
		}, func() {
			say(i18n.T("Okay, I'll remind you later."))
		}, func() {
			ask(i18n.T("Sorry, I didn't catch that. Should I install the update now, yes or no?"))
		}, unclear)
		words := i18n.F("An update for this Show is ready, version {version}. Want me to install it now?", "version", spoken(v))
		if house {
			words = i18n.F("An update for your Shows is ready, version {version}. Want me to install it now?", "version", spoken(v))
		}
		if ask(words) {
			return
		}
		question.Withdraw("update")
	}
	if house {
		say(i18n.F("An update for your Shows is ready, version {version}. You can install it in Home Assistant.", "version", spoken(v)))
		return
	}
	say(notice(v, auto))
}

// installHouse asks Genbu to install updates on every Show that has one, leaving this one out: it is
// installing already, and a second install on top would fetch the same update twice.
func installHouse(ctx context.Context) {
	if !hass.Get().Ready() {
		return
	}
	ctx, cancel := context.WithTimeout(ctx, houseWithin)
	defer cancel()
	live, err := hass.Get().OpenLive(ctx)
	if err != nil {
		slog.Info("no house-wide update: Home Assistant not reached", "err", err)
		return
	}
	defer live.Close()
	if err := live.CallService(ctx, "genbu", "install_updates", map[string]any{"except_node": config.Get().Device.Name}); err != nil {
		slog.Info("no house-wide update through Genbu", "err", err)
		return
	}
	slog.Info("asked Genbu to update the other Shows too")
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

// unclear is said when the second asking got words that answer nothing; the next reminder asks again.
func unclear() { say(i18n.T("That wasn't a yes or no. I'll ask again later.")) }
