"""v0.35.0 — m31: the m30 containment rule was width-only; a line break escaped.

Live repro (measured against the real renderer, before the fix):

    a centred box centres on the TOTAL height of the text it is given, and
    `fit_text` measured width across the whole string without ever asking how
    many lines that string draws. PIL breaks a line on `\\n`, so a value of
    seven lines drew its FIRST line at y=15 — ten px above the custom card's
    panel top at y=25 — and a top-anchored box (board meta, hero subtitle)
    spilled below its own border. Silent, exit 0: the m30 class one axis deeper.

DECIDED (v0.35.0):
* The fix lives in `screen.flatten_lines`, called by `fit_text` (and `ellipsize`)
  — the single home of containment — so every caller inherits vertical
  containment exactly as it already inherits horizontal. A line-breaking
  character (every boundary `str.splitlines()` recognises) flattens to a space
  before the text is measured or drawn.
* Newline-free text is returned byte-for-byte identical, so every render that
  never contained a break is unchanged (locked below by a parity test against a
  no-flatten reference).
* The CLI is deliberately NOT changed here: a newline is not inert data, it is
  unrenderable on a one-line box, and the loader must stay permissive (m27/m28/
  m29 trust boundary) — hand-edited JSON with a `\\n` still has to render, just
  contained. Rejecting it at the CLI is queued separately, not a gate fix.

Zero hardware, zero network, and no test depends on today's date.
"""

import datetime
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw

from ak820 import cards, screen
from ak820.palette import THEMES
from ak820.screen import (
    FONT_BOLD_PATHS,
    FONT_NARROW_PATHS,
    _load_font,
    fit_text,
    flatten_lines,
)

FIXED_TODAY = datetime.date(2026, 6, 1)

# The custom card's value panel and the gutters above/below it. The header pill
# ends at y=19 and the panel top border is at y=25, so y 20..24 is a clean gutter
# that only escaped text can paint; the panel bottom border is at y=78 and the
# sub line starts at y=85, so y 79..84 is the matching gutter below.
PANEL = (6, 25, 121, 78)
ABOVE_BAND = range(20, 25)
BELOW_BAND = range(79, 84)
PANEL_X = range(7, 121)  # inside the vertical border columns


def bright(px) -> bool:
    """A painted glyph pixel, not a dark panel/background fill."""
    return sum(px[:3]) > 250


def _row(milestone_str, short="DAUGHTER", days=37, theme="calm"):
    return {
        "id": short.lower(),
        "title": short.title(),
        "short_title": short,
        "orig_date": "06-01",
        "next_date": FIXED_TODAY + datetime.timedelta(days=days),
        "date_label": (FIXED_TODAY + datetime.timedelta(days=days)).strftime("%b %d"),
        "days_left": days,
        "years": 5,
        "category": "milestone",
        "milestone_str": milestone_str,
        "short_age": milestone_str,
        "theme_key": theme,
    }


# Multi-line spellings that all used to escape their box vertically.
MULTILINE_VALUES = [
    "A\nB",
    "First day\nof grade 5",
    "line1\nline2\nline3\nline4\nline5\nline6\nline7",
    "WORD WORD\nWORD WORD\nWORD WORD\nWORD WORD\nWORD WORD",
    "a\r\nb\r\nc",
    "solo\n\n\nmany\n\nbreaks",
]


class TestFlattenLines(unittest.TestCase):
    def test_line_breaks_become_single_spaces(self):
        cases = {
            "a\nb": "a b",
            "a\r\nb": "a b",
            "a\rb": "a b",
            "line1\nline2\nline3": "line1 line2 line3",
            "a\n\n\nb": "a b",
            "  padded  ": "  padded  ",  # no break -> untouched, incl. whitespace
            "no break here": "no break here",
            "": "",
            "x": "x",
        }
        for src, want in cases.items():
            with self.subTest(src=src):
                self.assertEqual(flatten_lines(src), want)

    def test_leading_and_trailing_breaks_are_stripped(self):
        self.assertEqual(flatten_lines("\nhello\n"), "hello")
        self.assertEqual(flatten_lines("\n\na\nb\n\n"), "a b")

    def test_unicode_line_separators_flatten(self):
        # str.splitlines() boundaries PIL does not even break on still render as
        # stray inline glyphs, so they flatten too.
        self.assertEqual(flatten_lines("a\u2028b\u2029c"), "a b c")
        self.assertEqual(flatten_lines("a\x85b"), "a b")

    def test_newline_free_text_is_returned_identically(self):
        """The byte-identical guarantee: no break -> the very same string."""
        for src in ["Turns 5", "Dec 01 • Turns 5", "  keep  spacing  ", "a\tb"]:
            with self.subTest(src=src):
                out = flatten_lines(src)
                self.assertEqual(out, src)
                self.assertIs(out, src)  # not even a copy

    def test_result_never_contains_a_line_break(self):
        for src in MULTILINE_VALUES:
            with self.subTest(src=src[:16]):
                self.assertNotIn("\n", flatten_lines(src))
                self.assertNotIn("\r", flatten_lines(src))


