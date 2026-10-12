# go-esphome-device, OpenJade's copy

A copy of github.com/ygelfand/go-esphome-device v0.0.4 (MIT, LICENSE), used by echod through a
`replace` in echod/go.mod. The changes:

- `Info` has `ProjectName` and `ProjectVersion`, and the device info answer sends them, so Home
  Assistant can show what firmware runs on the device.
- `MediaPlayerTurnOn` and `MediaPlayerTurnOff` name the turn on/off commands the protocol already
  has, so a speaker can take "turn the music in the bedroom off".
