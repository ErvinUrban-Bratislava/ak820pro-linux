"""v0.34.0 — m30: unbounded dynamic text on the 128x128 cards.

Live repro (before the fix, measured against the real renderer):

    a 66-char note on a board row drew `f"{date_label} • {milestone_str}"` at a
    FIXED font size 9 with no width fitting at all: 288 px of text from x=11
    into a 128 px screen — clipped mid-word, silently, exit 0.

The probe also found the premise in the queue was only half true: the "fitted"
strings did NOT contain either. `_fit_text_font` shrank the font and stopped —
at `min_size` it returned the font whether or not the text fitted, so the hero
subtitle overflowed its box by 199 px and the custom card's value by 182 px at
their minimum sizes. The defect was never "one unfitted line"; it was that the
fitter had no containment guarantee, and every card shared it.

DECIDED (v0.34.0):
* Renderer — `screen.fit_text` (fit, THEN ellipsize) is the single home of
  "never draw outside the box", in the render_common module so `cards` and (when
  its char-count caps get the same treatment) `hud` share one rule. It returns
  (font, text): containment is a property of the pair, so a caller cannot apply
  the font and forget the text. Sizes are unchanged everywhere, so every string
  that already fitted renders BYTE-IDENTICALLY — only clipped text changes.
* CLI — `--short` is length-bounded (`validate_max_len` + `MAX_SHORT_LEN`,
  ConfigError 6) because its help already promised "max ~10 chars" while nothing
  enforced anything (20 chars accepted): a documented limit with no rule behind
  it is m29's class. The limit feeds the help text, so the two cannot drift.
* `--note` deliberately gets NO length bound (locked below): the board row shows
  ~15 characters of a note at its fixed size 9, so any usable bound still
  permits truncating text while rejecting legitimate prose the loader renders
  from JSON anyway. The ellipsis is the honest mark; a bound would buy nothing.

Zero hardware, zero network, and no test depends on today's date.
"""

import ast
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw

from ak820 import cards, cli, coercion, configvalues, events, screen
from ak820.errors import ConfigError
from ak820.parser import build_parser
from ak820.screen import (
    ELLIPSIS,
    FONT_BOLD_PATHS,
    FONT_NARROW_PATHS,
    _load_font,
    ellipsize,
    fit_text,
    text_width,
)

# The exact overflowing note from the v0.33.0 live probes (66 chars, 288 px).
LONG_NOTE = "The quick brown fox jumps over the lazy dog and keeps running far"[:66]
FIXED_TODAY = datetime.date(2026, 10, 25)
BOARD_BG = (6, 9, 16)


def _main(argv):
    """cli.main(argv) capturing stdout+stderr; SystemExit code becomes the rc."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cli.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


def _row(milestone_str, short="DAUGHTER", days=37, theme="calm"):
    """A countdown row shaped exactly like compute_upcoming_events' output."""
    return {
        "id": short.lower(),
        "title": short.title(),
        "short_title": short,
        "orig_date": "12-01",
        "next_date": FIXED_TODAY + datetime.timedelta(days=days),
        "date_label": (FIXED_TODAY + datetime.timedelta(days=days)).strftime("%b %d"),
        "days_left": days,
        "years": 5,
        "category": "milestone",
        "milestone_str": milestone_str,
        "short_age": milestone_str,
        "theme_key": theme,
    }


def _legacy_fit(draw, text, paths, max_size, min_size, max_width):
    """The pre-v0.34 algorithm, kept here so parity is proven not asserted."""
    for sz in range(max_size, min_size - 1, -1):
        font = _load_font(paths, sz)
        if text_width(draw, text, font) <= max_width:
            return sz, text
    return min_size, text


class _DrawFixture(unittest.TestCase):
    def setUp(self):
        self.draw = ImageDraw.Draw(Image.new("RGB", (128, 128)))


