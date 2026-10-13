"""Names the genbu parts share."""

DOMAIN = "genbu"
MANUFACTURERS = {"OpenJade"}  # what echod reports for a Genbu Show over ESPHome
HOUSE_ENTITY = "sensor.genbu_house_version"
# The house's own controls (GENBU-119): its update card, the channel it reads, and the night install.
UPDATE_ENTITY = "update.genbu_show_firmware"
CHANNEL_SELECT = "select.genbu_release_channel"
NIGHT_SWITCH = "switch.genbu_install_at_night"
NIGHT_AT = (3, 0)  # when a waiting update installs by itself, local time, if Install at night is on
CHANGED = f"{DOMAIN}_changed"  # dispatcher signal: the house was looked at again
STORE_KEY = f"{DOMAIN}.house"
STORE_VERSION = 1
SERVICE_FORGET = "forget_show"  # the installer's --remove-show, once the Show is out of ESPHome
SERVICE_INSTALL = "install_updates"  # "yes" on a Show, or the voice kit: the house installs its update
SERVICE_MUSIC_ROUTE = "music_route"  # the voice kit: where music asked for on a Show plays (GENBU-161)
SERVICE_SHOW_KEY = "show_key"  # admins only: a Show gets a new Home Assistant key (GENBU-196)
NAME = "Genbu Assistant"  # the integration, and its one config entry (GENBU-120)
OLD_NAME = "Genbu"  # the entry's title before 1.4.0
PANEL_TITLE = "Genbu Control Center"  # the sidebar page's title (GENBU-121)
CONF_SEARXNG = "searxng_url"  # the recipe search; without it nobody gets genbu__recipe (GENBU-120 §4)
RECIPE_ENTITY = "sensor.genbu_recipe"  # the recipe last found, which the recipe page shows
NEW_RECIPE = f"{DOMAIN}_recipe"  # dispatcher signal: a recipe was found
SHOW_RECIPE = "recipe_show"  # the Show's ESPHome action that puts the recipe page up
OLD_RECIPES = "jarvis_recipe"  # Genbu Rezepte, whose recipe search moved here in 1.4.0
# The per-Show switches the assistant's tools ask before they are offered, by key in House.switches.
RECIPES = "recipes"
VOICE_UPDATES = "voice_updates"
PANEL = "genbu"  # the sidebar page with a card per Show, at /genbu
PANEL_FILES = "/genbu_panel"  # where its script is served from (frontend/), and the dashboard cards
# The dashboard's cards (ha-dashboard/), in frontend/ too. Genbu loads them on every page, so a dashboard
# that uses them needs no resource and nothing copied to /config/www.
CARDS = ("jarvis-calendar-card.js", "jarvis-live-timer-card.js", "jarvis-recipe-card.js",
         "jarvis-room-environment-card.js", "jarvis-weather-card.js")
# Loaded with the cards: reloads a page whose cards are older than the ones Genbu serves (GENBU-147).
FRESH = "genbu-fresh.js"

