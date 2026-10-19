# Wake word over music (GENBU-160)

With music at medium volume, a Show 5 doesn't hear "Okay Nabu" from across a narrow room. There are two
cases, and they need different fixes:

1. **The Show plays the music itself.** It has the music as a reference (the loopback channels), so the
   echo canceller can take it out of the microphones.
2. **Another speaker in the room plays it** (a TV, a Bluetooth box, another Show). There is no reference.
   The canceller can't help at all, and whatever helps has to work from the microphones alone.

## What's measured

A 4 s sweep through the daemon's own canceller, on both boards (2026-10-14, numbers also in wiki 731):

| | Show 5 | Show 8 (crown) |
|---|---|---|
| Echo lag | 2.1 ms | 2.4 ms |
| Canceller best / at the end of the sweep | 26.0 / 16.3 dB | 32.1 / 19.4 dB |

The reference is there and lines up, so the echo path isn't broken. But the canceller loses 10 dB or more
by the end of the sweep. The leveler then raises what's left (it starts at +20 dB) before the wake
detector hears it. That's a likely cause of case 1, but it's a guess until a real music-plus-voice take
shows it.

## How to take and replay a measurement

On the Show, with the daemon held (`touch /run/techo5/hold; kill -TERM $(pidof techo5)`), and a 48 kHz
wav copied to `/tmp/music48.wav`:

    sh /tmp/cap.sh 90 4 kitchen 12     # Show 5: 90 s, 4 channels, a "SAY IT" every 12 s
    sh /tmp/cap.sh 90 6 living 12      # Show 8: 6 channels

`cap.sh` lives in `~/.cache/jarvis-show/genbu160/` on the laptop. It plays the file with `techo5 tools media
/tmp/music48.wav`, which needs a build with commit 65c35953. Older builds only take http urls; `cap.sh.bak`
next to it serves the file with busybox nc instead. The script writes `/tmp/cap_<tag>.s24` and
`/tmp/session_<tag>.log`, in the same format as micsession.sh on the Dot.

For case 2, run the same thing with the Show silent (no `tools media`) and the music on the other speaker.

On the laptop, put the take and its session log (renamed to `session.log`) in a folder of their own, then:

    cd echod/internal/hardware/mic
    TECHO5_MICBENCH=<folder> TECHO5_MODELS=../../../../inputs/models go test -run TestReplayWakeScores -v .

Add `TECHO5_BOARD=crown` for a Show 8 take. The test prints hits and false wakes at three thresholds, for
the center mic and the average of all mics. `TECHO5_LEVEL=off`, `TECHO5_GAIN`, `TECHO5_POSTGAIN` and
`TECHO5_AEC=webrtc` change the path, so one take can answer "is it the leveler?" and "would WebRTC's
canceller do better?" without going back to the device.

## Case 2: music from another speaker

Options, from cheapest to most work:

- **Measure first.** The model may cope better than expected: microWakeWord's training notebook already
  mixes [Free Music Archive tracks and AudioSet](https://github.com/OHF-Voice/micro-wake-word/blob/main/notebooks/basic_training_notebook.ipynb)
  in behind the wake word at −5 to 10 dB SNR. If the problem is the leveler raising the music, not the
  model, a leveler that won't add gain over a loud, steady floor fixes it for free.
- **Use the microphones' direction.** The Show 8 has four microphones, so a beamformer could favour the
  talker over a speaker standing somewhere else. The Show 5's two give very little to steer with. The
  delay-and-sum code in `beam.go` was written for the Dot's ring of seven and would need its geometry for
  the Shows. That's work, and the Show 8 is the only board where it's likely to pay off.
- **A noise suppressor in front of the detector.** Speech enhancement before wake detection [helps in
  music and TV noise](https://arxiv.org/abs/2101.12732), most when the two are trained together. An
  off-the-shelf one like [RNNoise](https://www.raspberrypi.com/news/real-time-ml-audio-noise-suppression-on-raspberry-pi-pico-2/)
  is small enough for the Shows, but it was trained on noise, not music. It might smear the wake word as
  much as the music, and only a replay of a real take can tell. WebRTC's suppressor (`--ns` on the
  helper) targets steady noise and probably does little against music. That's a guess too.
- **Train with more music.** Mixing music and TV audio into training cut false rejects by
  [30–45% under playback](https://arxiv.org/abs/1808.00563) in one study. It's a model change, so it's
  upstream's to make, or ours if we ever train our own model.

What decides it is a case 2 take. Until then, none of these is worth building.

## Security

Anything that listens through music can also be reached through music:

- **A TV or a stream can say the wake word.** Amazon keeps a cache of
  [audio fingerprints](https://amazon.science/blog/why-alexa-wont-wake-up-when-she-hears-her-name-in-amazons-super-bowl-ad)
  for known broadcasts and still [tells advertisers](https://developer.amazon.com/en-US/alexa/branding/alexa-guidelines/fingerprinting)
  it can't promise the device stays asleep. For us, a wake from the TV costs one wrong turn, as long as
  the assistant asks before anything that matters. The Genbu agent does that for updates today. Locks
  and alarms depend on what Home Assistant exposes to Assist, so that's worth checking on its own.
- **Commands can be hidden in songs.** [CommanderSong](https://www.usenix.org/conference/usenixsecurity18/presentation/yuan-xuejing)
  (USENIX Security 2018) hid commands in music that a speech recogniser understood and listeners didn't
  notice. A front end that is better at pulling speech out of music makes this a little easier, not
  harder. The defence is the same as above: confirmation before anything that matters.
- **A new model is new parser surface.** If a neural suppressor comes in, its weights ship inside the
  signed firmware and are never downloaded on their own. ONNX has had recent
  [model-file bugs](https://www.wiz.io/vulnerability-database/cve/cve-2026-27489) (file reads through a
  crafted model), and a format we can't read the code for doesn't belong on the Show.
- **Raising the threshold during music is a deafness switch.** Anyone who can play music can then keep
  the Show from hearing its wake word. That's acceptable in a home, but the Show should log when it does
  this, so "it didn't hear me" can be traced.
