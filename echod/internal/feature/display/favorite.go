package display

import "github.com/HuskerMinion/techo5/echod/internal/feature/home"

// favKeyOf is what a press of the star is kept against: the song, or the station without one.
func favKeyOf(rd home.Radio) string {
	if rd.Now == "" && rd.Title == "" {
		return ""
	}
	return rd.Title + "\x00" + rd.Now
}

// favPress is the last press of the star: what it was for, and whether that made it a favorite. The
// lists the star reads come a poll behind, so the press is what the star shows until they agree.
type favPress struct {
	key string
	on  bool
}

// shown is the star for key, given what the lists say: the press while it stands, which ends once the
// lists agree or something else is playing.
func (p *favPress) shown(key string, listed bool) bool {
	if p.key == "" {
		return listed
	}
	if p.key != key || p.on == listed {
		*p = favPress{}
		return listed
	}
	return p.on
}
