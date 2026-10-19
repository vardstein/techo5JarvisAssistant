from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MusicNowPlayingContractTests(unittest.TestCase):
    def test_music_assistant_never_collapses_to_legacy_strip(self):
        display = (ROOT / "echod/internal/feature/display/display.go").read_text(encoding="utf-8")
        self.assertIn('s.radio.Now != "Music Assistant" && d.stripDue', display)
        self.assertIn('if rd.Now == "Music Assistant" {\n\t\t\treturn // MA owns a persistent full-screen Now Playing page', display)

    def test_external_preferred_speaker_still_drives_now_playing_page(self):
        display = (ROOT / "echod/internal/feature/display/display.go").read_text(encoding="utf-8")
        route = (ROOT / "echod/internal/feature/home/music_route.go").read_text(encoding="utf-8")
        self.assertIn("routedMusic := s.music.Playing || s.music.Paused", display)
        self.assertIn('s.radio.Now, s.radio.Music = "Music Assistant", true', display)
        self.assertIn('Entity   string', route)
        self.assertIn('media_title', route)
        self.assertIn('media_artist', route)
        self.assertIn('media_album_name', route)

    def test_fullscreen_page_shows_route_album_progress_and_next(self):
        render = (ROOT / "echod/internal/feature/display/render_nowplaying.go").read_text(encoding="utf-8")
        for marker in (
            "musicPlace(s.music)",
            "albumName := rd.Album",
            "line(r.small, albumName",
            'strings.Join(s.music.Rooms, "  ·  ")',
            'r.upNext(s.music.Next, col.Dx())',
            'i18n.T("Next") + "  ·  "',
            "r.musicProgress(s.music, s.now",
        ):
            self.assertIn(marker, render)

    def test_queue_context_uses_supported_ha_response_action(self):
        route = (ROOT / "echod/internal/feature/home/music_route.go").read_text(encoding="utf-8")
        hass = (ROOT / "echod/internal/lib/hass/hass.go").read_text(encoding="utf-8")
        self.assertIn('CallResponse("music_assistant", "get_queue"', route)
        self.assertIn('?return_response', hass)
        self.assertIn('ServiceResponse json.RawMessage', hass)

    def test_transport_and_stop_target_the_real_routed_output(self):
        route = (ROOT / "echod/internal/feature/home/music_route.go").read_text(encoding="utf-8")
        display = (ROOT / "echod/internal/feature/display/display.go").read_text(encoding="utf-8")
        home = (ROOT / "echod/internal/feature/home/home.go").read_text(encoding="utf-8")
        self.assertIn("func (f *Feature) MusicTransport", route)
        self.assertIn('hass.Get().Call("media_player", service', route)
        # The page's buttons go through MusicTap, which shows the tap at once and hands it to
        # MusicTransport off the touch loop.
        self.assertIn("d.musicTap(media.TransportNext, next)", display)
        self.assertIn("home.Get().MusicTap(t)", display)
        self.assertIn("f.MusicTransport(t)", route)
        self.assertIn("routed := f.stopRoutedMusic()", home)

    def test_favorite_targets_the_real_routed_ma_player(self):
        favorite = (ROOT / "echod/internal/feature/home/favorite.go").read_text(encoding="utf-8")
        self.assertIn("playback := f.MusicPlayback()", favorite)
        self.assertIn("ma := playback.Entity", favorite)

    def test_external_route_can_fetch_cover_art_but_keeps_decoder_bounds(self):
        route = (ROOT / "echod/internal/feature/home/music_route.go").read_text(encoding="utf-8")
        art = (ROOT / "echod/internal/feature/home/maart.go").read_text(encoding="utf-8")
        decode = (ROOT / "echod/internal/feature/home/decode.go").read_text(encoding="utf-8")
        self.assertIn('st.Attributes["entity_picture"]', route)
        self.assertIn("hass.Get().FetchURL(fetch)", route)
        self.assertIn("RemoteArt(b, ours)", route)
        self.assertIn("layoutArt", art)
        self.assertIn("maxArtPixels", decode)


if __name__ == "__main__":
    unittest.main()
