package config

import "time"

// Update is which releases the device follows. The channel is a name rather than a URL: where each
// one points is compiled in, so this cannot be used to send the device somewhere else for its next
// binary.
type Update struct {
	Channel string `json:"channel"`

	// LastVersion is the build Home Assistant was last told about. It moves only once the telling has
	// happened, so a version that changed while nothing was listening is still reported later.
	LastVersion string `json:"last_version"`

	// AutoInstall installs an update the channel offers by itself, overnight while nothing is playing,
	// for a device nobody is going to press Install on: one with no Home Assistant, far away.
	AutoInstall bool `json:"auto_install,omitempty"`

	// Told is the last update the device said out loud was ready, and ToldAt when it last said so: it
	// says it again every few hours until the update is installed.
	Told   string    `json:"told,omitempty"`
	ToldAt time.Time `json:"told_at,omitzero"`

	// Following the house's version (docs/update-leader.md). HouseSeen is the house version last seen
	// while following, empty for a Show that has never followed one. HouseAsked and HouseAskedAt are the
	// last time it asked whether to move to the house version after being off. HouseFailed is a house
	// version that would not install here or was rolled back, which it does not try again by itself.
	HouseSeen    string    `json:"house_seen,omitempty"`
	HouseAsked   string    `json:"house_asked,omitempty"`
	HouseAskedAt time.Time `json:"house_asked_at,omitzero"`
	HouseFailed  string    `json:"house_failed,omitempty"`
}

const DefaultChannel = "stable"

func defaultUpdate() Update {
	return Update{Channel: DefaultChannel}
}

type UpdateWriter struct{ st *Store }

func (w UpdateWriter) Channel(v string) error {
	return w.st.Update(func(c *Config) { c.Update.Channel = v })
}

func (w UpdateWriter) AutoInstall(v bool) error {
	return w.st.Update(func(c *Config) { c.Update.AutoInstall = v })
}

func (w UpdateWriter) Told(v string, at time.Time) error {
	return w.st.Update(func(c *Config) { c.Update.Told, c.Update.ToldAt = v, at })
}

func (w UpdateWriter) LastVersion(v string) error {
	return w.st.Update(func(c *Config) { c.Update.LastVersion = v })
}

func (w UpdateWriter) HouseSeen(v string) error {
	return w.st.Update(func(c *Config) { c.Update.HouseSeen = v })
}

func (w UpdateWriter) HouseAsked(v string, at time.Time) error {
	return w.st.Update(func(c *Config) { c.Update.HouseAsked, c.Update.HouseAskedAt = v, at })
}

func (w UpdateWriter) HouseFailed(v string) error {
	return w.st.Update(func(c *Config) { c.Update.HouseFailed = v })
}
