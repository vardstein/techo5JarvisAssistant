# Lyrics on the now-playing page

When Music Assistant plays a song it has words for, the now-playing page gets a button with lines of
text on it, left of the clock. Tapping it swaps the song's details for its words; tapping again brings
them back. Words with times in them follow the song: the line being sung is lit, the one before stays
above it while it is short, and the page moves on the moment the next line starts. Words without times
move down through the song as it plays.

## Where the words come from

The Show asks Music Assistant, the same server Sendspin plays from. Music Assistant answers with what it
knows about the song:

- words already in its library, read from the files' tags (`LYRICS`/`USLT`, with or without LRC times);
- for songs from an OpenSubsonic server such as Navidrome, the server's own answer to
  `getLyricsBySongId`, which also comes from the files' tags;
- if online metadata is on in Music Assistant, whatever its lyrics providers (LRCLIB) find.

With `--music-assistant-local-metadata` the last one is off and nothing about the song leaves the house.

Radio and streams without a song in Music Assistant's library show the page as before, with no button.

## Why not through Home Assistant

Home Assistant's Music Assistant integration tells the Show what is playing but not the words. Asking
Music Assistant directly needs no YAML in Home Assistant and no restart, so the installer can set it up
in one go.

## The Show's own user

The Show does not get your Music Assistant login. The installer asks for an admin's token (Music
Assistant → Settings → your profile → long-lived tokens). Make one for the occasion, paste it when
asked, and delete it in Music Assistant afterwards; the Show's own token does not depend on it. For
runs without a terminal, `--music-assistant-token-file` or the keyring entry `application jarvis-show
secret music-assistant-token` work too. Pressing Enter at the question skips lyrics.

With it, the installer:

1. makes a plain (not admin) Music Assistant user named after the Show, e.g. `jarvis-show-5`, with a
   random password that is thrown away at once, so nobody can sign in with it;
2. makes a long-lived token for that user, named "Jarvis Show", and revokes the one it made last time;
3. hands that token to the Show through the `esphome.<show>_music_assistant` action.

The admin token is used for that and then dropped; the installer writes it nowhere. Running
`python3 tools/jarvis-show.py home-assistant --name NAME` again keeps the Show's token as long as it
still works, so the player doesn't drop. It only makes a new one when the Show has none, when Music
Assistant turned it away, or when it ends within a month (Music Assistant's tokens last a year). To
tell which token it holds, the Show names it only by a hash of the token's id, and only over the
encrypted API; the token itself never leaves the Show. Any other "Jarvis Show" token of that user is
revoked. To take lyrics away, delete the user in Music Assistant, or call the action with an empty
token.

The Show's diagnostic sensor "Music Assistant key" says how its token stands: `ok`, `none` (no token
yet), or `gone` (Music Assistant answered 401; the Show stops sending it until the installer runs).

On the Show the token sits in `music-assistant.json` next to the API key, readable by root only.

## Separate networks

The Show talks to Music Assistant on TCP port 8095. On a flat home network that just works. If the Show
sits on its own network (an IoT VLAN, say) and only reaches Home Assistant on 443, allow the Show to
reach Music Assistant's address on TCP 8095 as well. Sendspin itself runs the other way (Music
Assistant connects to the Show) and does not need this.

Without that rule everything else still plays; the Show logs why it got no words and tries again ten
minutes later.

## Checking on the Show

Play a song with words in its tags. Within a few seconds of the song starting, the button appears;
echod logs `lyrics song=… lines=… synced=true`. A failed fetch logs the reason once and rests ten
minutes. HTTP 401 means the token is gone: the "Music Assistant key" sensor reads `gone`, and the Show
waits for the installer to give it a new one; a timeout usually means the
network rule above is missing.
