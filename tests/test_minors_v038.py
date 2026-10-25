"""v0.38.0 — the glyph audit's three remaining edges, queued by v0.37.0.

Re-measured live before fixing:
* Pillow's `load_default` face (the last-resort `_load_font` fallback) draws
  `•` (U+2022) with a mask byte-identical to its `.notdef` — tofu — while
  `…`/`°` happen to render (a Pillow-version accident, never a promise).
* `%b` is locale-dependent DRAWN text (board header, hero ticker, every
  `date_label`); no locale installed on this runner emits non-ASCII months,
  so the lock is structural (a fixed table + an AST ban), not a locale repro.
* User text (MPRIS titles/artists, hand-authored notes) can hold any
  codepoint and tofus exactly like the old header tags did.

DECIDED (v0.38.0):
1. Typed startup error, not a silent degrade: `FontMissingError` (exit 10).
   The fallback is gone, so the allow-list promise now covers EVERY font
   `_load_font` can return. (An ASCII separator would fix only `•` and move
   every production render for a machine that is not broken; a "guaranteed
   face" does not exist on arbitrary Linux — Pillow's own lacks `•`.)
2. Fixed C-locale month table in the new PIL-free leaf `ak820/dates.py` —
   one home (`cards` may not import `events`; `events` must stay PIL-free),
   byte-stable ASCII output; `%b` is banned from shipped string literals.
3. NO render-seam substitution for user text — the `.notdef` box IS the
   visible mark; a "?" mark would be ambiguous with a real "?" and a
   per-glyph probe in the `--watch` hot path buys no information. Locked
   behaviorally: exotic codepoints reach the layout seam byte-identical and
   containment still measures (tofu glyphs have width).

Zero hardware, zero network; no test depends on today's date.
"""

import ast
import contextlib
import io
import locale
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont

import ak820
import ak820_ctrl
from ak820 import cards, cli, dates, errors, events, hud, screen
from ak820.errors import AK820Error, FontMissingError
from ak820.screen import ELLIPSIS, FONT_BOLD_PATHS, fit_text, flatten_lines, text_width

FIXTURE_TODAY = date(2026, 10, 24)
FIXTURE_CFG = {
    "events": [
        {
            "id": "bday",
            "title": "Birthday",
            "short_title": "BDAY",
            "date": "1990-10-26",
            "category": "birthday",
        }
    ]
}


