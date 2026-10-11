//go:build !dot && !spot

package display

import (
	"image"
	"image/draw"
	"strings"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/i18n"
	"github.com/HuskerMinion/techo5/echod/internal/layout"
)

// The starter guide is the first-run card: a few pages on what to say, where things are and how to
// change the look. A tap goes on to the next page, Skip or a tap on the last page puts it away, and
// config.Screen.Welcomed keeps it away. That flag lives with the rest of the state on /data, which an
// update leaves alone, so the guide comes up on a device's first boot and after a reset, never after
// an update.

// guidePage is one page of the guide: its heading and its paragraphs.
type guidePage struct {
	title string
	paras []string
}

// guidePages is the guide, the first page first.
func guidePages() []guidePage {
	name := config.Get().Device.Name
	if name == "" {
		name = layout.System
	}
	word := strings.ReplaceAll(config.Get().Wake.Slot(0).ID, "_", " ")
	if word == "" {
		word = i18n.T("the wake word")
	} else {
		word = strings.ToUpper(word[:1]) + word[1:]
	}
	return []guidePage{
		{"Hello", []string{
			i18n.F("This is {name}. Say \"{word}\" and ask for anything.", "name", name, "word", word),
			"A few short pages to show you around. You only see them this once.",
		}},
		{"Getting around", []string{
			"Swipe down from the top for settings: Wi-Fi, Bluetooth, cameras, radio, themes.",
			"Swipe in from the right edge for cameras, radio, announcements and calls.",
		}},
		{"More ways in", []string{
			"Swipe in from the left edge for the Home Assistant dashboard.",
			"Say \"show the front door\" or \"go home\" to move the screen.",
		}},
		{"Make it yours", []string{
			"It starts in Obsidian, Genbu's own dark look.",
			"For another theme, swipe down from the top and open Display, then Theme. Custom colors are there too.",
		}},
	}
}

// welcome draws the guide's page (counted from 0; past the end is the last page).
func (r *renderer) welcome(s scene, page int) {
	pages := guidePages()
	page = min(max(page, 0), len(pages)-1)
	p := pages[page]
	// Over the start screen's temple, the same picture the splash was just showing (and has cached),
	// so the guide follows on from it in the theme's colors.
	sp := newSplash(r)
	draw.Draw(r.dst, r.dst.Rect, r.splashScene(sp, sp.markRect(r).Min.Y >= 0), image.Point{}, draw.Src)
	card := r.guideCard()
	r.bevel(card, shift(walnut, 10), true)
	left := card.Min.X + r.s(40)
	r.text(r.title, p.title, left, card.Min.Y+r.s(78), amber)
	for _, l := range r.guideLines(p) {
		r.text(r.small, l.text, left, l.y, cream)
	}

	// Where the reader is: a dot a page by the heading, the current one larger and lit.
	gap, cy := r.s(24), card.Min.Y+r.s(64)
	for i := range pages {
		x := card.Max.X - r.s(40) - (len(pages)-1-i)*gap
		if i == page {
			r.disc(x, cy, r.s(7), amber)
		} else {
			r.disc(x, cy, r.s(4), dim)
		}
	}
	base := r.guideFoot()
	hint := "Tap for the next page"
	if page == len(pages)-1 {
		hint = "Tap anywhere to begin"
	} else {
		r.text(r.small, "Skip", left, base, dim)
	}
	r.text(r.small, hint, card.Max.X-r.s(40)-r.width(r.small, hint), base, amber)
}

// guideLine is a line of a page's text and its baseline.
type guideLine struct {
	text string
	y    int
}

// guideLines lays a page's paragraphs out under its heading, wrapped to the card.
func (r *renderer) guideLines(p guidePage) []guideLine {
	card := r.guideCard()
	var lines []guideLine
	y, lead := card.Min.Y+r.s(140), r.s(40)
	for _, para := range p.paras {
		for _, line := range r.wrap(r.small, para, card.Dx()-2*r.s(40)) {
			lines = append(lines, guideLine{line, y})
			y += lead
		}
		y += lead / 3
	}
	return lines
}

// guideFoot is the baseline of the hints at the foot of the card.
func (r *renderer) guideFoot() int {
	return r.guideCard().Max.Y - r.s(38)
}

// guideCard is the card the guide is drawn on.
func (r *renderer) guideCard() image.Rectangle {
	return image.Rect(r.margin, r.s(30), r.w-r.margin, r.h-r.s(30))
}

// guideSkip is where a tap means Skip: the bottom-left corner of the card, larger than the word so a
// thumb finds it.
func (r *renderer) guideSkip() image.Rectangle {
	card := r.guideCard()
	return image.Rect(card.Min.X, card.Max.Y-r.s(90), card.Min.X+r.s(220), card.Max.Y)
}
