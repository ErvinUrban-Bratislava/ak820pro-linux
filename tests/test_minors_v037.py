"""v0.37.0 — m32: the media header tags rendered TOFU on the production font.

Live repro (measured against the real fonts before the fix, `.notdef`-mask
comparison — a glyph is tofu when its rendered mask is byte-identical to the
font's `.notdef` glyph or to an unassigned PUA codepoint):

    LiberationSans-Bold — the FIRST catalog entry, installed on production and
    in CI (`apt-get install fonts-liberation fonts-dejavu-core`) — draws `▶` and
    `⏸` as its tofu box, so the media header read "□ PLAYING" / "□ PAUSED".
    NotoSans-Bold draws `▶`, `⏸` AND `♫` as a BLANK `.notdef`. DejaVuSans-Bold
    carries `▶`/`♫` but not `⏸`. NO catalog font carries `⏸` at all.

DECIDED (v0.37.0):
* Renderer — the tags become catalog-universal text: plain "PLAYING"/"PAUSED"/
  "MEDIA". Of the three options on the table, only this one cannot drift:
  (b) reordering the catalog cannot help a glyph NO catalog font has, and it
  would move every other render on the machine; (c) per-glyph fallback needs a
  font that carries the glyph to fall back TO, has none here, and would break
  `fit_text`'s "containment is a property of the (font, text) PAIR" contract.
  The state is not lost — the header COLOUR (emerald/amber/slate) and the bottom
  status bar already carry it.
* The rule that keeps it fixed is STATIC, not a font probe: every literal a
  renderer draws must be ASCII, except a named allow-list (`…`, `•`, `°`) whose
  members are themselves glyph-probed. A probe-only lock can only test the
  fonts installed on the runner; an ASCII-only rule needs no font at all, so a
  new literal can never be added that some font renders as tofu — the class v0.34
  closed for the ellipsis, now closed for every literal.
* Two minors found while probing, both fixed at their seam: `card` was the only
  render command without `--save-preview` (it renders through the SAME validation
  seam as live/--dry-run, so a refused card is never previewed as sendable), and
  `events list` printed a hand-edited multi-line note ragged across rows — the
  break is flattened at the PRINT seam only, never in the data (`--json` and the
  stored bytes keep it; the loader stays permissive, m27/m28).

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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont

from ak820 import cli, configvalues, hud, renderers, screen
from ak820.screen import (
    FONT_BOLD_PATHS,
    FONT_MONO_PATHS,
    FONT_NARROW_PATHS,
    FONT_REGULAR_PATHS,
    _load_font,
)

# ---------------------------------------------------------------------------
# The glyph probe (the technique that found m32, now a permanent lock)
# ---------------------------------------------------------------------------

CATALOGS = {
    "bold": FONT_BOLD_PATHS,
    "narrow": FONT_NARROW_PATHS,
    "mono": FONT_MONO_PATHS,
    "regular": FONT_REGULAR_PATHS,
}

_PROBE_SIZE = 32
_MASK_CACHE: dict = {}
_REF_CACHE: dict = {}


def _mask(ch: str, font) -> Image.Image:
    img = Image.new("L", (_PROBE_SIZE * 3, _PROBE_SIZE * 2), 0)
    ImageDraw.Draw(img).text((4, 4), ch, fill=255, font=font)
    return img


def _reference_masks(path: str) -> tuple:
    """The font's `.notdef` mask and its unassigned-PUA mask (the two tofu shapes)."""
    if path not in _REF_CACHE:
        font = ImageFont.truetype(path, _PROBE_SIZE)
        _REF_CACHE[path] = (_mask("\x00", font), _mask("\ue000", font), font)
    return _REF_CACHE[path]


