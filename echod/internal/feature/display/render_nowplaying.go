//go:build !dot && !spot

package display

import (
	"fmt"
	"image"
	"image/color"
	"image/draw"
	"math"
	"strings"
	"time"
	"unicode"

	xdraw "golang.org/x/image/draw"
	"golang.org/x/image/font"

	"github.com/HuskerMinion/techo5/echod/internal/feature/home"
	"github.com/HuskerMinion/techo5/echod/internal/i18n"
)

// nowPlaying is the idle screen while music plays or sits paused: the cover as a square on the left,
// and on the right where it plays, the song, who plays it and what comes next, the position, and one
// row of buttons. A station's logo stands on a card in the cover's place, and a song with no picture at
// all gets drawn notes (rings for talk radio).
//
// With a cover, the page is in the cover's colors the way Spotify's Car Thing was: the ground is
// made from the cover, darkened until white reads on it, the words are white with all but the song
// let through by the ground, and the controls are marks with no buttons under them. A song without
// one gets the same page on a ground made from the theme's color; only a station's logo keeps the
// theme's light page, as a logo is drawn for a white card.
//
// It used to draw the cover across the whole screen under a wash with the words over it. A cover
// washed dark enough for the words to read did not look like the cover any more, and one left bright
// enough to look like itself put the words over somebody's face.
func (r *renderer) nowPlaying(s scene) {
	rd := s.radio
	cover, col := r.nowPlayingLayout()
	r.cover.prepare(rd.Thumb, cover.Dx())
	// A song with a cover takes its ground from the cover, and one without from the theme's color, so
	// the page looks the same either way. A station's logo sits on the theme's page: a logo is drawn
	// for a white card, and its colors are the station's, not the music's.
	ink := themeInk()
	switch {
	case r.cover.img != nil && !rd.Logo:
		draw.Draw(r.dst, r.dst.Rect, r.cover.groundFor(r.dst.Rect.Size()), image.Point{}, draw.Src)
		ink = coverInk()
	case r.cover.img == nil:
		draw.Draw(r.dst, r.dst.Rect, r.plain.groundFor(r.dst.Rect.Size(), amber), image.Point{}, draw.Src)
		ink = coverInk()
	}
	r.coverArt(rd, cover)
	if s.library {
		r.libraryPill(r.libraryButton(), ink)
	}

	station := rd.Now
	if station == "" {
		station = rd.Chosen
	}
	if station == "" {
		station = "Radio"
	}
	// Where it plays, or the station; the play button already says whether it does. "Playing in"
	// in front cost the room's name its end on the Show 5.
	label := station
	if rd.Now == "Music Assistant" {
		label = musicPlace(s.music)
	}
	song, version := songParts(rd.Title)
	if s.showLyrics && rd.Title != "" {
		// The words take the song's place below, so the head says which song they are.
		label = song
	}

	// The head of the column: where it plays, and the time on the right. The page is looked at for
	// minutes, and the big clock is not on it.
	head := col.Min.Y + r.s(26)
	pill := r.clockPill(s.now)
	r.glassPill(pill, ink)
	r.text(r.small, clockText(s.now), pill.Min.X+r.s(16), head, ink.soft)
	labelEnd := pill.Min.X - r.s(16)
	if s.lyrics != nil {
		b := r.lyricsButton(s.now)
		r.lyricsPill(b, s.showLyrics, ink)
		labelEnd = b.Min.X - r.s(16)
	}
	r.text(r.small, r.fit(r.small, label, labelEnd-col.Min.X), col.Min.X, head, ink.head)

	fav, back, _, _, _ := r.nowPlayingButtons()
	bar := back.Min.Y - r.s(34)
	room := bar - r.s(44) // the words stop clear of the times over the bar

	if s.showLyrics {
		r.lyricWords(s, col, head, room, ink)
	} else {
		headline := station
		if rd.Title != "" {
			headline = song
		}
		face, step, lines := r.headlineLines(headline, col.Dx())
		y := head + r.s(66) - r.s(52-step)/2
		for _, line := range lines {
			r.text(face, line, col.Min.X, y, ink.title)
			y += r.s(step)
		}
		y -= r.s(step)
		line := func(face font.Face, text string, step int, c color.Color) {
			if text == "" || y+step > room {
				return
			}
			y += step
			r.text(face, r.clipTo(face, text, col.Dx()), col.Min.X, y, c)
		}
		// Who plays it is the second thing looked for, so a long name goes a size down before it is cut.
		artist := r.body
		if r.width(artist, rd.Artist) > col.Dx() {
			artist = r.small
		}
		line(artist, rd.Artist, r.s(48), ink.soft)
		// Which take of the song it is ("Radio Edit", "Remastered 2011") is worth less than the song
		// and who plays it, so it goes with the album, after it.
		// When the two don't fit on one line, the take gets a line of its own under the album.
		// A single's album is the song's own name again; saying it twice says nothing.
		albumName := rd.Album
		if a, _ := songParts(albumName); strings.EqualFold(strings.TrimSpace(a), strings.TrimSpace(song)) {
			albumName = ""
		}
		album := strings.TrimPrefix(albumName+"  ·  "+version, "  ·  ")
		switch {
		case album == "":
		case version == "" || albumName == "" || r.width(r.small, album) <= col.Dx():
			line(r.small, strings.TrimSuffix(album, "  ·  "), r.s(42), ink.faint)
		default:
			line(r.small, albumName, r.s(42), ink.faint)
			line(r.small, version, r.s(40), ink.faint)
		}

		// A group says which rooms it reached; the queue says what comes next.
		if rd.Now == "Music Assistant" && len(s.music.Rooms) > 1 {
			line(r.small, strings.Join(s.music.Rooms, "  ·  "), r.s(40), ink.head)
		}
		if rd.Now == "Music Assistant" && s.music.Next != "" {
			face, text := r.upNext(s.music.Next, col.Dx())
			line(face, text, r.s(40), ink.faint)
		}
	}

	// Music Assistant's player state supplies a best-effort position/duration. Sendspin deliberately
	// has no position in its protocol, so an unavailable HA value leaves an honest empty bar rather
	// than inventing timing.
	if rd.Now == "Music Assistant" {
		r.musicProgress(s.music, s.now, image.Rect(col.Min.X, bar, col.Max.X, bar+r.s(6)), ink)
	}

	// One row: the star, back, play or pause, forward, and stop; on a station only the star, play or
	// stop, and stop. Sendspin routes these back to Music Assistant, so the page controls the real
	// queue/group rather than a local shadow player.
	_, back, play, next, stop := r.nowPlayingButtons()
	defer r.favNotePill(s)
	defer r.pressLight(s.pressed)
	if ink.bare {
		r.bareButtons(s, fav, back, play, next, stop)
		return
	}
	rad := float64(r.s(14))
	ground := shift(walnut, 10)
	if !dark() {
		ground = shift(walnut, -6)
	}
	for _, b := range []image.Rectangle{fav, stop} {
		r.roundButton(b, rad, ground)
	}
	if !s.live {
		r.roundButton(back, rad, ground)
		r.roundButton(next, rad, ground)
	}
	r.roundButton(play, rad, amber)
	cx := func(b image.Rectangle) float64 { return float64(b.Min.X+b.Max.X) / 2 }
	cy := float64(play.Min.Y+play.Max.Y) / 2
	u := float64(r.s(13)) // half a mark's height
	if !s.live {
		r.markSkip(cx(back), cy, u, false, cream)
		r.markSkip(cx(next), cy, u, true, cream)
	}
	switch {
	case s.live && !s.paused:
		// A station can't be paused, only stopped: it comes back live, not where it was.
		r.roundFillF(cx(play)-u*0.85, cy-u*0.85, cx(play)+u*0.85, cy+u*0.85, u*0.22, onAccent())
	case s.paused:
		r.aaPoly([][2]float64{{cx(play) - u*0.7, cy - u*1.15}, {cx(play) + u*1.1, cy}, {cx(play) - u*0.7, cy + u*1.15}}, onAccent())
	default:
		bw := u * 0.62
		r.roundFillF(cx(play)-u*0.8, cy-u*1.05, cx(play)-u*0.8+bw, cy+u*1.05, bw/3, onAccent())
		r.roundFillF(cx(play)+u*0.8-bw, cy-u*1.05, cx(play)+u*0.8, cy+u*1.05, bw/3, onAccent())
	}
	starColor := dim
	if s.faved {
		starColor = amber
	}
	r.aaStar(cx(fav), cy, u*1.35, starColor)
	r.roundFillF(cx(stop)-u*0.8, cy-u*0.8, cx(stop)+u*0.8, cy+u*0.8, u*0.22, cream)
}

