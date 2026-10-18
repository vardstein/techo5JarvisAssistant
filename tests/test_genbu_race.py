"""Which Show answers a wake word several heard (ha/custom_components/genbu/race.py, GENBU-224, wiki 734)."""

import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
GENBU = ROOT / "ha" / "custom_components" / "genbu"
SPEC = importlib.util.spec_from_file_location("genbu_race", GENBU / "race.py")
race = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = race  # dataclasses look their module up
SPEC.loader.exec_module(race)

PHRASE = "okay_genbu"


def said(show, level=-30.0, floor=-60.0, score=0.9, cutoff=0.5, clipped=False, phrase=PHRASE, **extra):
    data = {"wake": f"w-{show}", "phrase": phrase, "slot": "1", "level_db": str(level), "floor_db": str(floor),
            "score": str(score), "cutoff": str(cutoff), "clipped": str(clipped).lower(), **extra}
    return race.heard(show, show.title(), data)


def run(*hearings, racers=None, last=None, at=0.0):
    """Races the hearings, all within the window, and closes the race."""
    ref = race.Referee()
    if last is not None:
        ref.last = last
    racers = racers if racers is not None else {h.show for h in hearings}
    for h in hearings:
        ref.hear(h, racers, at)
    return ref, ref.close(PHRASE, at + race.WINDOW)


class HeardTests(unittest.TestCase):
    def test_what_the_show_said(self):
        h = said("kitchen", level=-25.5, floor=-55.0, score=0.75, cutoff=0.5)
        self.assertEqual((h.wake, h.phrase, h.level_db, h.floor_db), ("w-kitchen", PHRASE, -25.5, -55.0))
        self.assertAlmostEqual(h.over_noise, 29.5)
        self.assertAlmostEqual(h.certainty, 0.5)
        self.assertFalse(h.odd)

    def test_no_wake_id_nothing_to_answer(self):
        self.assertIsNone(race.heard("kitchen", "Kitchen", {"phrase": PHRASE, "level_db": "-20"}))

    def test_more_than_a_microphone_gives_is_clamped_and_odd(self):
        h = said("liar", level=40.0, floor=-200.0, score=3.0)
        self.assertEqual((h.level_db, h.floor_db), (0.0, race.SILENCE_DB))
        self.assertEqual(h.certainty, 1.0)
        self.assertTrue(h.odd)

    def test_missing_or_nonsense_is_the_lowest_and_odd(self):
        for bad in ("", "nan", "loud"):
            h = said("odd", level=bad)
            self.assertEqual(h.level_db, race.SILENCE_DB, bad)
            self.assertTrue(h.odd, bad)

    def test_clipped_counts_as_the_top(self):
        h = said("near", level=-12.0, clipped=True)
        self.assertEqual(h.level_db, 0.0)
        self.assertTrue(h.clipped)

    def test_a_floor_over_the_level_is_no_negative_noise(self):
        self.assertEqual(said("x", level=-50.0, floor=-40.0).over_noise, 0.0)


class PickTests(unittest.TestCase):
    def test_the_loudest_answers(self):
        _, v = run(said("hall", level=-35.0), said("kitchen", level=-20.0), said("office", level=-40.0))
        self.assertEqual(v.winner.show, "kitchen")
        self.assertEqual({h.show for h in v.losers}, {"hall", "office"})

    def test_clipped_beats_a_loud_one(self):
        _, v = run(said("hall", level=-6.0), said("kitchen", level=-15.0, clipped=True))
        self.assertEqual(v.winner.show, "kitchen")

    def test_barely_heard_only_answers_alone(self):
        # Louder, but hardly out of its own noise: a Show next to a running tap.
        tap = said("tap", level=-20.0, floor=-21.0)
        quiet = said("quiet", level=-40.0, floor=-60.0)
        self.assertEqual(run(tap, quiet)[1].winner.show, "quiet")
        self.assertEqual(run(said("tap", level=-20.0, floor=-21.0))[1].winner.show, "tap")

    def test_a_tie_goes_to_the_show_still_talked_to(self):
        a, b = said("alpha", level=-30.0), said("bravo", level=-30.5)
        self.assertEqual(run(a, b)[1].winner.show, "alpha")
        # Bravo is still being talked to: its bonus alone takes it, and so would the tie.
        self.assertEqual(run(a, b, last=("bravo", 0.0))[1].winner.show, "bravo")

    def test_the_bonus_runs_out(self):
        a, b = said("alpha", level=-30.0), said("bravo", level=-33.0)
        self.assertEqual(run(a, b, last=("bravo", 0.0), at=race.STILL_TALKING + 1)[1].winner.show, "alpha")

    def test_a_tie_then_goes_to_the_clearer_one(self):
        a = said("alpha", level=-30.0, floor=-50.0)
        b = said("bravo", level=-31.0, floor=-50.0, score=1.0)
        self.assertEqual(run(a, b)[1].winner.show, "alpha")

    def test_a_tie_with_nothing_else_goes_by_name(self):
        self.assertEqual(run(said("zulu"), said("alpha"))[1].winner.show, "alpha")

    def test_a_board_offset_counts(self):
        a, b = said("alpha", level=-30.0), said("bravo", level=-33.0)
        a.model, b.model = "small", "big"
        race.BOARD_DB["big"] = 8.0
        try:
            self.assertEqual(run(a, b)[1].winner.show, "bravo")
        finally:
            race.BOARD_DB.clear()

    def test_a_winner_another_heard_far_clearer_is_flagged(self):
        loud = said("loud", level=-5.0, floor=-10.0)
        clear = said("clear", level=-20.0, floor=-50.0)
        v = run(loud, clear)[1]
        self.assertEqual((v.winner.show, v.flagged.show), ("loud", "clear"))
        self.assertIsNone(run(said("a", level=-20.0), said("b", level=-25.0))[1].flagged)

    def test_describe_says_the_parts(self):
        text = race.describe(said("kitchen", level=-20.0, clipped=True), last="kitchen")
        self.assertIn("Kitchen", text)
        self.assertIn("clipped", text)
        self.assertIn("still talking", text)


