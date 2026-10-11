# Voice: what to say to test a Show

Say these to a Show after an install, an OTA or a change on the Home Assistant side. They are German
because the Shows are set up in German. Each line says what should happen and, if it doesn't, where
to look. The Home Assistant side of each one is described in [voice-setup.md](voice-setup.md).

Some of these start music or change volumes in the room, so say them when that is all right.

Say the wake word before each one. Okay Nabu is the one to use; see [Wake word](voice-setup.md#wake-word).

## Answered by Home Assistant itself

These come back in well under a second once Whisper is done. If one takes several seconds, it went to
the model, so the automation is missing, not reloaded, or its sentence doesn't match what Whisper
wrote (the assistant's debug view in Home Assistant shows what it heard).

| Say | Should happen |
| --- | --- |
| "Wie spät ist es?" | The time. |
| "Wo sind wir?" | The room the Show is in. "Ich bin keinem Raum zugeordnet" means the Show's device has no area. |
| "Wie warm ist es hier?" | The room's temperature, with the room's name. |
| "Wie warm ist es im Bad?" | The bathroom's temperature (any area with a temperature sensor works). |
| "Wie kalt ist es draußen?" | The outside temperature. |
| "Wie wird das Wetter morgen?" | Tomorrow's forecast from the weather entity, not a made-up one. |
| "Stell einen Wecker für 2 Minuten." | "Wecker klingelt in 2 Minuten, um …", "Wecker HH:MM" shows under the clock, and it rings then. |
| "Spiel Musik." / "Mach Musik an." | A random song from the library starts on the room's speaker, then "Musik läuft." |

## Volume

Start some music on the room's speaker first.

| Say | Should happen |
| --- | --- |
| "Lautstärke auf 30." | The music goes to 30 %, the Show's own volume stays. |
| "Lautstärke runter." | The music goes down a step. |
| "Deine Lautstärke auf 30." | The Show itself goes to 30 %, the music stays. |
| "Sei leiser." | The Show goes down ten points. |
| "Lautstärke im Bad auf 20." with nothing playing there | It says no music is playing in that room, and nothing changes. |

## Music in the room

| Do | Should happen |
| --- | --- |
| Say the wake word over the music. | The music drops to about a tenth while the Show listens and answers, and comes back three seconds after. |
| Say "lauter" during the answer. | The music comes back one step louder than it was before, not one step above the ducked level. |
| Say the wake word too quietly, then again normally. | The second try wakes it. The log shows a `wake near miss` for the first. |

## Through the model

These take a few seconds. The answer should be short, in "du", with no question at the end.

| Say | Should happen |
| --- | --- |
| "Spiel was von Queen." | Queen starts on the room's speaker, the now-playing page comes up. |
| "Spiel Hit Radio FFH." | The station plays. |
| "Was ist ein Taco?" | A short answer, nothing switched. |

## On the Show

| Do | Should happen |
| --- | --- |
| Tap the books button on the now-playing cover. | Music Assistant's library comes up, without its settings. |
| Tap the tab on the left edge, or swipe back. | Back to the now-playing page. |
| When an answer ends in a question and the Show listens again, say "Stopp". | The conversation ends on the Show and nothing reaches Home Assistant. |

## Known to go wrong for now

These are open jobs, listed so a failure here isn't taken for a new bug.

- A station name with extra words in front, like "Biele Hit Radio FFH": Music Assistant can't find
  it, and the model offers random music instead.
- A mood, like "Spiel was Flottes": the radio page comes up and nothing plays.
- "Spiel Musik" doesn't say what it plays.
