"""Names the genbu parts share."""

DOMAIN = "genbu"
MANUFACTURERS = {"OpenJade"}  # what echod reports for a Genbu Show over ESPHome
CONF_MAIN = "main_show"  # the first main update Show, from the installer or the setup form
CONF_EXTRA = "extra_mains"
CONF_NOTIFY = "notify_service"  # e.g. mobile_app_my_phone, for the push when a main is gone a day
HOUSE_ENTITY = "sensor.genbu_house_version"
MAIN_SELECT = "select.genbu_main_update_show"
CHANGED = f"{DOMAIN}_changed"  # dispatcher signal: the house was looked at again
STORE_KEY = f"{DOMAIN}.house"
STORE_VERSION = 1
SERVICE_FORGET = "forget_show"  # the installer's --remove-show, once the Show is out of ESPHome
PANEL = "genbu"  # the sidebar page with a card per Show, at /genbu
PANEL_FILES = "/genbu_panel"  # where its script is served from (frontend/)

# Repairs issue ids; the missing one gets the node name added, so one issue per main update Show.
ISSUE_MISSING = "main_missing_"
ISSUE_PICKED = "main_picked_"
ISSUE_NO_MAIN = "no_main"

# The push, by Home Assistant's language. The rest is translated in strings.json.
PUSH = {
    "en": ("Genbu: main update Show offline",
           "{name} has been offline since {since}. Pick a new main update Show in Home Assistant, "
           "or Genbu picks {pick} on {pick_at}."),
    "de": ("Genbu: Haupt-Update-Show offline",
           "{name} ist seit {since} offline. Wähl in Home Assistant einen neuen Haupt-Update-Show, "
           "sonst nimmt Genbu am {pick_at} {pick}."),
}
