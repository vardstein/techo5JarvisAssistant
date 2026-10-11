"""Names the jarvis_recipe parts share."""

DOMAIN = "jarvis_recipe"
CONF_SEARXNG = "searxng_url"
NEW_RECIPE = f"{DOMAIN}_new"  # dispatcher signal: a recipe was looked up
SHOW_ACTION = "recipe_show"  # the Show's action, esphome.<show>_recipe_show
