package update

// Channel is which stream of releases a device follows. The URLs are compiled in rather than
// configured, so Home Assistant can move a device between our own streams but cannot aim it at
// somebody else's binary — the device runs this as root, and a text field would be a way to replace it.
type Channel int

const (
	// Stable is what a device follows unless somebody says otherwise. GitHub's "latest" excludes
	// prereleases, so a stable device never sees a dev build.
	Stable Channel = iota

	// Staging is the release candidate stream: what is about to become stable, for a device that
	// should see it a little early. CI publishes it from the staging branch.
	Staging

	// Dev is every build of the dev branch, for a device being worked on.
	Dev
)

// Staging and dev are rolling GitHub releases whose manifest CI replaces. Their tags are not the
// branch names, because a tag and a branch both called dev would make the name ambiguous to git.
const (
	stagingTag = "channel-staging"
	devTag     = "channel-dev"
)

// Label is what the setting is called in Home Assistant, and what is stored.
func (c Channel) Label() string {
	switch c {
	case Staging:
		return "staging"
	case Dev:
		return "dev"
	}
	return "stable"
}

// URL is where that channel's manifest.json lives, the one crown and checkers read.
//
// All of them are plain asset downloads rather than api.github.com: the REST API allows sixty unauthenticated
// requests an hour per address, which several devices behind one router would share with everything else
// on the network. Asset paths do not count against it.
func (c Channel) URL() string { return c.URLFor("") }

// URLFor is where that channel keeps the manifest a board reads (ManifestName).
func (c Channel) URLFor(board string) string { return c.at(ManifestName(board)) }

// at is where that channel keeps the manifest called name.
func (c Channel) at(name string) string {
	switch c {
	case Staging:
		return releases + "/download/" + stagingTag + "/" + name
	case Dev:
		return releases + "/download/" + devTag + "/" + name
	}
	return releases + "/latest/download/" + name
}

// Channels is every channel, in the order Home Assistant should offer them.
func Channels() []Channel { return []Channel{Stable, Staging, Dev} }