// nowPlayingLayout is the square the cover goes in and the column the words and buttons go in. The
// column is never narrower than the Show 5's 440: on the Show 8, which is less wide for its height,
// a cover the panel's full height would leave the buttons no room, so there the cover gives way.
// home's thumbSide makes the picture at this size, so the page does not scale it.
func (r *renderer) nowPlayingLayout() (cover, col image.Rectangle) {
	m := r.margin
	side := min(r.h-2*m, r.w-3*m-r.s(440))
	top := (r.h - side) / 2
	cover = image.Rect(m, top, m+side, top+side)
	col = image.Rect(cover.Max.X+m, top, r.w-m, top+side)
	return cover, col
}

// nowPlayingButtons is the row at the foot of the column, level with the bottom of the cover: the star
// and stop at its ends, the three transport buttons in the middle. The rectangles are the tap regions
// too.
func (r *renderer) nowPlayingButtons() (fav, back, play, next, stop image.Rectangle) {
	_, col := r.nowPlayingLayout()
	h := r.s(58)
	y := col.Max.Y - h
	fav = image.Rect(col.Min.X, y, col.Min.X+h, y+h)
	stop = image.Rect(col.Max.X-h, y, col.Max.X, y+h)
	back, play, next = r.transportButtons()
	return fav, back, play, next, stop
}

// transportButtons are back, play or pause, and forward, in the middle of the column. They are laid
// out from the panel's own size, because this page is drawn on the Show 8 as well: a width that fits
// the Show 5 is a third of a wider screen with the marks stranded in the corner of it.
func (r *renderer) transportButtons() (back, play, next image.Rectangle) {
	_, col := r.nowPlayingLayout()
	w, h, gap := r.s(92), r.s(58), r.s(12)
	x := col.Min.X + (col.Dx()-(3*w+2*gap))/2
	y := col.Max.Y - h
	back = image.Rect(x, y, x+w, y+h)
	play = image.Rect(x+w+gap, y, x+2*w+gap, y+h)
	next = image.Rect(x+2*(w+gap), y, x+3*w+2*gap, y+h)
	return back, play, next
}

// lyricsButton is the words button in the head of the column, left of the clock: a pill with lines of
// text drawn on it, because a word there cost the room's name most of its letters on the Show 5. It is
// there only for a song the music server has words for. The clock's width moves it, so a tap is checked
// against the same clock the frame drew.
func (r *renderer) lyricsButton(now time.Time) image.Rectangle {
	pill := r.clockPill(now)
	right := pill.Min.X - r.s(10)
	return image.Rect(right-r.s(56), pill.Min.Y, right, pill.Max.Y)
}

// clockPill is the time's place in the head: a pill at the column's right end, as tall as the words
// button beside it.
func (r *renderer) clockPill(now time.Time) image.Rectangle {
	_, col := r.nowPlayingLayout()
	w := r.width(r.small, clockText(now)) + 2*r.s(16)
	top := col.Min.Y - r.s(6)
	return image.Rect(col.Max.X-w, top, col.Max.X, top+r.s(42))
}

