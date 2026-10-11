//go:build !dot

package dashboard

import (
	"testing"

	"github.com/HuskerMinion/techo5/echod/internal/config"
)

func TestRecipeIn(t *testing.T) {
	for _, c := range []struct {
		known []config.DashboardChoice
		want  string
	}{
		{nil, ""},
		{[]config.DashboardChoice{{Label: "Home", Path: "lovelace/0"}}, ""},
		{[]config.DashboardChoice{{Label: "Rezept", Path: "jarvis-rezept/0"}}, "/jarvis-rezept"},
		{[]config.DashboardChoice{{Label: "Rezept", Path: "jarvis-rezept"}}, "/jarvis-rezept"},
		{[]config.DashboardChoice{{Label: "Rezepte", Path: "jarvis-rezepte/0"}}, ""},
	} {
		if got := recipeIn(c.known); got != c.want {
			t.Errorf("recipeIn(%v) = %q, want %q", c.known, got, c.want)
		}
	}
}