class TestFitTextContract(_DrawFixture):
    """fit_text: shrink first, mark only when shrinking cannot save the text."""

    def test_fits_at_max_size_is_untouched(self):
        font, drawn = fit_text(self.draw, "DAUGHTER", FONT_NARROW_PATHS, 12, 9, 102)
        self.assertEqual(drawn, "DAUGHTER")
        self.assertEqual(font.size, 12)

    def test_shrinks_before_marking(self):
        """Shrinking is tried first: a mark only appears when no size can save it."""
        text = "GRANDMOTHER"
        # Budget derived from the font that actually loaded, so the contract
        # holds on any runner (Liberation, DejaVu, or PIL default fallback):
        # strictly between the size-12 width (shrinking is needed) and the
        # size-9 width (shrinking can save it without a mark).
        f12 = _load_font(FONT_NARROW_PATHS, 12)
        f9 = _load_font(FONT_NARROW_PATHS, 9)
        w12 = text_width(self.draw, text, f12)
        w9 = text_width(self.draw, text, f9)
        budget = (w12 + w9) / 2 if w12 > w9 else w9
        too_wide = w12
        self.assertGreater(too_wide, budget, "fixture must actually need shrinking")
        font, drawn = fit_text(self.draw, text, FONT_NARROW_PATHS, 12, 9, budget)
        self.assertEqual(drawn, text, "shrinking must save the text before any ellipsis")
        self.assertLess(font.size, 12)
        self.assertLessEqual(text_width(self.draw, drawn, font), budget)

    def test_overflow_returns_min_size_and_a_mark(self):
        font, drawn = fit_text(self.draw, LONG_NOTE, FONT_BOLD_PATHS, 9, 9, 112)
        self.assertEqual(font.size, 9)
        self.assertTrue(drawn.endswith(ELLIPSIS), drawn)
        self.assertLess(len(drawn), len(LONG_NOTE))
        self.assertLessEqual(text_width(self.draw, drawn, font), 112)

    def test_every_input_stays_inside_the_box(self):
        """The containment invariant, table-driven over widths and texts."""
        texts = ["", "x", "Dec 01 • Turns 5", LONG_NOTE, LONG_NOTE * 4, "W" * 200, "a b c d e f g h"]
        widths = [0, 1, 8, 40, 112, 128]
        for text in texts:
            for width in widths:
                with self.subTest(text=text[:18], width=width):
                    font, drawn = fit_text(self.draw, text, FONT_BOLD_PATHS, 11, 8, width)
                    self.assertLessEqual(text_width(self.draw, drawn, font), width)

    def test_parity_with_the_legacy_fitter_when_text_fits(self):
        """Nothing that used to render changes: same size, same text (m30 guard)."""
        cases = [
            ("DAUGHTER", FONT_NARROW_PATHS, 16, 11, 116),
            ("Dec 01 • Turns 5", FONT_BOLD_PATHS, 11, 9, 116),
            ("NEXT: SON (3d)", FONT_BOLD_PATHS, 10, 8, 116),
            ("27 DAYS", FONT_BOLD_PATHS, 24, 13, 108),
            ("FAMILY", FONT_BOLD_PATHS, 10, 10, 39),
        ]
        for text, paths, mx, mn, w in cases:
            with self.subTest(text=text):
                font, drawn = fit_text(self.draw, text, paths, mx, mn, w)
                size, legacy_text = _legacy_fit(self.draw, text, paths, mx, mn, w)
                self.assertEqual(font.size, size)
                self.assertEqual(drawn, legacy_text)

    def test_marker_survives_at_every_catalog_size(self):
        """The ellipsis is only a mark if the drawn font actually has the glyph."""
        for paths in (FONT_BOLD_PATHS, FONT_NARROW_PATHS):
            for size in (8, 9, 11, 13):
                font = _load_font(paths, size)
                with self.subTest(size=size):
                    self.assertGreater(text_width(self.draw, ELLIPSIS, font), 0)


