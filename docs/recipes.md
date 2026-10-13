# Recipes

Ask the assistant "Wie mache ich Hackfleisch-Tacos?" and the recipe comes up on the Show you asked:
ingredients on the left, steps on the right, big enough to read from the stove. The assistant says which
dish it found and on which site, and doesn't read the whole thing out unless you ask.

It is a real recipe from a recipe site, not one the language model makes up. Genbu Assistant
searches for it through your SearXNG, opens the first results until one has a recipe in it, and puts
that on a dashboard at `jarvis-rezept`. Ask in English and it searches in English. The Show brings that dashboard
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

Recipes are part of Genbu Assistant from 1.4.0. There is no separate integration and nothing to tick in
the conversation agent: every agent that can control Home Assistant (the **Assist** tick) gets the recipe
tool, `genbu__recipe`, but only for a question asked at a Genbu Show.

1. **Settings → Devices & services → Genbu Assistant → Configure.** Put SearXNG's address under
   **Recipe search**, for example `http://10.0.0.5:8080`. This adds `sensor.genbu_recipe`, which holds
   the recipe last looked up, and a **Recipes** switch on each Show.
2. **Recipes** is on for every Show whose firmware has the recipe page. Turn it off on a Show that
   never cooks, in the Genbu Control Center or on the Show's Genbu device; that Show's assistant then
   doesn't get the tool at all.
3. Make the page. The installer makes it with the Jarvis dashboard (`--jarvis-dashboard`,
   [jarvis-crown-installer.md](jarvis-crown-installer.md)); by hand, it is
   **Settings → Dashboards → Add dashboard → New dashboard from scratch.** Call it "Rezept", and
   set its URL to `jarvis-rezept`: the Show finds the page by that address. Open it, **⋮ → Edit
   dashboard → ⋮ → Raw configuration editor**, and paste
   [`jarvis-recipe-dashboard.yaml`](../ha-dashboard/jarvis-recipe-dashboard.yaml).

The Show notices the new dashboard within five minutes. Until then the swipe up does nothing.

### Coming from Genbu Rezepte

Before 1.4.0 this was a separate integration, Genbu Rezepte (`jarvis_recipe`). Genbu Assistant takes its
SearXNG address over when it starts, and Genbu Rezepte 1.1.0 then stands back, so the assistant never
gets two recipe tools. A note in **Settings → Repairs** says so. Untick **Genbu Rezepte** in the
conversation agent's options and remove the integration when it suits you; Genbu Rezepte is dropped in
the release after. The recipe page made for `sensor.jarvis_recipe` keeps working: the card shows the
newer of the two sensors.

## Which Show it comes up on

The one you asked. Home Assistant tells Genbu Assistant which device the question came from, and it
calls `esphome.<node>_recipe_show` on that Show ([actions.md](actions.md#show-the-recipe)).

Asked anywhere else (a phone, a Voice PE, the chat in the Home Assistant app), the assistant has no
recipe tool. Recipes are for the screen in the kitchen, and an agent that never cooks keeps a shorter
tool list.

## What it opens on the web, and what it doesn't

The search results are pages from strangers, so Genbu Assistant is careful with them:

- It tries the first four results for "<dish> Rezept" ("<dish> recipe" in English), no more, and one
  Show looks up one recipe at a time.
- It only reads the recipe data recipe sites put in their pages for search engines
  ([schema.org Recipe](https://developers.google.com/search/docs/appearance/structured-data/recipe)).
  Nothing on the page runs, and a page without that data is skipped.
- Only pages on the internet. A result that points at an address in your own network (the router,
  a NAS, Home Assistant itself, 192.168.x.x, localhost) is skipped, and so is one that redirects
  there: it follows at most three redirects and checks each. The check is the lookup the connection
  uses, so a name that answers with a public address first and a home one a moment later gets
  nowhere.
- Only plain `http` and `https` addresses, no user name or password in them, HTML only, at most
  2 MB of a page, and 8 seconds for each request.
- What it keeps is the title, the picture's address, servings, time, up to 50 ingredients and 40
  steps, the site's name and the page's address. Nothing else from the page.

The assistant itself gets less than the screen: the dish, the site, servings, time and how many
ingredients and steps. The same assistant may unlock doors, so a stranger's page shouldn't talk to it.
Only when you ask it to read the recipe out does it get the ingredients and up to 12 steps, as plain
lines of at most 200 characters with the links taken out, marked as the page's text and not something
to do.

The recipe stays out of Home Assistant's history; only the dish's name is recorded. After a restart
the last recipe is still there.

The card loads the recipe's picture from the recipe site, so the Show (or the dashcast server)
asks that site for it. It sends no referrer.

## When it doesn't work

- **The assistant says it can't look up recipes, or makes one up.** No recipe search in Genbu
  Assistant's options (step 1), **Recipes** is off on that Show (step 2), the agent doesn't have
  **Assist** ticked, or the model ignored the tool; try asking again with "Such mir ein Rezept für …".
- **"Kein Rezept gefunden".** SearXNG didn't answer or answered without results. Open
  `http://<searxng>/search?q=Pfannkuchen+Rezept&format=json` in a browser: a 403 means JSON answers
  are off. The assistant is also told which sites it tried; ask it, which helps when none of them carried
  recipe data.
- **The assistant found it, but the Show stays on the clock.** The Show has no `jarvis-rezept` dashboard
  yet (step 5, then up to five minutes), or no dashcast server, or the question came from a device
  that isn't a Show.
- **The page says "sensor.genbu_recipe fehlt".** No recipe search is set up (step 1).

For more detail in Home Assistant's log:

```yaml
logger:
  logs:
    custom_components.genbu.llm: debug
```

## Status

The recipe reading, the lines for the assistant and the choice of Show have tests
(`tests/test_genbu_recipe.py`), and who gets the tool is tested in Home Assistant
(`ha/tests/test_genbu_recipe_tool.py`). The card was checked in a browser at 960×480 and 1280×800.
