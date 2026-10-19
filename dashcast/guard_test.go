package main

import (
	"context"
	"errors"
	"strings"
	"testing"
)

// Music Assistant's panel is found by its app, under the slugs its repositories give it, and never
// one for administrators or anything else that frames a page.
func TestMusicAssistantsPanelIsFound(t *testing.T) {
	for _, c := range []struct {
		panels map[string]panel
		want   string
	}{
		{map[string]panel{"lovelace": {component: "lovelace"}, "d5369777_music_assistant": {component: "app", addon: "d5369777_music_assistant"}}, "d5369777_music_assistant"},
		{map[string]panel{"music": {component: "app", addon: "music_assistant"}}, "music"},
		{map[string]panel{"mab": {component: "app", addon: "d5369777_music_assistant_beta"}}, "mab"},
		{map[string]panel{"ma": {component: "app", addon: "d5369777_music_assistant", admin: true}}, ""},
		{map[string]panel{"ma": {component: "iframe", url: "http://10.0.0.5:8095"}}, ""},
		{map[string]panel{"esphome": {component: "app", addon: "5c53de3b_esphome"}}, ""},
		{map[string]panel{"x": {component: "app", addon: "not_music_assistant_really"}}, ""},
		{map[string]panel{"b": {component: "app", addon: "music_assistant"}, "a": {component: "app", addon: "x_music_assistant_beta"}}, "a"},
	} {
		if got := musicPanel(c.panels); got != c.want {
			t.Errorf("%v: found %q, want %q", c.panels, got, c.want)
		}
	}
}

// A Music Assistant session is kept on its panel: no dashboard, no other page, nothing that only
// starts with the panel's name.
func TestOnlyIsOnePanel(t *testing.T) {
	allows := only("d5369777_music_assistant")
	for path, want := range map[string]bool{
		"/d5369777_music_assistant":         true,
		"/d5369777_music_assistant/":        true,
		"/d5369777_music_assistant_beta":    false,
		"/lovelace/0":                       false,
		"/config/dashboard":                 false,
		"/d5369777_music_assistant/../conf": false,
		"":                                  false,
	} {
		if got, err := allows(context.Background(), path); err != nil || got != want {
			t.Errorf("%q: allowed %v (%v), want %v", path, got, err, want)
		}
	}
}

// A Show's own user gets a guard of its own, one per user however often the Show connects; an
// administrator's token, or one Home Assistant refuses, gets none.
func TestShowUsersGuards(t *testing.T) {
	u := &users{ask: func(_ context.Context, cfg config) (string, bool, error) {
		switch cfg.token {
		case "admin":
			return "Jade", true, nil
		case "kitchen", "bedroom":
			return "Show " + cfg.token, false, nil
		}
		return "", false, errTokenRefused
	}}
	ctx := context.Background()
	cfg := config{ha: "http://10.0.0.5:8123", token: "dashcast"}
	a, name, err := u.guardFor(ctx, cfg, "kitchen")
	if err != nil || a == nil || name != "Show kitchen" || a.cfg.token != "kitchen" {
		t.Fatalf("kitchen: %v %q %v", a, name, err)
	}
	if again, _, _ := u.guardFor(ctx, cfg, "kitchen"); again != a {
		t.Error("the same user got a second guard")
	}
	if b, _, _ := u.guardFor(ctx, cfg, "bedroom"); b == nil || b == a {
		t.Error("another user did not get a guard of its own")
	}
	for _, token := range []string{"admin", "made-up"} {
		if g, _, err := u.guardFor(ctx, cfg, token); g != nil || err == nil {
			t.Errorf("%s: got a guard", token)
		}
	}
	// Only a refused one is told apart: that Show gets no page at all, an administrator's gets dashcast's.
	if _, _, err := u.guardFor(ctx, cfg, "made-up"); !errors.Is(err, errTokenRefused) {
		t.Errorf("a refused token: %v", err)
	}
	if _, _, err := u.guardFor(ctx, cfg, "admin"); errors.Is(err, errTokenRefused) {
		t.Error("an administrator's token counted as refused")
	}
	if cfg.token != "dashcast" {
		t.Error("dashcast's own config was changed")
	}
}

// The parked tabs' keys and the log name a token by its hash, never the token itself.
func TestTokenIDIsNotTheToken(t *testing.T) {
	id := tokenID("a-long-lived-token")
	if id == "" || strings.Contains("a-long-lived-token", id) || id != tokenID("a-long-lived-token") || id == tokenID("another") {
		t.Errorf("tokenID gave %q", id)
	}
}