class TestEllipsize(_DrawFixture):
    def test_text_that_fits_is_returned_verbatim(self):
        font = _load_font(FONT_BOLD_PATHS, 9)
        self.assertEqual(ellipsize(self.draw, "Turns 5", font, 112), "Turns 5")

    def test_empty_text(self):
        font = _load_font(FONT_BOLD_PATHS, 9)
        self.assertEqual(ellipsize(self.draw, "", font, 112), "")

    def test_box_too_small_for_the_mark_draws_nothing(self):
        font = _load_font(FONT_BOLD_PATHS, 9)
        self.assertEqual(ellipsize(self.draw, "long text here", font, 1), "")

    def test_trailing_space_is_not_left_before_the_mark(self):
        font = _load_font(FONT_BOLD_PATHS, 9)
        drawn = ellipsize(self.draw, "word " * 40, font, 60)
        self.assertFalse(drawn.endswith(" " + ELLIPSIS))
        self.assertTrue(drawn.endswith(ELLIPSIS))

    def test_result_never_exceeds_the_box_for_any_cut(self):
        font = _load_font(FONT_BOLD_PATHS, 9)
        for width in range(2, 130, 7):
            for text in (LONG_NOTE, "W" * 90, "i i i i i i i i i i i i i i i i i i"):
                with self.subTest(width=width, text=text[:12]):
                    drawn = ellipsize(self.draw, text, font, width)
                    self.assertLessEqual(text_width(self.draw, drawn, font), width)

    def test_keeps_more_than_the_minimum(self):
        """A greedy cut would keep 0 chars; the search must keep the most that fit."""
        font = _load_font(FONT_BOLD_PATHS, 9)
        drawn = ellipsize(self.draw, LONG_NOTE, font, 112)
        self.assertGreater(len(drawn), 5)


class TestBoardCardContainment(unittest.TestCase):
    """The m30 defect itself: the row meta line, measured in pixels."""

    # note lengths: short / edge (fits) / overflowing
    NOTES = ["Turns 5", "Anniversary", "Move day {years}".format(years=7), LONG_NOTE, LONG_NOTE * 3]

    def _meta_band(self, img, row_index):
        y0 = 22 + row_index * 35
        return range(y0 + 17, y0 + 30)

    def test_nothing_is_drawn_outside_the_row_box(self):
        for note in self.NOTES:
            with self.subTest(note=note[:20]):
                img = cards.render_board_card([_row(note)], today=FIXED_TODAY)
                stray = [
                    (x, y)
                    for y in self._meta_band(img, 0)
                    for x in (124, 125)
                    if img.getpixel((x, y)) != BOARD_BG
                ]
                self.assertEqual(stray, [], "text drawn past the row box")

    def test_text_is_actually_drawn(self):
        """Guards the containment test above against passing vacuously."""
        for note in self.NOTES:
            with self.subTest(note=note[:20]):
                img = cards.render_board_card([_row(note)], today=FIXED_TODAY)
                painted = any(
                    img.getpixel((x, y)) != BOARD_BG
                    for y in self._meta_band(img, 0)
                    for x in range(11, 124)
                )
                self.assertTrue(painted)

    def test_overflowing_note_is_marked_not_amputated(self):
        """The pre-fix render had no mark: 288 px drawn, the tail simply gone."""
        row = _row(LONG_NOTE)
        meta = f"{row['date_label']} • {row['milestone_str']}"
        legacy_font = _load_font(FONT_BOLD_PATHS, 9)
        probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        self.assertGreater(text_width(probe, meta, legacy_font), 128, "not an overflow case")
        img = cards.render_board_card([row], today=FIXED_TODAY)
        # the widest row of the meta band must stop at the box edge
        rightmost = max(
            x
            for y in self._meta_band(img, 0)
            for x in range(128)
            if img.getpixel((x, y)) != BOARD_BG and x < 126
        )
        self.assertLessEqual(rightmost, 123)

    def test_all_three_rows_are_contained(self):
        rows = [_row(LONG_NOTE, "AAAA"), _row(LONG_NOTE * 2, "BBBB"), _row("ok", "CCCC", days=0)]
        img = cards.render_board_card(rows, today=FIXED_TODAY)
        for idx in range(3):
            with self.subTest(row=idx):
                stray = [
                    (x, y)
                    for y in self._meta_band(img, idx)
                    for x in (124, 125)
                    if img.getpixel((x, y)) != BOARD_BG
                ]
                self.assertEqual(stray, [])

    def test_long_short_label_stays_left_of_the_days_pill(self):
        """Hand-edited long short_titles (loader stays permissive) must not collide."""
        img = cards.render_board_card([_row("Turns 5", short="A" * 30)], today=FIXED_TODAY)
        # the days pill occupies the right end of the name band; the gutter past
        # the row box must stay clean and the name must not reach the screen edge
        y0 = 22
        stray = [
            (x, y)
            for y in range(y0 + 2, y0 + 15)
            for x in (124, 125)
            if img.getpixel((x, y)) != BOARD_BG
        ]
        self.assertEqual(stray, [])


