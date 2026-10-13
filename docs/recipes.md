# Recipes

Ask the assistant "Wie mache ich Hackfleisch-Tacos?" and the recipe comes up on the Show you asked:
ingredients on the left, steps on the right, big enough to read from the stove. The assistant says which
dish it found and on which site, and doesn't read the whole thing out unless you ask.

It is a real recipe from a recipe site, not one the language model makes up. Home Assistant's
`jarvis_recipe` integration searches for it through your SearXNG, opens the first results until one
has a recipe in it, and puts that on a dashboard at `jarvis-rezept`. The Show brings that dashboard
up and keeps it there: it doesn't go back to the clock after 10 minutes. A swipe up brings it back
later ([The recipe page](dashboards.md#the-recipe-page)).

## What you need

- A voice assistant in Home Assistant whose conversation agent is a language model that can use
  tools (OpenAI, Anthropic, Google, Ollama and the like). Home Assistant's own agent without a model
  can't look anything up.
- [SearXNG](https://docs.searxng.org), reachable from Home Assistant, with JSON answers switched on.
  They are off by default; in SearXNG's `settings.yml`:

  ```yaml
  search:
    formats:
      - html
      - json
  ```

  Without that SearXNG answers 403 and the assistant finds nothing.
- For the page on the screen, the Show must be able to stream dashboards, so a
  [dashcast server](dashboards.md#setting-up-streamed). Without one the assistant still finds the recipe and
  can tell you what is in it, it just can't show it.

## Installing

1. Copy `ha/custom_components/jarvis_recipe/` to `/config/custom_components/jarvis_recipe/` on Home
   Assistant (the File editor or Samba add-on will do), and restart Home Assistant.
2. **Settings → Devices & services → Add integration → Genbu Rezepte.** It asks for SearXNG's
   address, for example `http://10.0.0.5:8080`. This adds `sensor.jarvis_recipe`, which holds the
   recipe last looked up.
3. **Settings → Voice assistants**, open your assistant, then the conversation agent's options
   (the cog next to it). Where it lets the assistant control Home Assistant, tick **Genbu Rezepte** as
   well as **Assist**, and save.
4. Make sure Genbu is 1.3.1 or newer: it brings the recipe card along with the others
   ([ha-dashboard](../ha-dashboard/README.md)).
5. Make the page. The installer makes it with the Jarvis dashboard (`--jarvis-dashboard`,
   [jarvis-crown-installer.md](jarvis-crown-installer.md)); by hand, it is
   **Settings → Dashboards → Add dashboard → New dashboard from scratch.** Call it "Rezept", and
   set its URL to `jarvis-rezept`: the Show finds the page by that address. Open it, **⋮ → Edit
   dashboard → ⋮ → Raw configuration editor**, and paste
   [`jarvis-recipe-dashboard.yaml`](../ha-dashboard/jarvis-recipe-dashboard.yaml).

The Show notices the new dashboard within five minutes. Until then the swipe up does nothing.

## Which Show it comes up on

The one you asked. Home Assistant tells the integration which device the question came from, and
the integration calls `esphome.<node>_recipe_show` on the Show with that name
([actions.md](actions.md#show-the-recipe)).

Typed in the Home Assistant app instead, it comes up on your Show if you have only one. With more
than one it stays on the dashboard and no Show is told; swipe up on the one in the kitchen. Asked
from a voice device that isn't a Show, no Show is told either.

## What it opens on the web, and what it doesn't

The search results are pages from strangers, so the integration is careful with them:

- It tries the first six results for "<dish> Rezept", no more.
- It only reads the recipe data recipe sites put in their pages for search engines
  ([schema.org Recipe](https://developers.google.com/search/docs/appearance/structured-data/recipe)).
  Nothing on the page runs, and a page without that data is skipped.
- Only pages on the internet. A result that points at an address in your own network (the router,
  a NAS, Home Assistant itself, 192.168.x.x, localhost) is skipped, and so is one that redirects
  there: it follows at most three redirects and checks each. A site whose name gives one address
  when checked and another a moment later can still get through; that is a limit of looking the
  name up before connecting.
- Only plain `http` and `https` addresses, no user name or password in them, HTML only, at most
  2 MB of a page, and 8 seconds for each request.
- What it keeps is the title, the picture's address, servings, time, up to 50 ingredients and 40
  steps, the site's name and the page's address. Nothing else from the page.

The recipe stays out of Home Assistant's history; only the dish's name is recorded. After a restart
the last recipe is still there.

The card loads the recipe's picture from the recipe site, so the Show (or the dashcast server)
asks that site for it. It sends no referrer.

## When it doesn't work

- **The assistant says it can't look up recipes, or makes one up.** The conversation agent doesn't have
  **Genbu Rezepte** ticked (step 3), or the model ignored the tool; try asking again with
  "Such mir ein Rezept für …".
- **"Kein Rezept gefunden".** SearXNG didn't answer or answered without results. Open
  `http://<searxng>/search?q=Pfannkuchen+Rezept&format=json` in a browser: a 403 means JSON answers
  are off. The assistant is also told which sites it tried; ask it, which helps when none of them carried
  recipe data.
- **The assistant found it, but the Show stays on the clock.** The Show has no `jarvis-rezept` dashboard
  yet (step 5, then up to five minutes), or no dashcast server, or the question came from a device
  that isn't a Show.
- **The page says "sensor.jarvis_recipe fehlt".** The integration isn't set up (step 2).

For more detail in Home Assistant's log:

```yaml
logger:
  logs:
    custom_components.jarvis_recipe: debug
```

## Status

The recipe reading and the choice of Show have tests (`tests/test_jarvis_recipe.py`), and the card
was checked in a browser at 960×480 and 1280×800. The integration hasn't been run in a live Home
Assistant yet.