// glassPill is a pane of frosted glass over the page: on a cover's ground the ground let through a
// little lighter, with a brighter rim and a sheen across its upper half; on the theme's page a plain
// button, like the words button's.
func (r *renderer) glassPill(b image.Rectangle, page nowInk) {
	x0, y0, x1, y1 := float64(b.Min.X), float64(b.Min.Y), float64(b.Max.X), float64(b.Max.Y)
	rad := (y1 - y0) / 2
	if !page.bare {
		ground := shift(walnut, 10)
		if !dark() {
			ground = shift(walnut, -6)
		}
		r.roundButton(b, rad, ground)
		return
	}
	under := r.pixel(b)
	rim := float64(r.s(3)) / 2
	r.roundFillF(x0, y0, x1, y1, rad, lerp(under, white, 0.30))
	r.roundFillF(x0+rim, y0+rim, x1-rim, y1-rim, rad-rim, lerp(under, white, 0.12))
	// The sheen: the upper half a touch lighter still, its corners following the pill's.
	mid := (y0 + y1) / 2
	r.roundFillF(x0+rim, y0+rim, x1-rim, mid+rad/2, rad-rim, lerp(under, white, 0.17))
	r.roundFillF(x0+rim, mid, x1-rim, y1-rim, rad-rim, lerp(under, white, 0.12))
}

// favNotePill says what a press of the star did, as the Car Thing said "Saved to Your Library": a
// dark pill across the foot of the cover, clear of the library button, with the star lit when the
// song was saved. The cover is the one place on the page with nothing to read under it.
func (r *renderer) favNotePill(s scene) {
	if s.favNote == "" {
		return
	}
	cover, _ := r.nowPlayingLayout()
	in, h := r.s(12), r.s(42)
	left, right, bottom := cover.Min.X+in, cover.Max.X-in, cover.Max.Y-in
	mark := 0
	if s.faved {
		mark = r.s(30)
	}
	pad := r.s(18)
	need := r.width(r.small, s.favNote) + 2*pad + mark
	// Beside the books when it fits there, else a row above them across the cover.
	if lib := r.libraryButton(); s.library && need > lib.Min.X-in-left {
		bottom = lib.Min.Y - r.s(8)
	} else if s.library {
		right = lib.Min.X - in
	}
	// A size down before any of it is cut: the longer languages run past the cover at full size.
	face, base := r.small, r.s(31)
	if r.width(face, s.favNote) > right-left-2*pad-mark {
		face, base = r.tiny, r.s(29)
	}
	text := r.fit(face, s.favNote, right-left-2*pad-mark)
	w := min(r.width(face, text)+2*pad+mark, right-left)
	x := left + (right-left-w)/2
	b := image.Rect(x, bottom-h, x+w, bottom)
	r.roundFillF(float64(b.Min.X), float64(b.Min.Y), float64(b.Max.X), float64(b.Max.Y), float64(h)/2, lerp(r.pixel(b), color.RGBA{A: 255}, 0.72))
	if mark > 0 {
		r.aaStar(float64(b.Min.X+pad+r.s(10)), float64(b.Min.Y+b.Max.Y)/2, float64(r.s(11)), amber)
	}
	r.text(face, text, b.Min.X+pad+mark, b.Min.Y+base, white)
}

// libraryButton is the button for Music Assistant's own pages (the library, the queue, the groups): a
// pill of books in the cover's lower right corner. In the head beside the words button it left the
// room's name about half of what "Wohnzimmer" needs on the Show 5, and the row of buttons has no gap
// for it; on the cover it costs nothing, and a tap anywhere else on the cover is still play/pause.
func (r *renderer) libraryButton() image.Rectangle {
	cover, _ := r.nowPlayingLayout()
	w, h, in := r.s(56), r.s(42), r.s(12)
	return image.Rect(cover.Max.X-in-w, cover.Max.Y-in-h, cover.Max.X-in, cover.Max.Y-in)
}

// libraryPill is the button: three books on a shelf, the last one leaning. On a cover's ground it is
// the cover darkened under it, so it reads on any picture without a theme's color stuck on the cover.
func (r *renderer) libraryPill(b image.Rectangle, ink nowInk) {
	cream := cream
	if ink.bare {
		r.roundFillF(float64(b.Min.X), float64(b.Min.Y), float64(b.Max.X), float64(b.Max.Y), float64(b.Dy())/2, lerp(r.pixel(b), color.RGBA{A: 255}, 0.55))
		cream = white
	} else {
		ground := shift(walnut, 10)
		if !dark() {
			ground = shift(walnut, -6)
		}
		r.roundButton(b, float64(b.Dy())/2, ground)
	}
	x := float64(b.Min.X + r.s(17))
	cy, bw := float64(b.Min.Y+b.Max.Y)/2, float64(r.s(5))
	foot := cy + float64(r.s(9))
	for i, h := range []float64{17, 14} {
		left := x + float64(i)*(bw+float64(r.s(2)))
		r.roundFillF(left, foot-float64(r.s(int(h))), left+bw, foot, bw/4, cream)
	}
	lean := x + 2*(bw+float64(r.s(2)))
	top := foot - float64(r.s(16))
	r.aaPoly([][2]float64{{lean, foot}, {lean + bw, foot}, {lean + bw + float64(r.s(6)), top + float64(r.s(1))}, {lean + float64(r.s(6)), top}}, cream)
}