def _main(argv):
    """cli.main(argv) capturing stdout+stderr; SystemExit code becomes the rc."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cli.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


def _string_constants(rel_path: str) -> set:
    """Every non-docstring string literal in a module (f-string parts included).

    Docstrings are excluded (prose may NAME the pattern a lock forbids — the
    v0.37 precedent): `dates.py`'s docstring explains what `%b` is by writing
    it; nothing there is drawn.
    """
    tree = ast.parse((ROOT / rel_path).read_text(encoding="utf-8"))
    docs: set = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docs.add(id(body[0].value))
    return {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs
    }


class TestFontMissingErrorTaxonomy(unittest.TestCase):
    """The new exit-10 class joins the §3.4 contract like D11/D12 did."""

    def test_class_shape(self):
        self.assertTrue(issubclass(FontMissingError, AK820Error))
        self.assertEqual(FontMissingError.exit_code, 10)

    def test_main_maps_it_to_exit_10(self):
        with mock.patch.object(cli, "_dispatch", side_effect=FontMissingError("boom")), \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["probe"])
        self.assertEqual(ctx.exception.code, 10)

    def test_reexport_identity_across_surfaces(self):
        self.assertIs(ak820.FontMissingError, errors.FontMissingError)
        self.assertIs(ak820_ctrl.FontMissingError, errors.FontMissingError)


class TestLastResortFontIsRefused(unittest.TestCase):
    """The fallback that sat outside the lock is gone — every form of it."""

    def test_missing_paths_raise_naming_the_fix(self):
        for paths in ([], ["/nonexistent/ak820-test-font.ttf"]):
            with self.subTest(paths=paths):
                with self.assertRaises(FontMissingError) as ctx:
                    screen._load_font(paths, 12)
                msg = str(ctx.exception)
                self.assertIn("fonts-liberation", msg)
                self.assertIn("fonts-dejavu-core", msg)
                for p in paths:
                    self.assertIn(p, msg)

    def test_corrupt_font_raises_not_falls_back(self):
        """A catalog path that EXISTS but is not a font must not resurrect the
        silent degrade either — the old loop `continue`d and fell through."""
        fd, path = tempfile.mkstemp(suffix=".ttf")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(b"definitely not a TrueType font")
            with self.assertRaises(FontMissingError):
                screen._load_font([path], 12)
        finally:
            os.unlink(path)

    def test_catalog_font_still_loads(self):
        """Non-vacuity: the refusal did not break the happy path."""
        if not [p for p in FONT_BOLD_PATHS if os.path.exists(p)]:
            self.skipTest("no catalog font on this runner")
        font = screen._load_font(FONT_BOLD_PATHS, 12)
        self.assertIsInstance(font, ImageFont.FreeTypeFont)

    def test_screen_source_has_no_load_default(self):
        """The fallback is structurally dead, not merely unused today.

        AST level (the v0.37 extractor precedent): prose may NAME the dead
        call — the comment above it explains why it is gone — but no CODE in
        `screen.py` may reach it again."""
        tree = ast.parse((ROOT / "ak820" / "screen.py").read_text(encoding="utf-8"))
        reached = [
            n
            for n in ast.walk(tree)
            if (isinstance(n, ast.Attribute) and n.attr == "load_default")
            or (isinstance(n, ast.Name) and n.id == "load_default")
        ]
        self.assertEqual(reached, [])

    def test_cli_render_exits_10_not_a_traceback(self):
        """End-to-end wiring: a font-less runner gets the friendly typed exit
        from every render command (dry-run proves it with zero USB)."""
        with mock.patch(
            "ak820.screen._load_font_cached",
            side_effect=FontMissingError("no catalog font on this runner"),
        ):
            rc, out, err = _main(["--dry-run", "hud"])
        self.assertEqual(rc, 10, out + err)
        self.assertIn("no catalog font", err)
        self.assertNotIn("Traceback", err)


class TestMonthTableSingleHome(unittest.TestCase):
    """`%b` replaced by one C-locale table — byte-stable drawn text."""

    def setUp(self):
        self.saved_locale = locale.setlocale(locale.LC_TIME)
        locale.setlocale(locale.LC_TIME, "C")

    def tearDown(self):
        locale.setlocale(locale.LC_TIME, self.saved_locale)

    def test_table_equals_c_locale_months(self):
        self.assertEqual(len(dates.MONTH_ABBREVS), 12)
        for m in range(1, 13):
            with self.subTest(month=m):
                self.assertEqual(date(2026, m, 1).strftime("%b"), dates.MONTH_ABBREVS[m - 1])

    def test_format_equals_c_locale_strftime(self):
        days = [(2026, m, d) for m in range(1, 13) for d in (1, 7, 14, 28)]
        days += [(2028, 2, 29), (2026, 1, 31), (2026, 4, 30), (2026, 12, 31)]
        for y, m, d in days:
            dt = date(y, m, d)
            with self.subTest(date=str(dt)):
                self.assertEqual(dates.format_date_label(dt), dt.strftime("%b %d"))

    def test_output_always_ascii_and_zero_padded(self):
        for m in range(1, 13):
            label = dates.format_date_label(date(2026, m, 5))
            self.assertTrue(label.isascii(), label)
            self.assertEqual(label, f"{dates.MONTH_ABBREVS[m - 1]} 05")

    def test_no_percent_b_in_shipped_literals(self):
        """The locale-dependent spec is banned from every package module.

        Checked against string LITERALS, not raw text — the comments and
        docstrings that record WHY `%b` is gone name it on purpose (v0.37's
        extractor precedent); prose is never drawn.
        """
        for py in sorted((ROOT / "ak820").glob("*.py")):
            rel = f"ak820/{py.name}"
            for literal in _string_constants(rel):
                with self.subTest(module=rel, literal=literal):
                    self.assertNotIn("%b", literal)

    def test_consumers_call_the_table(self):
        cards_src = (ROOT / "ak820" / "cards.py").read_text(encoding="utf-8")
        events_src = (ROOT / "ak820" / "events.py").read_text(encoding="utf-8")
        # import + exactly the three former strftime sites
        self.assertEqual(cards_src.count("format_date_label"), 4)
        self.assertIn("dates.format_date_label(next_date)", events_src)

    def test_dates_is_a_pil_free_leaf(self):
        src = (ROOT / "ak820" / "dates.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"^\s*(from PIL import|import PIL)", src, re.M))
        tree = ast.parse(src)
        imported: set = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, "dates must import nothing from the package")
                if node.module:
                    imported.add(node.module)
        self.assertEqual(imported, {"datetime"})

    def test_fresh_interpreter_no_cycle(self):
        code = (
            f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
            "import ak820.dates, ak820.events, ak820.cards, ak820.hud; print('ok')"
        )
        res = subprocess.run([sys.executable, "-I", "-c", code], cwd=ROOT,
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("ok", res.stdout)

    def test_date_label_rows_use_the_table(self):
        upcoming = events.compute_upcoming_events(FIXTURE_CFG, today=FIXTURE_TODAY)
        self.assertTrue(upcoming)
        for u in upcoming:
            with self.subTest(event=u["id"]):
                self.assertEqual(u["date_label"], dates.format_date_label(u["next_date"]))
                self.assertTrue(u["date_label"].isascii())

    def test_month_names_are_in_the_v037_probe_matrix(self):
        """The month abbreviations are DRAWN text living outside the scanned
        renderer modules — v0.37's FIXED_DISPLAY_STRINGS is the designated
        mechanism for exactly that, so the table must appear there."""
        src = (ROOT / "tests" / "test_minors_v037.py").read_text(encoding="utf-8")
        for abbr in dates.MONTH_ABBREVS:
            with self.subTest(abbr=abbr):
                self.assertIn(f'"{abbr}"', src)


class TestUserTextPassesThrough(unittest.TestCase):
    """The recorded boundary: user text is never rewritten at the render seam."""

    EXOTIC = "\U0001f525 東京 Café"
    LONG = ("\U0001f525 東京 Café naïve — ") * 12

    def setUp(self):
        self.draw = ImageDraw.Draw(Image.new("RGB", (128, 128)))

    def test_fit_text_returns_fitting_exotic_verbatim(self):
        _font, text = fit_text(self.draw, self.EXOTIC, FONT_BOLD_PATHS, 12, 9, 2000)
        self.assertEqual(text, self.EXOTIC)

    def test_flatten_lines_touches_only_line_breaks(self):
        self.assertEqual(flatten_lines("\U0001f525\n東京"), "\U0001f525 東京")
        self.assertEqual(flatten_lines(self.EXOTIC), self.EXOTIC)

    def test_overflowing_exotic_is_marked_not_amputated(self):
        """Containment is codepoint-blind: tofu boxes have width, so the
        measured fit still marks the tail instead of losing it silently."""
        font, text = fit_text(self.draw, self.LONG, FONT_BOLD_PATHS, 12, 8, 100)
        self.assertTrue(text.endswith(ELLIPSIS), text)
        self.assertLessEqual(text_width(self.draw, text, font), 100)

    def test_tofu_glyphs_have_width(self):
        """Vacuity guard for the containment argument above: an unmappable
        codepoint still measures > 0 px, so fit math cannot ignore it."""
        font = screen._load_font(FONT_BOLD_PATHS, 12)
        self.assertGreater(text_width(self.draw, "\U0001f525", font), 0)

    def test_media_renderer_hands_the_raw_title_to_layout(self):
        """No substitution between the MPRIS dict and the wrap: the layout
        seam receives the exact codepoints it was given."""
        title = "\U0001f525 東京"
        real = hud._wrap_media_title
        seen: list = []

        def spy(draw, text, font, budget):
            seen.append(text)
            return real(draw, text, font, budget)

        with mock.patch.object(hud, "_wrap_media_title", side_effect=spy):
            img = hud.render_media_card(
                {"status": "Playing", "title": title, "artist": "", "album": ""}
            )
        self.assertEqual(seen, [title])
        # non-vacuous: the title band actually drew ink (boxes or glyphs)
        bg = (12, 10, 18)
        ink = [
            (x, y)
            for y in range(26, 70)
            for x in range(8, 127)
            if img.getpixel((x, y)) != bg
        ]
        self.assertTrue(ink, "the exotic title drew nothing at all")

    def test_boundary_is_documented_where_it_lives(self):
        src = (ROOT / "ak820" / "hud.py").read_text(encoding="utf-8")
        self.assertIn("user-authored text", src)
        self.assertIn("byte-for-byte", src)


class TestSourceInvariants(unittest.TestCase):
    """§10 budgets hold for the module this iteration added."""

    def test_dates_module_under_budget(self):
        n = len((ROOT / "ak820" / "dates.py").read_text(encoding="utf-8").splitlines())
        self.assertLess(n, 300, f"ak820/dates.py is {n} lines")


if __name__ == "__main__":
    unittest.main()