def tofu_reason(ch: str, path: str) -> str | None:
    """None when `path` draws `ch` as a real glyph; the reason it is tofu otherwise."""
    notdef, pua, _ = _reference_masks(path)
    m = _MASK_CACHE.get((path, ch))
    if m is None:
        m = _mask(ch, ImageFont.truetype(path, _PROBE_SIZE))
        _MASK_CACHE[(path, ch)] = m
    if m.getbbox() is None:
        return "draws nothing (blank .notdef)"
    if m.tobytes() == notdef.tobytes():
        return "identical to .notdef (tofu box)"
    if m.tobytes() == pua.tobytes():
        return "identical to an unassigned PUA codepoint (tofu box)"
    return None


def installed_catalog_fonts() -> list:
    """Every catalog font this runner can actually load, as (catalog, path)."""
    return [(c, p) for c, paths in CATALOGS.items() for p in paths if os.path.exists(p)]


def assert_lock_has_fonts(case: unittest.TestCase) -> list:
    """The lock must never pass vacuously on a font-less runner."""
    fonts = installed_catalog_fonts()
    case.assertTrue(
        fonts,
        "no catalog font is installed — the glyph lock cannot prove anything here",
    )
    return fonts


# ---------------------------------------------------------------------------
# Literal extraction: every string a renderer draws, found in the source
# ---------------------------------------------------------------------------

# Modules whose string literals ARE display text (the 128x128 renderers and the
# theme tags they draw). `screen.py` is scanned for module-level constants only:
# its in-function literals are error messages and regex patterns, never drawn.
RENDERER_SOURCES = ("ak820/cards.py", "ak820/hud.py", "ak820/palette.py")
CONSTANT_SOURCES = ("ak820/screen.py",)

# The fixed vocabulary the renderers draw that lives OUTSIDE those modules —
# `compute_upcoming_events` builds these strings and cards.py draws them.
FIXED_DISPLAY_STRINGS = (
    "Birthday",
    "B-Day",
    "Anniversary",
    "Anniv",
    "Milestone",
    "Turns ",
    " Years",
    " yrs",
)

# Non-ASCII characters the renderers are allowed to draw, each with the home that
# needs it. Every member is glyph-probed against every installed catalog font by
# TestAsciiOnlyRule, so the list cannot accumulate a glyph some font lacks — and
# adding a character here is a deliberate, reviewable act, not an oversight.
ALLOWED_NON_ASCII = {
    "…",  # screen.ELLIPSIS — the truncation mark m30 introduced
    "•",  # cards.py hero header / board meta separator
    "°",  # hud.py temperature unit
}


def _docstring_ids(tree: ast.AST) -> set:
    """id()s of docstring constants — prose, never drawn, so never locked."""
    out = set()
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
            out.add(id(body[0].value))
    return out


def drawn_literals(rel_path: str, module_level_only: bool = False) -> set:
    """Every non-docstring string literal in a module (f-string parts included)."""
    tree = ast.parse((ROOT / rel_path).read_text(encoding="utf-8"))
    docs = _docstring_ids(tree)
    roots: list = (
        [n.value for n in tree.body if isinstance(n, ast.Assign)]  # type: ignore[attr-defined]
        if module_level_only
        else [tree]
    )
    found: set = set()
    for root in roots:
        for node in ast.walk(root):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docs
            ):
                found.add(node.value)
    return found


def all_drawn_literals() -> set:
    out: set = set()
    for rel in RENDERER_SOURCES:
        out |= drawn_literals(rel)
    for rel in CONSTANT_SOURCES:
        out |= drawn_literals(rel, module_level_only=True)
    return out | set(FIXED_DISPLAY_STRINGS)


def drawn_characters() -> set:
    """The character set the lock must prove out — whitespace excluded (it has no ink)."""
    return {ch for s in all_drawn_literals() for ch in s if not ch.isspace()}