// lyricsPill is the button: lit while the words are up, on the ground's own color while they are not.
// On a cover's ground it is glass like the clock beside it, and white with the ground's color for
// lines when lit.
func (r *renderer) lyricsPill(b image.Rectangle, on bool, page nowInk) {
	if page.bare {
		if !on {
			r.glassPill(b, page)
			r.verseLines(b, white)
			return
		}
		under := r.pixel(b)
		r.roundFillF(float64(b.Min.X), float64(b.Min.Y), float64(b.Max.X), float64(b.Max.Y), float64(b.Dy())/2, white)
		r.verseLines(b, under)
		return
	}
	ground, ink := shift(walnut, 10), cream
	if !dark() {
		ground = shift(walnut, -6)
	}
	if on {
		ground, ink = amber, onAccent()
	}
	r.roundButton(b, float64(b.Dy())/2, ground)
	r.verseLines(b, ink)
}

// verseLines is the lyrics button's mark: three lines of a verse, ragged on the right.
func (r *renderer) verseLines(b image.Rectangle, ink color.RGBA) {
	x := float64(b.Min.X + r.s(17))
	cy, gap, th := float64(b.Min.Y+b.Max.Y)/2, float64(r.s(8)), float64(r.s(4))
	for i, w := range []float64{22, 15, 19} {
		y := cy + float64(i-1)*gap
		r.roundFillF(x, y-th/2, x+float64(r.s(int(w))), y+th/2, th/2, ink)
	}
}

// lyricWords is the song's words between the head and the bar, in place of the song's details. Timed
// words keep the line being sung near the top, lit, with the one before it above when it is short; untimed ones
// have no line being sung and move down through the song as it goes.
func (r *renderer) lyricWords(s scene, col image.Rectangle, head, room int, ink nowInk) {
	l := s.lyrics
	type row struct {
		text string
		line int
	}
	var rows []row
	first := make([]int, len(l.Lines))
	for i, ln := range l.Lines {
		first[i] = len(rows)
		text := ln.Text
		if text == "" && l.Synced {
			text = "♪" // a gap in timed words is the band playing
		} else if text == "" {
			rows = append(rows, row{"", i}) // and in untimed ones the gap between verses
			continue
		}
		for _, w := range r.wrap(r.body, text, col.Dx()) {
			rows = append(rows, row{r.clipTo(r.body, w, col.Dx()), i})
		}
	}
	step := r.s(50)
	top := head + r.s(62)
	fits := max((room-top)/step+1, 1)
	elapsed := time.Duration(s.music.Elapsed(s.now) * float64(time.Second))
	cur := l.Current(elapsed)
	start := 0
	switch {
	case l.Synced && cur >= 0:
		// The line before stays above for the eye coming back, unless it would show only its end.
		start = first[cur]
		if cur > 0 && first[cur]-first[cur-1] == 1 {
			start--
		}
	case !l.Synced && s.music.Duration > 0 && len(rows) > fits:
		start = int(float64(len(rows)-fits+1) * elapsed.Seconds() / s.music.Duration)
	}
	start = min(start, max(len(rows)-fits, 0))
	for i, y := start, top; i < len(rows) && y <= room; i, y = i+1, y+step {
		c := ink.soft
		switch {
		case !l.Synced || rows[i].line == cur:
			c = ink.title
		case rows[i].line < cur:
			c = ink.past
		}
		r.text(r.body, rows[i].text, col.Min.X, y, c)
	}
}

// coverArt draws the picture in its square: the cover with rounded corners and a shadow under it, a
// logo on a light card, or drawn notes or rings on a card of the ground's own color.
func (r *renderer) coverArt(rd home.Radio, b image.Rectangle) {
	rad := float64(r.s(18))
	under := r.pixel(b) // before the shadow darkens it
	r.roundShadow(b, rad, float64(r.s(18)), r.s(8), shadowAlpha())
	switch {
	case r.cover.img != nil && rd.Logo:
		r.roundFill(b, rad, color.RGBA{0xf4, 0xf2, 0xee, 0xff}, color.RGBA{0xe8, 0xe5, 0xe0, 0xff})
		pad := b.Dx() / 8
		inner := b.Inset(pad)
		xdraw.ApproxBiLinear.Scale(r.dst, inner, r.cover.img, r.cover.img.Bounds(), draw.Over, nil)
	case r.cover.img != nil:
		r.roundImage(b, rad, r.cover.img)
	default:
		// A card a shade lighter than the ground it sits on, with the marks in white let through.
		card := shift(under, 14)
		r.roundFill(b, rad, shift(card, 6), shift(card, -4))
		cx, cy, u := (b.Min.X+b.Max.X)/2, (b.Min.Y+b.Max.Y)/2, float64(b.Dx())/400
		amber := color.RGBA{0xff, 0xff, 0xff, 0xff}
		r.faded(fmt.Sprint(rd.Music || rd.Now == "Music Assistant", card), b, 60, func() {
			if rd.Music || rd.Now == "Music Assistant" {
				// Two notes: heads, stems and a beam.
				r.disc(cx-int(75*u), cy+int(80*u), int(34*u), amber)
				r.disc(cx+int(75*u), cy+int(60*u), int(34*u), amber)
				r.stroke(cx-int(47*u), cy+int(70*u), cx-int(47*u), cy-int(110*u), int(14*u), amber)
				r.stroke(cx+int(103*u), cy+int(50*u), cx+int(103*u), cy-int(130*u), int(14*u), amber)
				r.stroke(cx-int(53*u), cy-int(110*u), cx+int(109*u), cy-int(130*u), int(30*u), amber)
				return
			}
			// Talk: rings going out from a transmitter dot, largest first so each ring shows.
			for _, rr := range []int{150, 100, 50} {
				r.disc(cx, cy, int(float64(rr+9)*u), amber)
				r.disc(cx, cy, int(float64(rr-9)*u), card)
			}
			r.disc(cx, cy, int(18*u), amber)
		})
	}
}

