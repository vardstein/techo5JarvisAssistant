from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MusicRoutingContractTests(unittest.TestCase):
    def test_room_has_persisted_preferred_music_assistant_player(self):
        source = (ROOT / "echod/internal/config/home.go").read_text(encoding="utf-8")
        self.assertIn('MusicPrimary string `json:"music_primary,omitempty"`', source)
        self.assertIn("func (w HomeWriter) MusicPrimary", source)

    def test_ha_and_direct_brain_share_same_play_resolver(self):
        home = (ROOT / "echod/internal/feature/home/home.go").read_text(encoding="utf-8")
        assistant = (ROOT / "echod/internal/feature/assistant/tools.go").read_text(encoding="utf-8")
        self.assertIn('Name: "music_play"', home)
        self.assertIn('return f.PlayMusic(c.String("media_id"))', home)
        self.assertIn('Name: "play_music"', assistant)
        self.assertIn("home.Get().PlayMusic(id)", assistant)

    def test_failover_uses_music_assistant_queue_transfer_and_is_sticky(self):
        source = (ROOT / "echod/internal/feature/home/music_route.go").read_text(encoding="utf-8")
        self.assertIn('"music_assistant", "transfer_queue"', source)
        self.assertIn('"source_player": primary', source)
        self.assertIn('"auto_play":     true', source)
        self.assertIn("if musicRoute.fallbackActive", source)
        self.assertIn("sticky until the next explicit Play request", source)
        self.assertNotIn('transfer_queue", map[string]any{\n\t\t"entity_id":    primary', source)

    def test_next_explicit_play_returns_to_healthy_primary_and_stops_local_fallback(self):
        source = (ROOT / "echod/internal/feature/home/music_route.go").read_text(encoding="utf-8")
        self.assertIn('"media_player", "media_stop"', source)
        self.assertIn('(wasFallback || localCarried()) && target != local', source)
        self.assertIn('"music_assistant", "play_media"', source)
        self.assertIn('"media_id":  mediaID', source)

    def test_unavailable_unknown_or_invalid_primary_falls_back_local(self):
        source = (ROOT / "echod/internal/feature/home/music_route.go").read_text(encoding="utf-8")
        for state in ('"unavailable"', '"unknown"'):
            self.assertIn(state, source)
        self.assertIn("!musicAssistantState(st)", source)
        self.assertIn("return local, local, true, nil", source)


if __name__ == "__main__":
    unittest.main()