class TestHeroAndCustomContainment(unittest.TestCase):
    """The queue asked whether the hero needed a bound; the probe says the hero
    needed CONTAINMENT (it overflowed by 199 px at min size), not a bound."""

    def _extent(self, img, band, lo=2, hi=125):
        xs = [
            x
            for y in band
            for x in range(lo, hi + 1)
            if img.getpixel((x, y)) != img.getpixel((0, y))
        ]
        return (min(xs), max(xs)) if xs else None

    def test_hero_subtitle_stays_inside_the_card(self):
        for note in ("Turns 5", LONG_NOTE, LONG_NOTE * 3):
            with self.subTest(note=note[:20]):
                img = cards.render_hero_card([_row(note)], today=FIXED_TODAY)
                extent = self._extent(img, range(89, 103))
                self.assertIsNotNone(extent)
                self.assertGreaterEqual(extent[0], 2)
                self.assertLessEqual(extent[1], 125)

    def test_hero_ticker_and_title_stay_inside(self):
        rows = [_row("Turns 5", "A" * 40), _row(LONG_NOTE, "B" * 40, days=3)]
        img = cards.render_hero_card(rows, today=FIXED_TODAY)
        for band, label in ((range(21, 40), "title"), (range(108, 122), "ticker")):
            with self.subTest(part=label):
                extent = self._extent(img, band)
                self.assertIsNotNone(extent)
                self.assertLessEqual(extent[1], 125)

    def test_custom_card_value_sub_footer_stay_inside(self):
        img = cards.render_custom_card(
            "REMINDER " * 6, "X" * 40, "sub " * 30, "footer " * 20, "soon"
        )
        for band, label in (
            (range(6, 19), "title"),
            (range(28, 76), "value"),
            (range(85, 99), "sub"),
            (range(109, 121), "footer"),
        ):
            with self.subTest(part=label):
                xs = [
                    x
                    for y in band
                    for x in range(2, 126)
                    if img.getpixel((x, y)) != img.getpixel((0, y))
                ]
                self.assertTrue(xs, f"{label} drew nothing at all")
                self.assertLessEqual(max(xs), 125)
                self.assertGreaterEqual(min(xs), 2)

    def test_empty_upcoming_still_renders(self):
        img = cards.render_hero_card([], today=FIXED_TODAY)
        self.assertEqual(img.size, (128, 128))