def _main(argv):
    """cli.main(argv) capturing stdout+stderr; SystemExit code becomes the rc."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cli.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


class TestProbeHarnessIsNotVacuous(unittest.TestCase):
    """A lock that cannot fail is decoration: prove the probe sees real tofu."""

    def test_probe_flags_the_glyphs_that_caused_m32(self):
        liberation = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
        if not os.path.exists(liberation):
            self.skipTest("Liberation is the production font; not on this runner")
        self.assertIsNotNone(tofu_reason("▶", liberation))
        self.assertIsNotNone(tofu_reason("⏸", liberation))

    def test_probe_flags_pause_on_every_catalog_font(self):
        """`⏸` is in NO catalog font — the fact that ruled out a catalog reorder."""
        for catalog, path in assert_lock_has_fonts(self):
            with self.subTest(catalog=catalog, font=os.path.basename(path)):
                self.assertIsNotNone(
                    tofu_reason("⏸", path),
                    "the premise of the fix: no catalog font carries U+23F8",
                )

    def test_probe_passes_glyphs_every_font_has(self):
        for catalog, path in assert_lock_has_fonts(self):
            for ch in ("A", "0", "%", "…", "•", "°"):
                with self.subTest(font=os.path.basename(path), ch=ch):
                    self.assertIsNone(tofu_reason(ch, path))


class TestEveryDrawnLiteralHasAGlyph(unittest.TestCase):
    """The coverage lock m32 asked for: EVERY literal the renderers draw."""

    def test_lock_sees_the_strings_the_queue_named(self):
        """Guards the EXTRACTOR itself — a literal it silently misses is a hole."""
        literals = all_drawn_literals()
        for expected in (
            "PLAYING",
            "PAUSED",
            "MEDIA",
            "AK820 Pro Display",
            "AJAZZ AK820 PRO",
            "TEMP",
            "UPTIME",
            "CPU",
            "RAM",
            "TODAY!",
            "TOMORROW",
            "DAYS",
            "NOW PLAYING",
            "IDLE",
            "FAMILY RADAR",
            "N/A",
        ):
            with self.subTest(literal=expected):
                self.assertIn(expected, literals)

    def test_every_drawn_character_renders_in_every_catalog_font(self):
        chars = drawn_characters()
        self.assertGreater(len(chars), 40, "the extractor found almost nothing")
        fonts = assert_lock_has_fonts(self)
        for catalog, path in fonts:
            for ch in sorted(chars):
                with self.subTest(catalog=catalog, font=os.path.basename(path), ch=ch):
                    reason = tofu_reason(ch, path)
                    self.assertIsNone(
                        reason,
                        f"{os.path.basename(path)} renders {ch!r} "
                        f"(U+{ord(ch):04X}) as tofu: {reason}",
                    )

    def test_the_last_resort_font_draws_every_ascii_literal(self):
        """`_load_font` falls back to PIL's default when NO catalog path exists.

        That font is Pillow's own bundled face and it is NOT a catalog font, so
        the allow-list is not promised on it (measured: it has no `•`). What must
        hold on it is the part the rule is built on: every ASCII literal renders.
        """
        default = ImageFont.load_default(size=_PROBE_SIZE)
        notdef = _mask("\x00", default)
        for ch in sorted(c for c in drawn_characters() if ord(c) < 128):
            with self.subTest(ch=ch):
                m = _mask(ch, default)
                self.assertIsNotNone(m.getbbox(), f"default font draws {ch!r} blank")
                self.assertNotEqual(m.tobytes(), notdef.tobytes())


class TestAsciiOnlyRule(unittest.TestCase):
    """The static half: no renderer literal may need a font probe to be safe."""

    def test_renderer_literals_are_ascii_save_the_allow_list(self):
        for rel in (*RENDERER_SOURCES, *CONSTANT_SOURCES):
            src = (ROOT / rel).read_text(encoding="utf-8")
            for literal in drawn_literals(rel, module_level_only=rel in CONSTANT_SOURCES):
                for ch in literal:
                    if ord(ch) > 127:
                        with self.subTest(module=rel, ch=ch):
                            self.assertIn(ch, ALLOWED_NON_ASCII)
                self.assertNotIn("\n", literal.strip())  # a drawn literal is one line
            self.assertTrue(src)

    def test_allow_list_members_are_used_and_carried(self):
        used = {ch for s in all_drawn_literals() for ch in s if ord(ch) > 127}
        self.assertEqual(
            used,
            set(ALLOWED_NON_ASCII),
            "an allow-list entry nothing draws is a dead exemption",
        )
        for catalog, path in assert_lock_has_fonts(self):
            for ch in ALLOWED_NON_ASCII:
                with self.subTest(font=os.path.basename(path), ch=ch):
                    self.assertIsNone(tofu_reason(ch, path))

    def test_the_tofu_tags_are_gone_from_the_source(self):
        """The exact regression: the three glyph tags must never come back.

        Checked against the module's string LITERALS, not its raw text — the
        comment that records why the tags were removed names the glyphs on
        purpose, and prose is not drawn.
        """
        literals = drawn_literals("ak820/hud.py")
        for glyph in ("▶", "⏸", "♫"):
            with self.subTest(glyph=glyph):
                self.assertFalse(
                    any(glyph in lit for lit in literals),
                    f"{glyph} is back inside a drawn hud literal",
                )
        for word in ("PLAYING", "PAUSED", "MEDIA"):
            self.assertIn(word, literals)


class TestMediaHeaderRenders(unittest.TestCase):
    """Pixel proof on the real renderer, for all three media states."""

    STATES = (("Playing", "PLAYING"), ("Paused", "PAUSED"), ("Idle", "MEDIA"))
    HEADER_BG = {"Playing": (16, 185, 129), "Paused": (245, 158, 11), "Idle": (30, 41, 59)}

    def _media(self, status):
        return hud.render_media_card(
            {"status": status, "title": "Nocturne", "artist": "Chopin", "album": ""}
        )

    # The header band WITHOUT the outer border columns (x=0/x=127 and y=0 are the
    # card's own outline, not text), so "ink" means "the tag's pixels".
    _YS = range(1, 18)
    _XS = range(1, 127)

    def _ink(self, img, predicate):
        return {(x, y) for y in self._YS for x in self._XS if predicate(img.getpixel((x, y)))}

    def _header_ink(self, img, status):
        bg = self.HEADER_BG[status]
        return self._ink(img, lambda px: px != bg)

    def test_header_ink_is_exactly_the_expected_word(self):
        font = _load_font(FONT_BOLD_PATHS, 10)
        for status, word in self.STATES:
            with self.subTest(status=status):
                img = self._media(status)
                ref = Image.new("RGB", (128, 128), (0, 0, 0))
                ImageDraw.Draw(ref).text((6, 3), word, fill=(255, 255, 255), font=font)
                expected = self._ink(ref, lambda px: px != (0, 0, 0))
                self.assertEqual(self._header_ink(img, status), expected)

    def test_header_is_not_a_row_of_tofu_boxes(self):
        """Same length, same font, same place — `.notdef` must NOT match the render."""
        font = _load_font(FONT_BOLD_PATHS, 10)
        for status, word in self.STATES:
            with self.subTest(status=status):
                img = self._media(status)
                tofu = Image.new("RGB", (128, 128), (0, 0, 0))
                ImageDraw.Draw(tofu).text(
                    (6, 3), "\x00" * len(word), fill=(255, 255, 255), font=font
                )
                boxes = self._ink(tofu, lambda px: px != (0, 0, 0))
                # A font whose .notdef is BLANK makes `boxes` empty; either way the
                # render must not match it — "PLAYING" is not "□□□□□□□" (nor "      ").
                self.assertNotEqual(self._header_ink(img, status), boxes)

    def test_header_word_stays_inside_the_header_bar(self):
        """Containment with air: the tag ends well before the card's border."""
        for status, _word in self.STATES:
            with self.subTest(status=status):
                ink = self._header_ink(self._media(status), status)
                self.assertTrue(ink, "the header drew nothing at all")
                self.assertLessEqual(max(x for x, _y in ink), 120)
                self.assertGreaterEqual(min(x for x, _y in ink), 6)