class TestFitTextSingleLine(unittest.TestCase):
    def setUp(self):
        self.draw = ImageDraw.Draw(Image.new("RGB", (128, 128)))

    def test_fit_text_never_returns_a_multiline_string(self):
        for src in MULTILINE_VALUES:
            with self.subTest(src=src[:16]):
                _font, drawn = fit_text(self.draw, src, FONT_BOLD_PATHS, 12, 9, 108)
                self.assertNotIn("\n", drawn)
                self.assertNotIn("\r", drawn)

    def test_parity_newline_free_is_byte_identical(self):
        """A no-flatten reference: newline-free text must render exactly as before."""

        def reference(draw, text, paths, mx, mn, w):
            for sz in range(mx, mn - 1, -1):
                font = _load_font(paths, sz)
                if screen.text_width(draw, text, font) <= w:
                    return sz, text
            font = _load_font(paths, mn)
            return mn, screen.ellipsize(draw, text, font, w)

        cases = [
            ("DAUGHTER", FONT_NARROW_PATHS, 16, 11, 116),
            ("Dec 01 • Turns 5", FONT_BOLD_PATHS, 11, 9, 116),
            ("27 DAYS", FONT_BOLD_PATHS, 24, 13, 108),
        ]
        for text, paths, mx, mn, w in cases:
            with self.subTest(text=text):
                font, drawn = fit_text(self.draw, text, paths, mx, mn, w)
                size, ref_text = reference(self.draw, text, paths, mx, mn, w)
                self.assertEqual(font.size, size)
                self.assertEqual(drawn, ref_text)


class TestCustomValueVerticalContainment(unittest.TestCase):
    """The gate defect itself: the centred value escaping above the panel."""

    def _gutter_ink(self, img):
        above = [
            (x, y) for y in ABOVE_BAND for x in PANEL_X if bright(img.getpixel((x, y)))
        ]
        below = [
            (x, y) for y in BELOW_BAND for x in PANEL_X if bright(img.getpixel((x, y)))
        ]
        return above, below

    def test_value_never_escapes_above_or_below_the_panel(self):
        for value in MULTILINE_VALUES:
            with self.subTest(value=value[:20]):
                img = cards.render_custom_card("T", value, "", "", "soon")
                above, below = self._gutter_ink(img)
                self.assertEqual(above, [], "value drew above the panel top")
                self.assertEqual(below, [], "value drew below the panel bottom")

    def test_value_is_actually_drawn(self):
        """Guards the containment test above against passing vacuously."""
        for value in MULTILINE_VALUES:
            with self.subTest(value=value[:20]):
                img = cards.render_custom_card("T", value, "", "", "soon")
                painted = any(
                    bright(img.getpixel((x, y)))
                    for y in range(PANEL[1] + 1, PANEL[3])
                    for x in PANEL_X
                )
                self.assertTrue(painted, "nothing was drawn inside the panel")

    def test_multiline_value_renders_identically_to_its_flattened_form(self):
        """Proves the mechanism: the renderer draws the flattened single line."""
        for value in MULTILINE_VALUES:
            with self.subTest(value=value[:20]):
                a = cards.render_custom_card("T", value, "", "", "soon")
                b = cards.render_custom_card("T", flatten_lines(value), "", "", "soon")
                self.assertEqual(a.tobytes(), b.tobytes())

    def test_sub_and_footer_newlines_are_contained(self):
        img = cards.render_custom_card(
            "T\nT2", "ok", "sub\nline", "foot\ner", "soon"
        )
        self.assertEqual(img.size, (128, 128))
        above, below = self._gutter_ink(img)
        self.assertEqual(above, [])
        self.assertEqual(below, [])


class TestBoardAndHeroVerticalContainment(unittest.TestCase):
    def test_board_meta_newline_stays_inside_row_box(self):
        # row 0 box is y 22..53; y 54..56 is the gutter before row 1 (starts 57).
        for note in ["First day\nof grade 5", "a\nb\nc\nd\ne", "Turns 5"]:
            with self.subTest(note=note[:16]):
                img = cards.render_board_card([_row(note)], today=FIXED_TODAY)
                escaped = [
                    (x, y)
                    for y in range(54, 57)
                    for x in range(9, 123)
                    if bright(img.getpixel((x, y)))
                ]
                self.assertEqual(escaped, [], "meta line spilled below the row box")

    def test_hero_subtitle_newline_does_not_cross_the_divider(self):
        # divider at y=105, ticker starts y=109; y 106..108 is the clean gutter.
        for note in ["Turns 5\nYay", "a\nb\nc\nd"]:
            with self.subTest(note=note[:16]):
                img = cards.render_hero_card([_row(note)], today=FIXED_TODAY)
                crossed = [
                    (x, y)
                    for y in range(106, 109)
                    for x in range(10, 118)
                    if bright(img.getpixel((x, y)))
                ]
                self.assertEqual(crossed, [], "subtitle crossed the divider")


class TestContainmentSingleHome(unittest.TestCase):
    """Anti-recurrence: flattening lives at the containment home, nowhere else."""

    def test_fit_text_and_ellipsize_both_flatten(self):
        import inspect

        src = inspect.getsource(screen)
        self.assertIn("def flatten_lines", src)
        fit_body = src.split("def fit_text", 1)[1].split("def ", 1)[0]
        ellip_body = src.split("def ellipsize", 1)[1].split("def ", 1)[0]
        self.assertIn("flatten_lines(text)", fit_body)
        self.assertIn("flatten_lines(text)", ellip_body)

    def test_cards_does_not_reimplement_flattening(self):
        src = (ROOT / "ak820" / "cards.py").read_text(encoding="utf-8")
        # cards relies on fit_text; it must not hand-roll its own newline handling
        self.assertNotIn("flatten_lines", src)
        self.assertNotIn("\\n", src)

    def test_screen_stays_within_the_module_budget(self):
        # the fix must not blow the §10 <300-line budget (regression guard).
        lines = (ROOT / "ak820" / "screen.py").read_text(encoding="utf-8").splitlines()
        self.assertLess(len(lines), 300)


if __name__ == "__main__":
    unittest.main()