// roundImage copies img, which is b's size, into b with its corners rounded off.
func (r *renderer) roundImage(b image.Rectangle, rad float64, img *image.RGBA) {
	x0, y0, x1, y1 := rectF(b)
	corner := int(rad) + 1
	for y := b.Min.Y; y < b.Max.Y; y++ {
		if y < r.dst.Rect.Min.Y || y >= r.dst.Rect.Max.Y {
			continue
		}
		sy := y - b.Min.Y
		edge := y < b.Min.Y+corner || y >= b.Max.Y-corner
		for x := b.Min.X; x < b.Max.X; x++ {
			if !edge && x == b.Min.X+corner {
				// The middle of the row has no corner in it: one copy.
				to := b.Max.X - corner
				copy(r.dst.Pix[r.dst.PixOffset(x, y):r.dst.PixOffset(to, y)], img.Pix[img.PixOffset(x-b.Min.X, sy):img.PixOffset(to-b.Min.X, sy)])
				x = to - 1
				continue
			}
			i := img.PixOffset(x-b.Min.X, sy)
			c := color.RGBA{img.Pix[i], img.Pix[i+1], img.Pix[i+2], 255}
			r.blendAt(x, y, c, clamp01(0.5-rrDist(float64(x)+0.5, float64(y)+0.5, x0, y0, x1, y1, rad)))
		}
	}
}

// coverCache is the cover as the page draws it, and the color it lends the ground. home makes the
// picture at the page's size already; this only scales one that came some other way, and only once.
type coverCache struct {
	src  *image.RGBA
	side int
	img  *image.RGBA
	tint color.RGBA
	// ground is the page's ground made from the cover, for the panel's size; see groundFor.
	ground *image.RGBA
}

func (c *coverCache) prepare(src *image.RGBA, side int) {
	if src == c.src && side == c.side {
		return
	}
	c.src, c.side, c.img, c.ground = src, side, nil, nil
	if src == nil || side <= 0 {
		return
	}
	if src.Bounds().Dx() == side && src.Bounds().Dy() == side && src.Bounds().Min == (image.Point{}) {
		c.img = src
	} else {
		c.img = image.NewRGBA(image.Rect(0, 0, side, side))
		xdraw.CatmullRom.Scale(c.img, c.img.Bounds(), src, src.Bounds(), draw.Src, nil)
	}
	c.tint = averageColor(c.img)
}

// headlineLines sets a song's title (or a station's name) in the column, in the largest bold size
// that holds it: one line at full size, then one line a size down, then two lines from full size
// down, broken where the lines come out most even. Only a title too long for two lines of the
// smallest size is cut, at the end of the second line. step is the line spacing, in drawn-for
// pixels.
func (r *renderer) headlineLines(s string, w int) (face font.Face, step int, lines []string) {
	type size struct {
		face font.Face
		step int
	}
	sizes := []size{{r.title, 52}, {r.title2, 46}, {r.title3, 40}}
	for _, z := range sizes[:2] {
		if r.width(z.face, s) <= w {
			return z.face, z.step, []string{s}
		}
	}
	words := strings.Fields(s)
	for _, z := range sizes {
		if two := r.evenBreak(z.face, words, w); two != nil {
			return z.face, z.step, two
		}
	}
	z := sizes[len(sizes)-1]
	lines = r.wrap(z.face, s, w)
	if len(lines) > 2 {
		lines = []string{lines[0], strings.Join(lines[1:], " ")}
	}
	for i := range lines {
		lines[i] = r.clipTo(z.face, lines[i], w)
	}
	return z.face, z.step, lines
}

// evenBreak is words as two lines no wider than w with the longer line as short as it can be, or
// nil when no break does it.
func (r *renderer) evenBreak(face font.Face, words []string, w int) []string {
	var best []string
	bestW := w + 1
	for k := 1; k < len(words); k++ {
		a, b := strings.Join(words[:k], " "), strings.Join(words[k:], " ")
		if most := max(r.width(face, a), r.width(face, b)); most <= w && most < bestW {
			best, bestW = []string{a, b}, most
		}
	}
	return best
}

// upNext is the line saying what the queue plays next, made to fit width before anything is cut: a
// size down first, then without the take ("Remastered 2011"), then the song alone without who plays it,
// which the queue puts in front ("Artist - Song"). Only a song name too long even for that is cut.
func (r *renderer) upNext(next string, width int) (font.Face, string) {
	label := i18n.T("Next") + "  ·  "
	song, _ := songParts(next)
	tries := []string{next, song}
	for _, sep := range []string{" – ", " - ", " — "} {
		if i := strings.Index(song, sep); i > 0 && i+len(sep) < len(song) {
			tries = append(tries, song[i+len(sep):])
			break
		}
	}
	for _, t := range tries {
		for _, face := range []font.Face{r.small, r.tiny} {
			if r.width(face, label+t) <= width {
				return face, label + t
			}
		}
	}
	return r.tiny, r.clipTo(r.tiny, label+tries[len(tries)-1], width)
}

// songParts splits a song's title into the song and which take of it this is, so the song can have
// the big line to itself: "Song (All Star Mix – Main Pass)" is "Song" and "All Star Mix – Main Pass",
// as is "Song - Radio Edit". Only a bracket or a dash at the end that names a take is split off;
// "Song (Part 2)" stays whole, as does a title that would be left with nothing.
func songParts(title string) (song, version string) {
	song = strings.TrimSpace(title)
	var takes []string
	for {
		head, take, ok := lastTake(song)
		if !ok {
			break
		}
		song = head
		takes = append([]string{take}, takes...)
	}
	return song, strings.Join(takes, "  ·  ")
}

// lastTake splits a version note off the end of a title, if one is there.
func lastTake(t string) (head, take string, ok bool) {
	var i int
	switch {
	case strings.HasSuffix(t, ")"):
		i = strings.LastIndex(t, "(")
	case strings.HasSuffix(t, "]"):
		i = strings.LastIndex(t, "[")
	default:
		for _, dash := range []string{" - ", " – ", " — "} {
			if j := strings.LastIndex(t, dash); j > 0 && namesTake(t[j+len(dash):]) {
				return strings.TrimSpace(t[:j]), strings.TrimSpace(t[j+len(dash):]), true
			}
		}
		return t, "", false
	}
	if i <= 0 {
		return t, "", false
	}
	inner := strings.TrimSpace(t[i+1 : len(t)-1])
	head = strings.TrimSpace(t[:i])
	if head == "" || !namesTake(inner) {
		return t, "", false
	}
	return head, inner, true
}