class TestFlattenTwinEquivalence(unittest.TestCase):
    """One rule, two homes (the v0.36 precedent): CLI twin == render-side flattener."""

    SPELLINGS = [
        "a\nb",
        "a\r\nb",
        "a\rb",
        "a\vb",
        "a\fb",
        "a\x1cb",
        "a\x1db",
        "a\x1eb",
        "a\x85b",
        "a\u2028b",
        "a\u2029b",
        "line one\n\nline two",
        "  padded\nvalue  ",
        "multi \n spaces",
        "no break here",
        "",
        "\n",
        "plain",
    ]

    def test_twin_matches_screen_flatten_lines(self):
        for text in self.SPELLINGS:
            with self.subTest(text=repr(text)):
                self.assertEqual(
                    configvalues.flatten_for_display(text), screen.flatten_lines(text)
                )

    def test_regexes_are_still_one_pattern(self):
        self.assertEqual(configvalues._LINE_BREAK_RE.pattern, screen._LINE_BREAK_RE.pattern)

    def test_newline_free_text_is_byte_identical(self):
        for text in ("DAUGHTER", "Turns 5", "Oct 01 • Turns 5", ""):
            with self.subTest(text=text):
                self.assertEqual(configvalues.flatten_for_display(text), text)

    def test_flattening_is_idempotent_and_single_line(self):
        for text in self.SPELLINGS:
            once = configvalues.flatten_for_display(text)
            self.assertNotIn("\n", once)
            self.assertEqual(configvalues.flatten_for_display(once), once)