class TestShortLengthBound(unittest.TestCase):
    """m30's CLI half: --short is bounded, and the bound is the documented one."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = os.path.join(self.tmp, "events.json")
        with open(self.cfg, "w", encoding="utf-8") as f:
            json.dump({"events": []}, f)

    def _add(self, short, **kw):
        argv = ["events", "add", "--title", "T", "--short", short, "--date", "05-01",
                "--config", self.cfg]
        if kw.get("note"):
            argv += ["--note", kw["note"]]
        return _main(argv)

    def test_limit_is_the_documented_one(self):
        self.assertEqual(configvalues.MAX_SHORT_LEN, 12)

    def test_lengths_up_to_the_limit_are_accepted(self):
        for n in (1, 8, 11, configvalues.MAX_SHORT_LEN):
            with self.subTest(length=n):
                rc, out, err = self._add("S" * n)
                self.assertEqual(rc, 0, out + err)

    def test_over_limit_is_rejected_naming_the_limit(self):
        for n in (configvalues.MAX_SHORT_LEN + 1, 20, 60):
            with self.subTest(length=n):
                before = open(self.cfg, "rb").read()
                rc, out, err = self._add("S" * n)
                self.assertEqual(rc, 6)
                self.assertIn("12", out + err)
                self.assertIn("--short", out + err)
                self.assertNotIn("Traceback", out + err)
                self.assertEqual(open(self.cfg, "rb").read(), before, "config was rewritten")

    def test_padding_does_not_buy_extra_length(self):
        rc, _, _ = self._add(" " + "S" * 13 + " ")
        self.assertEqual(rc, 6)

    def test_stripped_label_is_what_gets_stored(self):
        rc, _, _ = self._add("  DAUGHTER  ")
        self.assertEqual(rc, 0)
        cfg = json.load(open(self.cfg, encoding="utf-8"))
        self.assertEqual(cfg["events"][0]["id"], "daughter")
        self.assertEqual(cfg["events"][0]["short_title"], "DAUGHTER")

    def test_note_is_deliberately_unbounded(self):
        """The recorded decision: prose is not length-capped; the renderer marks it."""
        rc, out, err = self._add("SON", note=LONG_NOTE)
        self.assertEqual(rc, 0, out + err)
        cfg = json.load(open(self.cfg, encoding="utf-8"))
        self.assertEqual(cfg["events"][0]["note"], LONG_NOTE)

    def test_title_is_not_length_bounded(self):
        rc, _, err = _main(
            ["events", "add", "--title", "T" * 200, "--short", "OK", "--date", "05-01",
             "--config", self.cfg]
        )
        self.assertEqual(rc, 0)


class TestLoaderStaysPermissive(unittest.TestCase):
    """Trust boundary (m27/m28/m29 class): the CLI refuses, the loader renders."""

    def test_hand_edited_long_short_title_loads_and_renders(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "events.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"events": [{"id": "x", "title": "X", "short_title": "Z" * 40,
                             "date": "12-01", "category": "milestone"}]},
                f,
            )
        cfg = events.load_events_config(path)  # must NOT raise
        rows = events.compute_upcoming_events(cfg, today=FIXED_TODAY)
        self.assertEqual(rows[0]["short_title"], "Z" * 40)
        img = cards.render_board_card(rows, today=FIXED_TODAY)
        stray = [
            (x, y)
            for y in range(24, 37)
            for x in (124, 125)
            if img.getpixel((x, y)) != BOARD_BG
        ]
        self.assertEqual(stray, [])

    def test_validate_max_len_returns_the_value(self):
        self.assertEqual(configvalues.validate_max_len("abc", "--short", 3), "abc")

    def test_validate_max_len_names_flag_and_limit(self):
        with self.assertRaises(ConfigError) as ctx:
            configvalues.validate_max_len("abcd", "--short 'abcd'", 3)
        msg = str(ctx.exception)
        self.assertIn("4 chars", msg)
        self.assertIn("3", msg)


class TestOneHomeInvariants(unittest.TestCase):
    """Anti-recurrence: the containment rule has one home and cannot be skipped."""

    # Dynamic strings exempt from fit_text because their width is bounded by
    # construction (the countdown numerics), documented in cards.py.
    EXEMPT_NAMES = {"num_str", "unit_str", "days_s"}

    def _fitted_names(self, fn):
        names = set()
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and getattr(node.value.func, "id", None) == "fit_text"
            ):
                for target in node.targets:
                    if isinstance(target, ast.Tuple):
                        names.update(e.id for e in target.elts if isinstance(e, ast.Name))
        return names

    def _literal_names(self, fn):
        """Names bound to a plain string literal — static text, not dynamic."""
        names = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        return names

    def _fixed_format(self, arg):
        """Fixed-format, fixed-width date text — the v0.34 exemption class.

        v0.38.0 extended it: `%b` strftime sites became `format_date_label`
        (the C-locale month table, 3-char month + 2-digit day = bounded by
        construction exactly like the strftime it replaced), including the
        f-string that embeds only such calls (`f"TODAY: {format_date_label(...)}"`).
        """
        def _is_fixed(call):
            return getattr(call.func, "attr", None) == "strftime" or getattr(
                call.func, "id", None
            ) == "format_date_label"

        if isinstance(arg, ast.Call):
            return _is_fixed(arg)
        if isinstance(arg, ast.JoinedStr):
            # literal parts are static text; every DYNAMIC part must be fixed-format
            return all(
                isinstance(v, ast.Constant)
                or (
                    isinstance(v, ast.FormattedValue)
                    and isinstance(v.value, ast.Call)
                    and _is_fixed(v.value)
                )
                for v in arg.values
            )
        return False

    def _unfitted_draws(self, source, func_name):
        tree = ast.parse(source)
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func_name)
        fitted = self._fitted_names(fn)
        literals = self._literal_names(fn)
        offenders = []
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "text"):
                continue
            # draw.text(xy, text, ...) — the string is the SECOND positional arg
            arg = next(
                (kw.value for kw in node.keywords if kw.arg == "text"),
                node.args[1] if len(node.args) > 1 else None,
            )
            if arg is None:
                continue
            if isinstance(arg, ast.Constant):  # literal text
                continue
            if self._fixed_format(arg):
                continue  # fixed format, fixed width
            name = arg.id if isinstance(arg, ast.Name) else None
            if name in fitted or name in self.EXEMPT_NAMES or name in literals:
                continue
            offenders.append((func_name, node.lineno, ast.dump(arg)[:40]))
        return offenders

    def test_every_dynamic_draw_is_fitted(self):
        src = (ROOT / "ak820" / "cards.py").read_text(encoding="utf-8")
        for func in ("render_hero_card", "render_board_card", "render_custom_card"):
            with self.subTest(func=func):
                self.assertEqual(self._unfitted_draws(src, func), [])

    def test_the_board_meta_line_is_fitted(self):
        """The exact line that caused m30, locked against regression."""
        src = (ROOT / "ak820" / "cards.py").read_text(encoding="utf-8")
        self.assertIn("f_meta, meta_s = fit_text(", src)
        self.assertIn("BOARD_META_W", src)
        self.assertNotIn("_load_font(FONT_BOLD_PATHS, 9)", src)

    def test_the_old_fitter_is_gone(self):
        for path in (ROOT / "ak820" / "cards.py", ROOT / "ak820" / "screen.py"):
            self.assertNotIn("_fit_text_font", path.read_text(encoding="utf-8"), path.name)
        self.assertFalse(hasattr(cards, "_fit_text_font"))
        self.assertFalse(hasattr(screen, "_fit_text_font"))

    def test_containment_lives_in_the_render_common_module(self):
        import inspect

        self.assertIn("def fit_text", inspect.getsource(screen))
        self.assertIn("def ellipsize", inspect.getsource(screen))
        # cards imports it rather than re-implementing it
        src = (ROOT / "ak820" / "cards.py").read_text(encoding="utf-8")
        self.assertIn("    fit_text,\n", src)

    def test_shim_reexports_the_new_primitives(self):
        import milestone_radar

        self.assertIs(milestone_radar.fit_text, screen.fit_text)
        self.assertIs(milestone_radar.ellipsize, screen.ellipsize)

    def test_help_text_and_enforcement_share_one_constant(self):
        src = (ROOT / "ak820" / "parser.py").read_text(encoding="utf-8")
        self.assertIn("max {MAX_SHORT_LEN} chars", src)
        self.assertNotIn("max ~10 chars", src, "the help text drifted from the rule")
        actions = {}
        for action in build_parser()._subparsers._group_actions[0].choices["events"] \
                ._subparsers._group_actions[0].choices["add"]._actions:
            for flag in action.option_strings:
                actions[flag] = action
        self.assertIn(f"max {configvalues.MAX_SHORT_LEN} chars", actions["--short"].help)


class TestCoercionSplitStillWorks(unittest.TestCase):
    """v0.34.0 moved the loader coercion to ak820.coercion; behavior is identical."""

    def test_coerce_config_values_signature_and_behavior(self):
        cfg = {"events": [{"title": 123}], "backlight": {"idle_rainbow": "false"}}
        out = coercion.coerce_config_values(cfg, "x")
        self.assertEqual(out["events"][0]["title"], "123")
        self.assertIs(out["backlight"]["idle_rainbow"], False)

    def test_events_load_still_coerces(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "events.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"events": [{"title": 123, "date": "12-01"}]}, f)
        cfg = events.load_events_config(path)
        self.assertEqual(cfg["events"][0]["title"], "123")

    def test_no_import_cycle(self):
        code = (
            "import sys; sys.path.insert(0, %r);"
            "import ak820.coercion, ak820.configvalues, ak820.events, ak820.screen, ak820.cards;"
            "print('ok')" % str(ROOT)
        )
        import subprocess

        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ok", proc.stdout)


if __name__ == "__main__":
    unittest.main()