// namesTake says whether words name a version of a song rather than being part of its name.
func namesTake(s string) bool {
	l := " " + strings.ToLower(s) + " "
	for _, w := range takeWords {
		if strings.Contains(l, w) {
			return true
		}
	}
	return false
}

var takeWords = []string{
	"mix", "edit", "version", "remaster", " live", "feat.", "feat ", " ft.", "featuring", " with ",
	"radio", " mono", "stereo", "acoustic", " demo", "instrumental", "extended", "deluxe", "bonus",
	"explicit", " clean", " single", "unplugged", "rework", " dub", " cover", "session", "reprise",
	"karaoke", " vip", "bootleg", "mashup", " from ", "soundtrack", "anniversary", "re-recorded",
	"taylor's", "fassung", "aus dem film",
}

// nowInk is what the now-playing page draws its words and marks in: the head (where it plays), the
// song, who plays it, the rest, and lyrics already sung. bare is for a cover's ground, where the
// controls are marks with no buttons under them.
type nowInk struct {
	head, title, soft, faint, past color.Color
	bare                           bool
}

// themeInk is the theme's own colors, for a page with no cover to take its colors from. A function,
// because the theme can change under a running Show.
func themeInk() nowInk {
	return nowInk{head: amber, title: cream, soft: dim, faint: dim, past: lerp(walnut, dim, 0.55)}
}

// coverInk is white over a cover's ground: the song at full strength and the rest let through by the
// ground, so each line is in the cover's color as well as white. The colors are premultiplied, which
// is what the text drawing lays over the ground.
func coverInk() nowInk {
	see := func(a uint8) color.RGBA { return color.RGBA{a, a, a, a} }
	return nowInk{head: see(200), title: white, soft: see(178), faint: see(150), past: see(95), bare: true}
}

var white = color.RGBA{0xff, 0xff, 0xff, 0xff}

// pixel is the color already drawn in the middle of b, for a part laid over the cover's ground that
// has to be the ground's own color made lighter or darker.
func (r *renderer) pixel(b image.Rectangle) color.RGBA {
	p := image.Pt((b.Min.X+b.Max.X)/2, (b.Min.Y+b.Max.Y)/2)
	if !p.In(r.dst.Rect) {
		return walnut
	}
	i := r.dst.PixOffset(p.X, p.Y)
	return color.RGBA{r.dst.Pix[i], r.dst.Pix[i+1], r.dst.Pix[i+2], 255}
}

// groundFor is the page's ground for a cover, made once per cover and panel: each corner of the page
// takes the color of the same corner of the cover, and the colors run into each other across it.
// Each corner is the cover's most colorful part there rather than its mean, which on most covers is
// a brown, and is darkened until white words read on it. The foot is darker still, under the controls.
func (c *coverCache) groundFor(size image.Point) *image.RGBA {
	if c.ground != nil && c.ground.Rect.Size() == size {
		return c.ground
	}
	h := c.img.Bounds().Dx() / 2
	var corner [4]color.RGBA // top left, top right, bottom left, bottom right
	for i := range corner {
		corner[i] = groundTone(vividColor(c.img, image.Rect(i%2*h, i/2*h, i%2*h+h, i/2*h+h)))
	}
	c.ground = cornerGround(size, corner)
	return c.ground
}

// plainGround is the ground for a song with no cover: the theme's color, darkened as a cover's would
// be, strongest top left and running into a darker mix of it with the theme's ground.
type plainGround struct {
	img  *image.RGBA
	tint color.RGBA
}

func (p *plainGround) groundFor(size image.Point, tint color.RGBA) *image.RGBA {
	if p.img != nil && p.img.Rect.Size() == size && p.tint == tint {
		return p.img
	}
	deep := groundTone(lerp(tint, color.RGBA{0x10, 0x10, 0x14, 0xff}, 0.6))
	p.img, p.tint = cornerGround(size, [4]color.RGBA{groundTone(tint), deep, deep, groundTone(lerp(tint, deep, 0.5))}), tint
	return p.img
}

// cornerGround runs four corner colors (top left, top right, bottom left, bottom right) into each
// other across a page of size, darker toward the foot, under the controls.
func cornerGround(size image.Point, corner [4]color.RGBA) *image.RGBA {
	g := image.NewRGBA(image.Rectangle{Max: size})
	for y := 0; y < size.Y; y++ {
		fy := float64(y) / float64(max(size.Y-1, 1))
		left, right := lerp(corner[0], corner[2], fy), lerp(corner[1], corner[3], fy)
		foot := 1 - 0.35*fy*fy
		for x := 0; x < size.X; x++ {
			col := lerp(left, right, float64(x)/float64(max(size.X-1, 1)))
			i := g.PixOffset(x, y)
			g.Pix[i], g.Pix[i+1], g.Pix[i+2], g.Pix[i+3] = uint8(float64(col.R)*foot), uint8(float64(col.G)*foot), uint8(float64(col.B)*foot), 255
		}
	}
	return g
}

// vividColor is the mean of a part of a picture with each pixel counted by how much color it has, so
// a red jacket on a grey wall gives red, and a grey picture still gives its grey.
func vividColor(img *image.RGBA, b image.Rectangle) color.RGBA {
	var rs, gs, bs, n float64
	for y := b.Min.Y; y < b.Max.Y; y += 6 {
		for x := b.Min.X; x < b.Max.X; x += 6 {
			i := img.PixOffset(x, y)
			cr, cg, cb := float64(img.Pix[i]), float64(img.Pix[i+1]), float64(img.Pix[i+2])
			w := 8 + max(cr, cg, cb) - min(cr, cg, cb)
			w *= w
			rs, gs, bs, n = rs+cr*w, gs+cg*w, bs+cb*w, n+w
		}
	}
	if n == 0 {
		return walnut
	}
	return color.RGBA{uint8(rs / n), uint8(gs / n), uint8(bs / n), 255}
}

