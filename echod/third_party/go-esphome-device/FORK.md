# go-esphome-device, OpenJade's copy

A copy of github.com/ygelfand/go-esphome-device v0.0.4 (MIT, LICENSE), used by echod through a
`replace` in echod/go.mod. The changes:

- `Info` has `ProjectName` and `ProjectVersion`, and the device info answer sends them, so Home
  Assistant can show what firmware runs on the device.
- `MediaPlayerTurnOn` and `MediaPlayerTurnOff` name the turn on/off commands the protocol already
  has, so a speaker can take "turn the music in the bedroom off".
- A device holding the all-zeros key (`Unprovisioned()`) is waiting for its first key. It takes
  plaintext, so Home Assistant can add it, and zero-key Noise, so the real key never crosses the
  network readable. It says `api_encryption_provisionable` in its device info, and answers only
  hello, device info, ping, disconnect, an empty entity list and the key, which it takes over Noise
  only. Nothing reaches the handler until it has a key.
