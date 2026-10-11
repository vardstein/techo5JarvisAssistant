"""How the installer looks at the terminal: a heading per part, one line per thing, questions.

    ── Home Assistant ──────────────────────────────
      ✓ room dashboard dashboard-livingroom made
      · the Show is already in Living Room
      ! the Show has no esphome.x_y action yet

✓ is something done, · something that was already so, ! something to look at, ✗ a stop. Colour and
the symbols only where the terminal shows them; NO_COLOR, TERM=dumb and a pipe get plain text with
the same layout, so a saved log still reads.

The colours are OpenJade's, from the website: slate grey for the frame, jade green for done, the
warm orange for anything to look at. A terminal with full colour gets them exactly, one with 256
colours the nearest, and an older one the plain ANSI colours.

The question being asked sits in a box open on the right, its answer typed on the bottom corner:

    ╭─ Wi-Fi network ──────────────────────────────
    │  Enter keeps what is there
    ╰─› kitchen

The steps underneath report with a plain progress(text). A text starting "# " is a heading and one
starting "WARN: " a warning; the rest are sorted into done and already-so by how they read.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap
from typing import TextIO

WIDTH = 78

_CODES = re.compile(r"\033\[[0-9;]*m|[\001\002]")

# What a line says when nothing had to change.
_SAME = re.compile(r"\balready\b|\bleft (alone|as it is|at)\b|\bwas kept\b|\bwere kept\b|\bwas there\b|"
                   r"\bkept\b|\bunchanged\b|\bleft in\b|\bleft out\b|^no |, so no ")


# role: (full colour, 256-colour, plain ANSI). Mid tones, so they read on a dark and a light terminal.
_PALETTE = {
    "frame": ("139;150;164", "38;5;247", "2"),    # slate, --accent on the site
    "done": ("111;165;138", "38;5;72", "32"),     # jade
    "warn": ("208;132;84", "38;5;173", "33"),     # the site's orange (--warn)
    "fail": ("200;97;90", "38;5;167", "31"),
}


def _depth() -> int:
    """How many colours the terminal says it has: 3 (full), 2 (256) or 1 (the plain eight)."""
    if os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return 3
    return 2 if "256" in os.environ.get("TERM", "") else 1


def visible(text: str) -> str:
    """text without its colour codes: what takes room on the screen."""
    return _CODES.sub("", text)


class Out:
    def __init__(self, stream: TextIO | None = None, err: TextIO | None = None, *,
                 color: bool | None = None, fancy: bool | None = None, width: int | None = None) -> None:
        self._stream = stream
        self._err = err
        self._color = color
        self._fancy = fancy
        self._width = width
        self.warnings: list[str] = []

    # The streams are looked up when used, so a test that swaps sys.stdout sees the lines.
    @property
    def stream(self) -> TextIO:
        return self._stream or sys.stdout

    @property
    def err(self) -> TextIO:
        return self._err or sys.stderr

    @property
    def color(self) -> bool:
        if self._color is not None:
            return self._color
        return (self.stream.isatty() and not os.environ.get("NO_COLOR")
                and os.environ.get("TERM", "") != "dumb")

    @property
    def fancy(self) -> bool:
        if self._fancy is not None:
            return self._fancy
        return "utf" in (getattr(self.stream, "encoding", "") or "").lower()

    @property
    def width(self) -> int:
        if self._width:
            return self._width
        return max(40, min(WIDTH, shutil.get_terminal_size((WIDTH, 24)).columns))

    def paint(self, code: str, text: str, *, prompt: bool = False) -> str:
        """text in colour. prompt: for input(), whose line editing has to be told the codes take no room."""
        if not (self.color and text):
            return text
        start, end = f"\033[{code}m", "\033[0m"
        if prompt and "readline" in sys.modules:
            start, end = f"\001{start}\002", f"\001{end}\002"
        return start + text + end

    def tone(self, role: str, text: str, *, bold: bool = False, prompt: bool = False) -> str:
        """text in one of OpenJade's colours (_PALETTE)."""
        full, c256, basic = _PALETTE[role]
        code = {3: "38;2;" + full, 2: c256}.get(_depth(), basic)
        return self.paint(("1;" if bold else "") + code, text, prompt=prompt)

    def bold(self, text: str, *, prompt: bool = False) -> str:
        return self.paint("1", text, prompt=prompt)

    def dim(self, text: str, *, prompt: bool = False) -> str:
        return self.paint("2", text, prompt=prompt)

    def mark(self, kind: str, *, prompt: bool = False) -> str:
        fancy, plain, role = {"done": ("✓", "+", "done"), "same": ("·", "-", None), "warn": ("!", "!", "warn"),
                              "fail": ("✗", "x", "fail"), "ask": ("?", "?", "frame"), "prompt": ("›", ">", "frame"),
                              "step": ("▸", ">", "frame"), "caution": ("▲", "!", "warn")}[kind]
        sign = fancy if self.fancy else plain
        if role is None:
            return self.dim(sign, prompt=prompt)
        return self.tone(role, sign, bold=True, prompt=prompt)

    def _painted(self, paint: str, text: str) -> str:
        """The old ANSI codes the callers pass, in OpenJade's colours where it has one for them."""
        role = {"33": "warn", "31": "fail", "32": "done"}.get(paint)
        return self.tone(role, text) if role else self.paint(paint, text)

    def _wrap(self, text: str, first: str, rest: str, width: int) -> list[str]:
        return textwrap.wrap(text, width=width, initial_indent=first, subsequent_indent=rest,
                             break_long_words=False, break_on_hyphens=False) or [first.rstrip()]

    def _line(self, kind: str, text: str, *, stream: TextIO | None = None, paint: str | None = None) -> None:
        for line in self.lines(kind, text, paint=paint):
            print(line, file=stream or self.stream, flush=True)

    def lines(self, kind: str, text: str, *, paint: str | None = None) -> list[str]:
        """text after its mark, wrapped under itself. The mark is one column wide whatever it looks like."""
        wrapped = self._wrap(text, "", "", self.width - 4)
        return [("  " + self.mark(kind) + " " if i == 0 else "    ") + (self._painted(paint, w) if paint else w)
                for i, w in enumerate(wrapped)]

    def heading_lines(self, text: str) -> list[str]:
        rule = "─" if self.fancy else "-"
        head = f"{rule * 2} {text} "
        return ["", self.tone("frame", rule * 2 + " ") + self.bold(f"{text} ") +
                self.tone("frame", rule * max(3, self.width - len(head)))]

    # ------------------------------------------------------------------------------ the question box

    def _corners(self) -> tuple[str, str, str, str]:
        """top, side, bottom and the rule along the top, in the terminal's characters."""
        return ("╭─", "│", "╰─", "─") if self.fancy else ("+-", "|", "+-", "-")

    def box_top(self, title: str, *, role: str = "frame") -> list[str]:
        """A blank line, then the box's top with title in it; a long title wraps down its side."""
        top, side, _, rule = self._corners()
        words = self._wrap(title, "", "", self.width - 7)
        tail = rule * max(3, self.width - 6 - len(words[-1])) if len(words) == 1 else ""
        first = "  " + self.tone(role, top + " ") + self.bold(words[0]) + (" " + self.tone(role, tail) if tail else "")
        return ["", first] + ["  " + self.tone(role, side) + "  " + self.bold(w) for w in words[1:]]

    def box_side(self, text: str, *, role: str = "frame", dim: bool = True) -> list[str]:
        """text down the box's side, wrapped under itself."""
        _, side, _, _ = self._corners()
        return ["  " + self.tone(role, side) + "  " + (self.dim(w) if dim else w)
                for w in self._wrap(text, "", "", self.width - 5)]

    def box_row(self, text: str, *, role: str = "frame") -> str:
        """A line in the box kept as it is, like a numbered choice. Six columns in, as before the box."""
        _, side, _, _ = self._corners()
        return "  " + self.tone(role, side) + "   " + text

    def box_prompt(self, text: str = "", *, role: str = "frame") -> str:
        """The box's bottom corner, where the answer goes. Its codes are marked for input()."""
        _, _, bottom, _ = self._corners()
        sign = "›" if self.fancy else ">"
        return ("  " + self.tone(role, bottom + sign, bold=True, prompt=True) + " " +
                (self.dim(text, prompt=True) + " " if text else ""))

    def typed(self, phrase: str, *, ask=None) -> str:
        """The last word before something that can't be undone: the phrase typed in, in an orange box."""
        for line in self.box_top(f"Type {phrase} to continue", role="warn"):
            print(line, file=self.stream, flush=True)
        return (ask or input)(self.box_prompt(role="warn")).strip()

    def note_lines(self, text: str, *, indent: int = 4) -> list[str]:
        pad = " " * indent
        return [pad + self.dim(w) for w in self._wrap(text, "", "", self.width - indent)]

    # ------------------------------------------------------------------------------------ the lines

    def title(self, text: str, detail: str = "") -> None:
        print(file=self.stream)
        print(self.bold(text) + (self.dim("  " + detail) if detail else ""), file=self.stream, flush=True)

    def heading(self, text: str) -> None:
        for line in self.heading_lines(text):
            print(line, file=self.stream, flush=True)

    def done(self, text: str) -> None:
        self._line("done", text)

    def same(self, text: str) -> None:
        self._line("same", text, paint="2")

    def warn(self, text: str) -> None:
        self.warnings.append(text)
        self._line("warn", text, paint="33")

    def fail(self, text: str) -> None:
        self.stream.flush()
        self._line("fail", text, stream=self.err, paint="31")

    def step(self, text: str) -> None:
        """A longer stage starting, like the bootloader being unlocked."""
        self._line("step", text, paint="1")

    def caution(self, text: str) -> None:
        """Something to know before saying yes: not a problem, so not counted with the warnings."""
        self._line("caution", text, paint="33")

    def note(self, text: str, *, indent: int = 4) -> None:
        """A quieter line under the one before: a hint, a path, what to run next."""
        for line in self.note_lines(text, indent=indent):
            print(line, file=self.stream, flush=True)

    def command(self, text: str) -> None:
        print("      " + self.paint("1", text), file=self.stream, flush=True)

    def field(self, label: str, value: str, *, pad: int = 10) -> None:
        print("  " + self.dim(label.ljust(pad)) + " " + value, file=self.stream, flush=True)

    def progress(self, text: str) -> None:
        """One line from a step underneath, sorted by what it says."""
        if text.startswith("# "):
            self.heading(text[2:])
        elif text.startswith("WARN: "):
            self.warn(text[6:])
        elif _SAME.search(text):
            self.same(text)
        else:
            self.done(text)

    def finish(self, ok_text: str) -> None:
        """The last line of a run: done, or done with the warnings counted."""
        print(file=self.stream)
        if self.warnings:
            n = len(self.warnings)
            print("  " + self.mark("warn") + " " + self.bold(ok_text) +
                  self.tone("warn", f", with {n} thing{'s' if n != 1 else ''} to look at (marked ! above)"),
                  file=self.stream, flush=True)
        else:
            print("  " + self.mark("done") + " " + self.bold(ok_text), file=self.stream, flush=True)


out = Out()
