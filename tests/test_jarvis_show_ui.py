import io
import os
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown import ui  # noqa: E402


def plain(**kw):
    stream = io.StringIO()
    return ui.Out(stream, stream, color=False, fancy=True, width=60, **kw), stream


class OutTests(unittest.TestCase):
    def test_lines_are_sorted_by_what_they_say(self):
        out, stream = plain()
        out.progress("# Home Assistant")
        out.progress("room dashboard dashboard-livingroom made")
        out.progress("the Show is already in Living Room")
        out.progress("WARN: no esphome action yet")
        lines = stream.getvalue().splitlines()
        self.assertEqual(lines[0], "")
        self.assertTrue(lines[1].startswith("── Home Assistant ──"))
        self.assertEqual(len(lines[1]), 60)
        self.assertEqual(lines[2:], ["  ✓ room dashboard dashboard-livingroom made",
                                     "  · the Show is already in Living Room",
                                     "  ! no esphome action yet"])
        self.assertEqual(out.warnings, ["no esphome action yet"])

    def test_long_lines_wrap_under_their_text(self):
        out, stream = plain()
        out.done("word " * 20)
        lines = stream.getvalue().splitlines()
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(len(line) <= 60 for line in lines))
        self.assertTrue(all(line.startswith("    ") for line in lines[1:]))

    def test_without_utf8_or_colour_the_layout_stays(self):
        stream = io.StringIO()
        out = ui.Out(stream, stream, color=False, fancy=False, width=60)
        out.heading("Checks")
        out.done("ok")
        out.fail("stop")
        self.assertEqual(stream.getvalue().splitlines(), ["", "-- Checks " + "-" * 50, "  + ok", "  x stop"])

    def test_colour_takes_no_room(self):
        out = ui.Out(io.StringIO(), color=True, fancy=True, width=60)
        line = out.lines("warn", "look here", paint="33")[0]
        self.assertIn("\033[", line)
        self.assertEqual(ui.visible(line), "  ! look here")
        prompt = out.paint("36", "›", prompt=True)
        self.assertEqual(ui.visible(prompt), "›")

    def test_finish_counts_the_warnings(self):
        out, stream = plain()
        out.finish("Done")
        out.warn("one")
        out.warn("two")
        out.finish("Done")
        self.assertEqual(stream.getvalue().splitlines(),
                         ["", "  ✓ Done", "  ! one", "  ! two", "", "  ! Done, with 2 things to look at (marked ! above)"])

    def test_cautions_are_not_counted(self):
        out, _ = plain()
        out.caution("it erases the Show")
        self.assertEqual(out.warnings, [])


class QuestionBoxTests(unittest.TestCase):
    def asker(self, answers, *, fancy=True):
        from jarvis_crown.settings import Asker
        said, prompts = [], []

        def ask(prompt):
            prompts.append(prompt)
            return answers.pop(0)
        out = ui.Out(io.StringIO(), color=False, fancy=fancy, width=60)
        return Asker(True, ask=ask, say=said.append, out=out), said, prompts

    def test_a_question_is_boxed_with_a_blank_line_before_it(self):
        asker, said, prompts = self.asker(["kitchen"])
        self.assertEqual(asker.text("Show name", None, str, help="what Home Assistant calls it"), "kitchen")
        self.assertEqual(said[0], "")
        self.assertTrue(said[1].startswith("  ╭─ Show name ─"))
        self.assertEqual(len(said[1]), 60)
        self.assertEqual(said[2:], ["  │  Enter to skip", "  │  what Home Assistant calls it"])
        self.assertEqual(prompts, ["  ╰─› "])

    def test_choices_sit_in_the_box_and_the_plain_box_is_ascii(self):
        asker, said, prompts = self.asker(["2"], fancy=False)
        self.assertEqual(asker.pick("wake word", "Okay Nabu", ["Okay Nabu", "Hey Jarvis"]), "Hey Jarvis")
        self.assertTrue(said[1].startswith("  +- wake word -"))
        self.assertEqual(said[2:], ["  |   1  Okay Nabu  (now)", "  |   2  Hey Jarvis"])
        self.assertEqual(prompts, ["  +-> number or name (Enter leaves it as it is) "])

    def test_a_long_question_wraps_down_the_side(self):
        asker, said, _ = self.asker(["x"])
        asker.text("word " * 20, None, str)
        self.assertTrue(all(len(line) <= 60 for line in said))
        self.assertTrue(said[2].startswith("  │  word"))

    def test_the_typed_phrase_gets_its_own_box(self):
        stream = io.StringIO()
        out = ui.Out(stream, stream, color=False, fancy=True, width=60)
        self.assertEqual(out.typed("ERASE", ask=lambda prompt: " ERASE \n"), "ERASE")
        self.assertTrue(stream.getvalue().splitlines()[1].startswith("  ╭─ Type ERASE to continue ─"))


class PaletteTests(unittest.TestCase):
    def test_openjade_colours_at_each_depth(self):
        out = ui.Out(io.StringIO(), color=True, fancy=True, width=60)
        for env, code in (({"COLORTERM": "truecolor"}, "38;2;208;132;84"), ({"TERM": "xterm-256color"}, "38;5;173"),
                          ({"TERM": "xterm"}, "33")):
            with self.subTest(env=env), mock.patch.dict(os.environ, env, clear=True):
                self.assertIn(f"\033[{code}m", out.lines("warn", "look", paint="33")[0])

    def test_a_question_prompt_tells_readline_the_codes_take_no_room(self):
        out = ui.Out(io.StringIO(), color=True, fancy=True, width=60)
        with mock.patch.dict(sys.modules, {"readline": object()}):
            prompt = out.box_prompt()
        self.assertIn("\001", prompt)
        self.assertEqual(ui.visible(prompt), "  ╰─› ")


if __name__ == "__main__":
    unittest.main()