// groundTone makes a cover's color into ground for white words: no lighter than a dark mid-tone, a
// little more colorful than it was so the darkening does not turn it to mud, and never quite black.
func groundTone(c color.RGBA) color.RGBA {
	r, g, b := float64(c.R), float64(c.G), float64(c.B)
	luma := 0.299*r + 0.587*g + 0.114*b
	k := 1.0
	if luma > 70 {
		k = 70 / luma
	}
	out := func(v float64) uint8 {
		v = (luma + (v-luma)*1.3) * k
		return uint8(min(max(v, 14), 255))
	}
	return color.RGBA{out(r), out(g), out(b), 255}
}

// bareButtons is the row of controls on a cover's ground: white marks with nothing under them, and
// play or pause as a white disc with the mark cut in the ground's color. The tap regions are the same
// rectangles the buttons fill on the theme's page.
func (r *renderer) bareButtons(s scene, fav, back, play, next, stop image.Rectangle) {
	cx := func(b image.Rectangle) float64 { return float64(b.Min.X+b.Max.X) / 2 }
	cy := float64(play.Min.Y+play.Max.Y) / 2
	u := float64(r.s(14))
	under := r.pixel(play)
	rad := float64(play.Dy()) / 2
	r.roundFillF(cx(play)-rad, cy-rad, cx(play)+rad, cy+rad, rad, white)
	if !s.live {
		r.markSkip(cx(back), cy, u, false, white)
		r.markSkip(cx(next), cy, u, true, white)
	}
	m := u * 0.9
	switch {
	case s.live && !s.paused:
		r.roundFillF(cx(play)-m*0.8, cy-m*0.8, cx(play)+m*0.8, cy+m*0.8, m*0.2, under)
	case s.paused:
		r.aaPoly([][2]float64{{cx(play) - m*0.7, cy - m*1.15}, {cx(play) + m*1.1, cy}, {cx(play) - m*0.7, cy + m*1.15}}, under)
	default:
		bw := m * 0.62
		r.roundFillF(cx(play)-m*0.8, cy-m*1.05, cx(play)-m*0.8+bw, cy+m*1.05, bw/3, under)
		r.roundFillF(cx(play)+m*0.8-bw, cy-m*1.05, cx(play)+m*0.8, cy+m*1.05, bw/3, under)
	}
	side := lerp(r.pixel(fav), white, 0.7)
	star := side
	if s.faved {
		star = amber
	}
	r.aaStar(cx(fav), cy, u*1.4, star)
	r.roundFillF(cx(stop)-u*0.75, cy-u*0.75, cx(stop)+u*0.75, cy+u*0.75, u*0.2, lerp(r.pixel(stop), white, 0.7))
}

// averageColor is a picture's mean color, from every eighth pixel each way, which is plenty for a tint.
func averageColor(img *image.RGBA) color.RGBA {
	var rs, gs, bs, n int
	b := img.Bounds()
	for y := b.Min.Y; y < b.Max.Y; y += 8 {
		for x := b.Min.X; x < b.Max.X; x += 8 {
			i := img.PixOffset(x, y)
			if img.Pix[i+3] == 0 {
				continue
			}
			rs, gs, bs, n = rs+int(img.Pix[i]), gs+int(img.Pix[i+1]), bs+int(img.Pix[i+2]), n+1
		}
	}
	if n == 0 {
		return walnut
	}
	return color.RGBA{uint8(rs / n), uint8(gs / n), uint8(bs / n), 255}
}

// markSkip is back or forward: a bar and a triangle pointing away from it, centered on cx, cy, u high
// from the middle.
func (r *renderer) markSkip(cx, cy, u float64, forward bool, c color.RGBA) {
	bw, tw := u*0.38, u*1.25
	w := bw + u*0.15 + tw
	x := cx - w/2
	if forward {
		r.aaPoly([][2]float64{{x, cy - u}, {x + tw, cy}, {x, cy + u}}, c)
		r.roundFillF(x+tw+u*0.15, cy-u, x+w, cy+u, bw/3, c)
		return
	}
	r.roundFillF(x, cy-u, x+bw, cy+u, bw/3, c)
	r.aaPoly([][2]float64{{x + w, cy - u}, {x + w - tw, cy}, {x + w, cy + u}}, c)
}

// aaStar fills a five-pointed star centered at cx, cy with outer radius rad, smooth at the edges.
func (r *renderer) aaStar(cx, cy, rad float64, c color.RGBA) {
	pts := make([][2]float64, 10)
	for i := range pts {
		a := -math.Pi/2 + float64(i)*math.Pi/5
		rr := rad
		if i%2 == 1 {
			rr *= 0.45
		}
		pts[i] = [2]float64{cx + rr*math.Cos(a), cy + rr*math.Sin(a)}
	}
	r.aaPoly(pts, c)
}

// aaPoly fills a polygon, each edge pixel by how many of sixteen samples in it fall inside.
func (r *renderer) aaPoly(pts [][2]float64, c color.RGBA) {
	x0, y0, x1, y1 := math.Inf(1), math.Inf(1), math.Inf(-1), math.Inf(-1)
	for _, p := range pts {
		x0, y0, x1, y1 = min(x0, p[0]), min(y0, p[1]), max(x1, p[0]), max(y1, p[1])
	}
	for y := int(math.Floor(y0)); y <= int(math.Ceil(y1)); y++ {
		for x := int(math.Floor(x0)); x <= int(math.Ceil(x1)); x++ {
			in := 0
			for sy := 0; sy < 4; sy++ {
				for sx := 0; sx < 4; sx++ {
					if inPolygon(float64(x)+(float64(sx)+0.5)/4, float64(y)+(float64(sy)+0.5)/4, pts) {
						in++
					}
				}
			}
			r.blendAt(x, y, c, float64(in)/16)
		}
	}
}