class RefereeTests(unittest.TestCase):
    def test_the_first_opens_the_next_wait(self):
        ref = race.Referee()
        racers = {"a", "b", "c"}
        self.assertEqual(ref.hear(said("a"), racers, 0.0), "open")
        self.assertEqual(ref.hear(said("b"), racers, 0.01), "wait")
        self.assertEqual(ref.hear(said("c"), racers, 0.02), "close")

    def test_one_show_closes_at_once(self):
        self.assertEqual(race.Referee().hear(said("a"), {"a"}, 0.0), "close")

    def test_the_same_show_twice_counts_once(self):
        ref = race.Referee()
        ref.hear(said("a"), {"a", "b"}, 0.0)
        self.assertEqual(ref.hear(said("a"), {"a", "b"}, 0.01), "wait")
        self.assertEqual(len(ref.races[PHRASE].everyone), 1)

    def test_a_verdict_comes_once(self):
        ref, v = run(said("a"), said("b"))
        self.assertIsNotNone(v)
        self.assertIsNone(ref.close(PHRASE, 1.0))
        self.assertIsNone(race.Referee().close(PHRASE, 0.0))

    def test_waited_is_from_the_first(self):
        ref = race.Referee()
        ref.hear(said("a"), {"a", "b"}, 10.0)
        self.assertAlmostEqual(ref.close(PHRASE, 10.08).waited, 0.08)

    def test_late_is_told_and_says_whether_it_would_have_won(self):
        ref, v = run(said("a", level=-30.0), racers={"a", "b", "c"})
        loud, quiet = said("b", level=-10.0), said("c", level=-50.0)
        self.assertEqual(ref.hear(loud, {"a", "b", "c"}, 0.3), "late")
        self.assertTrue(ref.would_have_won(loud))
        self.assertEqual(ref.hear(quiet, {"a", "b", "c"}, 0.3), "late")
        self.assertFalse(ref.would_have_won(quiet))

    def test_would_have_won_weighs_who_was_still_talked_to(self):
        ref, _ = run(said("a", level=-30.0), racers={"a", "b"}, last=("a", 0.0))
        late = said("b", level=-29.0)
        ref.hear(late, {"a", "b"}, 0.3)
        self.assertFalse(ref.would_have_won(late))

    def test_after_forget_it_is_a_new_race(self):
        ref, _ = run(said("a"), racers={"a", "b"})
        self.assertEqual(ref.hear(said("b"), {"a", "b"}, race.WINDOW + race.FORGET + 0.1), "open")

    def test_other_phrases_race_apart(self):
        ref = race.Referee()
        ref.hear(said("a"), {"a", "b"}, 0.0)
        self.assertEqual(ref.hear(said("b", phrase="hey_jarvis"), {"a", "b"}, 0.01), "open")

    def test_the_winner_is_remembered_as_still_talked_to(self):
        ref, v = run(said("a", level=-10.0), said("b"))
        self.assertEqual(ref.still_talking(race.WINDOW + 1), "a")
        self.assertEqual(ref.still_talking(race.WINDOW + race.STILL_TALKING + 1), "")


class ShowSideTests(unittest.TestCase):
    """The names the Show's echod uses, so the two cannot drift apart."""

    def test_the_names_match_echod(self):
        go = (ROOT / "echod/internal/feature/voice/race.go").read_text()
        const = (GENBU / "const.py").read_text()
        for name in ('"esphome.genbu_wake"', '"wake_race"', '"wake_verdict"'):
            self.assertIn(name, go)
            self.assertIn(name, const)
        for field in ("wake", "phrase", "level_db", "floor_db", "score", "cutoff", "clipped"):
            self.assertIn(f'"{field}":', go)


if __name__ == "__main__":
    unittest.main()
