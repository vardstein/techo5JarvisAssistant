# The assistant chain

Everything a Genbu Show is asked goes through one place: the Home Assistant assistant (Assist
pipeline) its **Assistant** select names, "preferred" unless someone changed it. That assistant is
the one source of truth for speech, the model and the voice. The Show and the pieces the installer
puts into Home Assistant only ever talk to it, or answer the device that asked. Nothing is tied to a
particular house, Show or assistant name, so the same installer works with someone else's setup,
Home Assistant Cloud included.

This page is the list we checked that against (GENBU-110), and what changed.

## How a request travels

1. The Show hears its wake word and streams what follows to Home Assistant as an ESPHome voice
   satellite.
2. The assistant's speech to text turns it into a sentence.
3. Home Assistant tries the installer's sentence triggers first. It does that before the
   conversation agent, whatever agent the assistant uses, and with or without "Prefer handling
   commands locally" (`assist_pipeline/default_pipeline.py`, checked on 2026.10).
4. With "Prefer handling commands locally" on, Home Assistant's own intents come next ("Licht an",
   timers).
5. Everything else goes to the conversation agent, with the scripts that are exposed to Assist and
   the Genbu Rezepte tool, when the agent can use tools.
6. Text to speech, and the answer goes back to the device that asked.

## What the installer puts in, and how it knows who asked

| Piece | Kind | Which device or room |
| --- | --- | --- |
| Genbu - Where are we, Room temperature, Wecker, Local Music | sentence triggers | `trigger.device_id`: the area of the device that asked, or that device itself |
| Genbu - Local Music Stop | sentence triggers | `trigger.device_id`, or the room said; there it also pauses the music player of each Show in that room, which often has no room itself |
| Genbu - Forecast | sentence trigger | none needed, the house's weather |
| Genbu - Volume | sentence trigger, blueprint `volume-where-the-music-is` | `trigger.device_id`, or the room said |
| `play_random_music`, `jarvis_play_radio`, `jarvis_play_mood` | scripts the agent may call | the player the agent names, else the one satellite that is busy, else the house's players in order |
| Genbu Musik starten (`jarvis_music_start`) | helper script | whatever the scripts above hand it; not for Assist |
| *Show*: turn the room down while it listens | blueprint `turn-room-down`, one per Show | that Show's own satellite, on purpose: it is about the room that Show stands in |
| Genbu Rezepte | tool for the agent (LLM API) | `llm_context.device_id`, the device that asked |
| Genbu (the `genbu` integration) | device and music routing | per Show, by its device |

None of them names an assistant, a pipeline or a conversation agent. Renaming the assistant
(GENBU-109) or pointing it at another agent changes nothing here.

## The one way around the assistant

The Show's own setup page has a **Brain** setting. The default is Home Assistant. "Automatic" and
"Direct" let the Show reach speech to text, a model and text to speech by itself when Home Assistant
can't be reached. That is a fallback someone has to switch on, so it stays as it is.

## What changed

- The automations and scripts the installer makes are called "Genbu - ..." and "Genbu Musik
  starten" instead of "Jarvis - ...", the name of our own assistant. The recipe tool shows as
  "Genbu Rezepte".
- The ids stay as they are (`jarvis_local_*`, `jarvis_play_radio`, `jarvis_recipe` and so on).
  Exposure settings, agents that have the recipe tool ticked, and anything a house built on them
  refer to those ids, so changing them would break them.
- An item's name no longer counts when the installer compares what is there with what it would make.
  A house keeps the names it has, the old ones included, and running the installer again asks
  nothing new.

### Gains, losses, breaks

- **Gains:** a new house gets pieces named after the product, not after our assistant, and the voice
  extras work with any agent.
- **Losses:** none we found. Everything that answered before answers the same way.
- **Breaks:** none on a house that already has them. Two things look different:
  - The recipe sensor's name becomes "Genbu Rezept", unless someone renamed it by hand.
  - A recipe entry that is already set up keeps the title it was given.

  A changed name no longer marks an automation as different. So a house that renamed one of them by
  hand is no longer asked about it for that reason alone.