// pressLight lights b, a button just pressed, as a lighter pill over what is drawn there: the finger
// sees the tap land before the music answers it.
func (r *renderer) pressLight(b image.Rectangle) {
	if b.Empty() {
		return
	}
	x0, y0, x1, y1 := float64(b.Min.X), float64(b.Min.Y), float64(b.Max.X), float64(b.Max.Y)
	rad := float64(min(b.Dx(), b.Dy())) / 2
	for y := b.Min.Y; y < b.Max.Y; y++ {
		for x := b.Min.X; x < b.Max.X; x++ {
			r.blendAt(x, y, white, 0.32*clamp01(0.5-rrDist(float64(x)+0.5, float64(y)+0.5, x0, y0, x1, y1, rad)))
		}
	}
}

// roundFillF is a rounded rectangle in one color at fractional coordinates, for marks too small for
// whole pixels to look even.
func (r *renderer) roundFillF(x0, y0, x1, y1, rad float64, c color.RGBA) {
	for y := int(math.Floor(y0)); y <= int(math.Ceil(y1)); y++ {
		for x := int(math.Floor(x0)); x <= int(math.Ceil(x1)); x++ {
			r.blendAt(x, y, c, clamp01(0.5-rrDist(float64(x)+0.5, float64(y)+0.5, x0, y0, x1, y1, rad)))
		}
	}
}

// musicPlace is where Music Assistant plays, for the head of the now-playing page: the group, the one
// room it resolved to, or the speaker it fell back to.
func musicPlace(v home.MusicPlaybackView) string {
	switch {
	case v.Route != "":
		return prettyMusicName(v.Route)
	case len(v.Rooms) == 1:
		return prettyMusicName(v.Rooms[0])
	case v.Output != "":
		return v.Output
	default:
		return "Music Assistant"
	}
}

func prettyMusicName(v string) string {
	v = strings.TrimSpace(strings.ReplaceAll(v, "_", " "))
	if v == "" {
		return v
	}
	r := []rune(v)
	r[0] = unicode.ToUpper(r[0])
	return string(r)
}

// musicProgress is the position as a rounded bar in b, with the time gone and the length over its ends.
// The position moves on from Music Assistant's last report while the song plays, so the time gone
// counts up between reports rather than jumping.
func (r *renderer) musicProgress(v home.MusicPlaybackView, now time.Time, b image.Rectangle, ink nowInk) {
	pos := v.Elapsed(now)
	rad := float64(b.Dy()) / 2
	track, fillC := shift(walnut, 18), amber
	if !dark() {
		track = shift(walnut, -18)
	}
	if ink.bare {
		track, fillC = lerp(r.pixel(b), white, 0.22), white
	}
	r.roundFillF(float64(b.Min.X), float64(b.Min.Y), float64(b.Max.X), float64(b.Max.Y), rad, track)
	if v.Duration > 0 {
		p := min(max(pos/v.Duration, 0), 1)
		fill := float64(b.Min.X) + float64(b.Dx())*p
		if fill > float64(b.Min.X)+2*rad {
			r.roundFillF(float64(b.Min.X), float64(b.Min.Y), fill, float64(b.Max.Y), rad, fillC)
		}
		left := mediaClock(pos)
		right := mediaClock(v.Duration)
		r.text(r.tiny, left, b.Min.X, b.Min.Y-r.s(12), ink.faint)
		r.text(r.tiny, right, b.Max.X-r.width(r.tiny, right), b.Min.Y-r.s(12), ink.faint)
	}
}

func mediaClock(seconds float64) string {
	if seconds < 0 {
		seconds = 0
	}
	n := int(seconds + .5)
	return fmt.Sprintf("%d:%02d", n/60, n%60)
}

// The marks are 36 rows high from x and top at the Show 5's width, drawn as rows the way this panel has
// always drawn its play mark. Sizes go through s() so a wider panel gets marks to match its buttons.

func (r *renderer) markBack(x, top int) {
	r.markTriangle(x+r.s(14), top, r.s(30), false)
	r.markBar(x, top)
}

func (r *renderer) markNext(x, top int) {
	r.markTriangle(x, top, r.s(30), true)
	r.markBar(x+r.s(44), top)
}

func (r *renderer) markPlay(x, top int) { r.markTriangle(x, top, r.s(36), true) }

// markStop is a station's stop: a square as tall as the pause bars' middle.
func (r *renderer) markStop(x, top int) {
	draw.Draw(r.dst, image.Rect(x, top, x+r.s(30), top+r.s(30)), image.NewUniform(amber), image.Point{}, draw.Src)
}

func (r *renderer) markPause(x, top int) {
	r.markBar(x, top)
	r.markBar(x+r.s(19), top)
}

func (r *renderer) markBar(x, top int) {
	mark := image.Rect(x, top, x+r.s(11), top+r.s(36))
	draw.Draw(r.dst, mark, image.NewUniform(amber), image.Point{}, draw.Src)
}

// markTriangle draws a triangle pointing right or left, widest in the middle.
func (r *renderer) markTriangle(x, top, size int, right bool) {
	for i := 0; i < size; i++ {
		half := min(i, size-1-i)
		w := half*3/2 + 1
		x0 := x
		if !right {
			x0 = x + (size/2)*3/2 + 1 - w
		}
		draw.Draw(r.dst, image.Rect(x0, top+i, x0+w, top+i+1), image.NewUniform(amber), image.Point{}, draw.Src)
	}
}
