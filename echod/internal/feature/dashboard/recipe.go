//go:build !dot

package dashboard

import (
	"strings"

	"github.com/HuskerMinion/techo5/echod/internal/config"
)

// The recipe page: the dashboard Home Assistant's jarvis_recipe integration fills with the recipe
// Jarvis last looked up (ha/custom_components/jarvis_recipe). Like the room's dashboard it is found,
// never made, by its address, and it is always streamed. It is a page that stays: once up it is
// there until somebody swipes on, and a swipe up or the swipe in from the left brings it back.

// RecipeBoard is the recipe dashboard's address in Home Assistant.
const RecipeBoard = "jarvis-rezept"

// RecipePath is the recipe dashboard as a page for Stream ("/jarvis-rezept"), empty when Home
// Assistant has none or there is no dashcast server to stream it.
func (f *Feature) RecipePath() string {
	d := config.Get().Dashboard
	if d.Server == "" || d.Mode == config.DashboardOff {
		return ""
	}
	return recipeIn(d.Known)
}

func recipeIn(known []config.DashboardChoice) string {
	for _, c := range known {
		if base, _, _ := strings.Cut(strings.Trim(c.Path, "/"), "/"); base == RecipeBoard {
			return "/" + RecipeBoard
		}
	}
	return ""
}