class TestEventsListDisplay(unittest.TestCase):
    """The ragged-row minor: flatten at the print seam, never in the data."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = os.path.join(self.tmp, "events.json")
        self._write(
            {
                "events": [
                    {
                        "id": "x",
                        "title": "Grandma\nVisit",
                        "short_title": "GRANDMA",
                        "date": "12-01",
                        "category": "milestone",
                        "note": "line one\nline two",
                    }
                ]
            }
        )

    def _write(self, cfg):
        with open(self.cfg, "w", encoding="utf-8") as f:
            json.dump(cfg, f)

    def _rows(self, out):
        """Table rows only (the header and the dash rule are not data rows)."""
        return [ln for ln in out.splitlines() if ln and not set(ln) == {"-"}]

    def test_multiline_note_and_title_stay_on_one_row(self):
        rc, out, err = _main(["events", "list", "--config", self.cfg])
        self.assertEqual(rc, 0, out + err)
        rows = self._rows(out)
        self.assertEqual(len(rows), 2, out)  # header + exactly one data row
        self.assertIn("Grandma Visit", rows[1])
        self.assertIn("line one line two", rows[1])

    def test_json_output_keeps_the_raw_stored_text(self):
        rc, out, err = _main(["events", "list", "--json", "--config", self.cfg])
        self.assertEqual(rc, 0, out + err)
        data = json.loads(out)
        self.assertEqual(data[0]["milestone_str"], "line one\nline two")
        self.assertEqual(data[0]["title"], "Grandma\nVisit")

    def test_listing_writes_nothing_back(self):
        before = Path(self.cfg).read_bytes()
        _main(["events", "list", "--config", self.cfg])
        self.assertEqual(Path(self.cfg).read_bytes(), before)

    def test_single_line_configs_render_as_before(self):
        self._write(
            {
                "events": [
                    {
                        "id": "d",
                        "title": "Daughter",
                        "short_title": "DAUGHTER",
                        "date": "12-01",
                        "category": "birthday",
                    }
                ]
            }
        )
        rc, out, err = _main(["events", "list", "--config", self.cfg])
        self.assertEqual(rc, 0, out + err)
        rows = self._rows(out)
        self.assertEqual(len(rows), 2, out)
        self.assertIn("Daughter", rows[1])
        self.assertIn("Birthday", rows[1])  # the fixed birthday column, unchanged
        self.assertNotIn("\n", rows[1])


class TestCardSavePreview(unittest.TestCase):
    """`card` joins the preview surface every other render command already had."""

    BASE = ["card", "--title", "KODI", "--value", "READY", "--sub", "sub", "--footer", "foot"]

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.out = os.path.join(self.tmp, "card.png")

    def test_preview_writes_a_128x128_png(self):
        rc, out, err = _main(self.BASE + ["--save-preview", self.out])
        self.assertEqual(rc, 0, out + err)
        self.assertIn("Saved card preview", out)
        with Image.open(self.out) as img:
            self.assertEqual(img.size, (128, 128))

    def test_preview_is_the_image_the_live_seam_renders(self):
        """Parity with `renderers.card_image` — one render, not a preview dialect."""
        import argparse

        rc, _out, err = _main(self.BASE + ["--save-preview", self.out])
        self.assertEqual(rc, 0, err)
        args = argparse.Namespace(
            title="KODI", value="READY", sub="sub", footer="foot", theme="soon"
        )
        with Image.open(self.out) as img:
            self.assertEqual(img.convert("RGB").tobytes(), renderers.card_image(args).tobytes())

    def test_a_refused_card_is_never_previewed(self):
        """The m31b seam guards the preview too: exit 6, no file, no USB."""
        for flag in ("--title", "--value", "--sub", "--footer"):
            with self.subTest(flag=flag):
                argv = list(self.BASE)
                argv[argv.index(flag) + 1] = "a\nb"
                before = sorted(os.listdir(self.tmp))
                rc, out, err = _main(argv + ["--save-preview", self.out])
                self.assertEqual(rc, 6, out + err)
                self.assertIn(flag, out + err)
                self.assertNotIn("Traceback", out + err)
                self.assertEqual(sorted(os.listdir(self.tmp)), before, "a file was written")

    def test_unusable_targets_are_exit_9_not_a_traceback(self):
        as_dir = _main(self.BASE + ["--save-preview", self.tmp])
        self.assertEqual(as_dir[0], 9, as_dir[1] + as_dir[2])
        no_ext = _main(self.BASE + ["--save-preview", os.path.join(self.tmp, "card.xyz")])
        self.assertEqual(no_ext[0], 9, no_ext[1] + no_ext[2])
        ro = os.path.join(self.tmp, "ro")
        os.makedirs(ro)
        os.chmod(ro, 0o500)
        try:
            rc, out, err = _main(self.BASE + ["--save-preview", os.path.join(ro, "c.png")])
            self.assertEqual(rc, 9, out + err)
        finally:
            os.chmod(ro, 0o700)

    def test_preview_needs_no_device_and_beats_dry_run(self):
        """Dispatch order parity with hud/media: a preview is a file, not a payload."""
        rc, out, err = _main(["--dry-run"] + self.BASE + ["--save-preview", self.out])
        self.assertEqual(rc, 0, out + err)
        self.assertTrue(os.path.exists(self.out))
        self.assertNotIn("nothing to preview", out)

    def test_missing_parent_directories_are_created(self):
        nested = os.path.join(self.tmp, "a", "b", "card.png")
        rc, out, err = _main(self.BASE + ["--save-preview", nested])
        self.assertEqual(rc, 0, out + err)
        self.assertTrue(os.path.exists(nested))


class TestParserSurface(unittest.TestCase):
    """Every render command previews; the repeated help strings have one home."""

    def setUp(self):
        from ak820.parser import build_parser

        self.parser = build_parser()

    def _flags(self, *argv):
        return self.parser.parse_args(list(argv))

    def test_every_render_command_accepts_save_preview(self):
        for argv in (
            ["radar"],
            ["card", "--title", "T", "--value", "V"],
            ["hud"],
            ["media"],
            ["stats"],
        ):
            with self.subTest(command=argv[0]):
                args = self._flags(*argv, "--save-preview", "out")
                self.assertEqual(args.save_preview, "out")

    def test_force_and_preview_help_have_one_wording(self):
        from ak820 import parser as parser_mod

        seen_force, seen_preview = set(), set()
        for action in self.parser._subparsers._group_actions[0].choices.values():  # type: ignore[attr-defined]
            for act in action._actions:  # type: ignore[attr-defined]
                if "--force" in act.option_strings:
                    seen_force.add(act.help)
                if "--save-preview" in act.option_strings:
                    seen_preview.add(act.help)
        self.assertEqual(seen_force, {parser_mod._FORCE_HELP})
        # radar previews a DIRECTORY, so it keeps its own wording; every FILE
        # preview target (card/hud/media) shares one string.
        self.assertIn(parser_mod._PREVIEW_HELP, seen_preview)
        self.assertEqual(len(seen_preview), 2)

    def test_watch_help_keeps_its_per_command_example(self):
        """The one-home constant is a template: each command keeps its own example."""
        choices = self.parser._subparsers._group_actions[0].choices  # type: ignore[attr-defined]
        for command, example in (("hud", "--watch 15"), ("media", "--watch 10")):
            actions = {a.dest: a for a in choices[command]._actions}  # type: ignore[attr-defined]
            with self.subTest(command=command):
                self.assertIn(example, actions["watch"].help)


class TestSourceInvariants(unittest.TestCase):
    """Anti-recurrence: the seams introduced this iteration stay single homes."""

    def test_card_preview_and_live_share_one_render_seam(self):
        config_src = (ROOT / "ak820" / "config.py").read_text(encoding="utf-8")
        renderers_src = (ROOT / "ak820" / "renderers.py").read_text(encoding="utf-8")
        self.assertIn("renderers.card_image(args)", config_src)
        self.assertIn("img = card_image(args)", renderers_src)
        # the validation lives in card_image and nowhere else in renderers
        self.assertEqual(renderers_src.count("validate_single_line"), 4)

    def test_events_list_flattens_at_the_print_seam_only(self):
        src = (ROOT / "ak820" / "config.py").read_text(encoding="utf-8")
        self.assertIn("configvalues.flatten_for_display(u[\"title\"])", src)
        self.assertIn("configvalues.flatten_for_display(u[\"milestone_str\"])", src)
        # the JSON branch is untouched: display flattening must not reach the data
        json_branch = src.split('if getattr(args, "json", False):')[1].split("else:")[0]
        self.assertNotIn("flatten_for_display", json_branch)

    def test_configvalues_stays_pil_free(self):
        """The twin exists BECAUSE this module may never import PIL (`events add`
        must stay PIL-free, §10.2) — so the lock is on imports, not on the word."""
        tree = ast.parse((ROOT / "ak820" / "configvalues.py").read_text(encoding="utf-8"))
        imported: set = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        self.assertFalse([m for m in imported if "PIL" in m], imported)
        self.assertFalse([m for m in imported if m.endswith("screen")], imported)

    def test_hud_never_reintroduces_a_glyph_tag(self):
        """AST-level: every string literal assigned to `tag` in render_media_card is ASCII."""
        tree = ast.parse((ROOT / "ak820" / "hud.py").read_text(encoding="utf-8"))
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "render_media_card"
        )
        tags = [
            node.value.value
            for node in ast.walk(fn)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            and any(getattr(t, "id", "") == "tag" for t in node.targets)
        ]
        self.assertEqual(len(tags), 3, tags)
        for tag in tags:
            self.assertTrue(tag.isascii(), tag)


if __name__ == "__main__":
    unittest.main()
