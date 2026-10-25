r"""v0.43.0 — the single-line vocabulary's remaining class: bidi format characters (m37).

Live-reproved before fixing: `validate_single_line` ACCEPTED `Trip\u202eHome`
(U+202E RIGHT-TO-LEFT OVERRIDE) — the CLI stored it exit 0 and `display_width`
counted 9 cells. The width is not the defect; the ORDER is: a terminal runs the
Unicode bidi algorithm and renders the rest of the line reversed, scrambling
that row and every column after it. No width rule can fix order, so the fix is
rejection at the creation seam and flattening at the display seam — exactly the
m34 TAB dual structure (the CLI cannot govern hand-edited configs; the display
seam cannot retroactively reject stored data).

v0.40's vocabulary was category Cc and these are Cf — the class was always
"characters with terminal-defined, unpredictable rendering inside a single-line
field", never "Cc". The vocabulary is Unicode's Bidi_Control property — all 12
codepoints, listed explicitly — NOT Cf wholesale: Cf also holds the joiners
v0.42 just made meaningful (ZWJ/ZWNJ/WJ/BOM) and format chars that never
reorder (soft hyphen).

The screen-side assumption was VERIFIED with a real render, not assumed — and
it FAILED: this Pillow is a Raqm build (`features.check("raqm")` is True) and
DOES apply the bidi algorithm (`A\u202eCB` renders pixel-identical to `ABC`). The
128x128 board is unaffected because every user string passes through
`flatten_lines` before it is measured or drawn, not because PIL is naive.

Every bidi/joiner spelling below is written with explicit escapes — a literal
control char is invisible in an editor and the exact codepoint IS the test
(v0.40's rule, applied to the very class that hides itself).
Zero hardware, zero network; no test depends on today's date.
"""

import ast
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ak820 import cli, configvalues, events, screen, tables, textwidth  # noqa: E402
from ak820.errors import ConfigError  # noqa: E402

# Unicode's Bidi_Control property — all 12 members, explicit codepoints: ALM,
# LRM/RLM, the embeds/overrides + PDF, the isolates + PDI.
BIDI_CODEPOINTS = [
    0x061C, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
    0x2066, 0x2067, 0x2068, 0x2069,
]
BIDI_CHARS = [chr(cp) for cp in BIDI_CODEPOINTS]
# Cf members that must KEEP passing: the joiners v0.42 made meaningful plus
# soft hyphen — rejecting these would break the family-emoji support shipped
# last iteration (the "weigh Cf against the joiners" decision, pinned).
JOINER_CHARS = ["\u200c", "\u200d", "\u2060", "\ufeff", "\u00ad"]
# The repro string from the queue item, spelled with its exact codepoint.
TRIP = "Trip\u202eHome"


def _main(argv):
    """cli.main(argv) capturing stdout+stderr; SystemExit code becomes the rc."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cli.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


class TestBidiRejectedUnit(unittest.TestCase):
    """The CLI guard's widened unit contract (m37)."""

    def test_every_bidi_char_is_rejected(self):
        for ch in BIDI_CHARS:
            with self.subTest(ch=f"U+{ord(ch):04X}"):
                with self.assertRaises(ConfigError) as cm:
                    configvalues.validate_single_line(f"a{ch}b", "--note 'x'")
                msg = str(cm.exception)
                self.assertIn("--note", msg)
                self.assertIn("bidi", msg)
                self.assertIn("order", msg)

    def test_line_break_message_still_wins_over_bidi(self):
        # Ordering (v0.36): a string holding a break AND an override names the
        # break — the more fundamental problem.
        with self.assertRaises(ConfigError) as cm:
            configvalues.validate_single_line("a\n\u202eb", "--x")
        self.assertIn("line break", str(cm.exception))
        self.assertNotIn("bidi", str(cm.exception))

    def test_bidi_message_wins_over_control(self):
        # The widened union matches both; the bidi check must run BEFORE the
        # control check or a TAB+RLO string gets the tab-stop message, which
        # misdescribes an ordering defect.
        with self.assertRaises(ConfigError) as cm:
            configvalues.validate_single_line("a\t\u202eb", "--x")
        self.assertIn("bidi", str(cm.exception))
        self.assertNotIn("TAB", str(cm.exception))

    def test_joiners_and_non_reordering_cf_still_pass(self):
        # Cf wholesale is the WRONG vocabulary: ZWJ/ZWNJ/WJ/BOM/soft hyphen are
        # Cf but not Bidi_Control — v0.42's cluster rule made ZWJ meaningful.
        for ch in JOINER_CHARS:
            with self.subTest(ch=f"U+{ord(ch):04X}"):
                s = f"a{ch}b"
                self.assertIs(configvalues.validate_single_line(s, "--x"), s)

    def test_family_emoji_still_valid(self):
        fam = "Family 👨\u200d👩\u200d👧 Reunion"
        self.assertIs(configvalues.validate_single_line(fam, "--title"), fam)

    def test_bidi_free_text_passes_unchanged(self):
        for value in ["Turns 5", "お盆休み", "a b", "", "A-B", "Trip Home"]:
            with self.subTest(value=value):
                self.assertIs(configvalues.validate_single_line(value, "--x"), value)


class TestCliRejectsBidi(unittest.TestCase):
    """The creation path refuses what no renderer can reorder back."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = f"{self.tmp}/events.json"
        with open(self.cfg, "w", encoding="utf-8") as f:
            json.dump({"events": []}, f)
        self.before = Path(self.cfg).read_bytes()

    def _add(self, *extra):
        return _main(
            ["events", "add", "--config", self.cfg, "--title", "T", "--short", "S",
             "--date", "06-01", *extra]
        )

    def test_events_flags_with_bidi_exit_6_and_save_nothing(self):
        for flag in ["--note", "--short", "--title"]:
            with self.subTest(flag=flag):
                rc, _out, err = self._add(flag, TRIP)
                self.assertEqual(rc, 6)
                self.assertIn(flag, err)
                self.assertIn("bidi", err)
                self.assertEqual(Path(self.cfg).read_bytes(), self.before, "config was written")
                self.assertNotIn("Traceback", err)

    def test_card_flags_with_bidi_exit_6_on_dry_run(self):
        base = ["--dry-run", "card", "--title", "REMINDER", "--value", "27 DAYS",
                "--sub", "s", "--footer", "f"]
        for flag in ["--title", "--value", "--sub", "--footer"]:
            argv = list(base)
            argv[argv.index(flag) + 1] = TRIP
            with self.subTest(flag=flag):
                rc, _out, err = _main(argv)
                self.assertEqual(rc, 6)
                self.assertIn(flag, err)
                self.assertIn("bidi", err)

    def test_valid_forms_still_pass(self):
        rc, _out, _err = self._add("--note", "Turns {years} today")
        self.assertEqual(rc, 0)
        rc, _out, _err = self._add("--title", "Family 👨\u200d👩\u200d👧 Reunion")
        self.assertEqual(rc, 0)


class TestBidiFlattenTwin(unittest.TestCase):
    """One rule, two homes (v0.36/v0.37/v0.40 precedent): the twins widened together."""

    SPELLINGS = [f"a{c}b" for c in BIDI_CHARS] + [
        TRIP,
        "a\u2066\u202eb\u2069",
        "\u202e",
        "  padded\u202evalue  ",
        "a \u202e b",
        "plain",
        "",
        "a  b",  # whitespace run WITHOUT a vocabulary char: trigger stays closed
    ]

    def test_pattern_is_one_pattern_in_two_homes(self):
        self.assertEqual(configvalues._CONTROL_RE.pattern, screen._CONTROL_RE.pattern)

    def test_union_membership_is_exactly_cc_zlz_p_plus_bidi(self):
        # The union may not drift in EITHER direction: every Cc/Zl/Zp/bidi char
        # matches, every joiner/ordinary char does not. This is the "weighed Cf
        # against the joiners" decision, locked as behavior.
        expected = set(range(0x00, 0x20)) | set(range(0x7F, 0xA0)) | {0x2028, 0x2029}
        expected |= set(BIDI_CODEPOINTS)
        battery = list(range(0x00, 0x40)) + list(range(0x7E, 0xA2)) + list(range(0x2000, 0x2070))
        battery += [0x061B, 0x061C, 0x061D, 0x00AD, 0x2060, 0xFEFF, 0x4E00, 0x1F600]
        for cp in battery:
            ch = chr(cp)
            with self.subTest(cp=f"U+{cp:04X}"):
                self.assertEqual(bool(configvalues._CONTROL_RE.search(ch)), cp in expected)

    def test_bidi_subset_is_locked_inside_the_union(self):
        # screen has no bidi-specific check; its flatten must still strip every
        # bidi char — the subset lock is what makes the two homes ONE rule.
        for ch in BIDI_CHARS:
            with self.subTest(cp=f"U+{ord(ch):04X}"):
                self.assertTrue(configvalues._BIDI_RE.search(ch))
                self.assertTrue(configvalues._CONTROL_RE.search(ch))
                self.assertTrue(screen._CONTROL_RE.search(ch))

    def test_search_equivalence_battery(self):
        for s in self.SPELLINGS:
            with self.subTest(s=repr(s)):
                self.assertEqual(
                    bool(configvalues._CONTROL_RE.search(s)),
                    bool(screen._CONTROL_RE.search(s)),
                )

    def test_flatten_twins_agree(self):
        for text in self.SPELLINGS:
            with self.subTest(text=repr(text)):
                self.assertEqual(
                    configvalues.flatten_for_display(text), screen.flatten_lines(text)
                )

    def test_bidi_flattens_to_one_space(self):
        self.assertEqual(configvalues.flatten_for_display(TRIP), "Trip Home")
        self.assertEqual(configvalues.flatten_for_display("a \u202e b"), "a b")
        self.assertEqual(configvalues.flatten_for_display("\u202e"), "")
        self.assertEqual(configvalues.flatten_for_display("a\u2066\u202eb\u2069"), "a b")

    def test_joiners_survive_flatten_byte_identical(self):
        # v0.42 composition: the cluster rule's inputs must pass the flatten
        # untouched, or the print seam would silently break joined sequences.
        fam = "Family 👨\u200d👩\u200d👧 Reunion"
        self.assertEqual(configvalues.flatten_for_display(fam), fam)
        for ch in JOINER_CHARS:
            with self.subTest(ch=f"U+{ord(ch):04X}"):
                self.assertEqual(configvalues.flatten_for_display(f"a{ch}b"), f"a{ch}b")

    def test_flattening_is_idempotent_and_vocabulary_free(self):
        for text in self.SPELLINGS:
            once = configvalues.flatten_for_display(text)
            self.assertIsNone(configvalues._CONTROL_RE.search(once), repr(text))
            self.assertEqual(configvalues.flatten_for_display(once), once)


class TestHandEditedBidiAtPrintSeam(unittest.TestCase):
    """CLI 6 / loader 0: stored bidi chars load, print aligned, stay raw."""

    ROW = {
        "title": TRIP,
        "next_date": date(2026, 12, 1),
        "days_left": 30,
        "milestone_str": "Turns\u202e5",
        "theme_key": "calm",
    }

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = os.path.join(self.tmp.name, "events.json")
        with open(self.cfg, "w", encoding="utf-8") as fh:
            json.dump({"events": []}, fh)
        self.addCleanup(self.tmp.cleanup)

    def _list(self, *extra):
        def fake(cfg, today=None):
            return [dict(self.ROW)]

        with mock.patch.object(events, "compute_upcoming_events", side_effect=fake):
            rc, out, err = _main(["events", "list", "--config", self.cfg, *extra])
        self.assertEqual(rc, 0, out + err)
        return out

    def test_bidi_row_prints_one_aligned_line(self):
        lines = self._list().splitlines()
        self.assertEqual(len(lines), 3)  # header + rule + exactly one row
        row = lines[2]
        self.assertNotIn("\u202e", row)
        self.assertIn("Trip Home", row)
        self.assertIn("Turns 5", row)
        header = lines[0]
        cols = {t: header.index(t) for t in ("NEXT DATE", "LEFT", "MILESTONE", "URGENCY")}
        checks = (
            ("NEXT DATE", row.index("2026-12-01")),
            ("LEFT", row.index("30d", row.index("2026-12-01") + 1)),
            ("MILESTONE", row.index("Turns 5")),
            ("URGENCY", row.rindex("calm")),
        )
        for token, pos in checks:
            with self.subTest(token=token):
                self.assertEqual(textwidth.display_width(row[:pos]), cols[token])

    def test_json_keeps_the_raw_bidi_characters(self):
        rows = json.loads(self._list("--json"))
        self.assertEqual(rows[0]["title"], TRIP)
        self.assertEqual(rows[0]["milestone_str"], "Turns\u202e5")

    def test_stored_bytes_are_untouched_by_listing(self):
        before = Path(self.cfg).read_bytes()
        self._list()
        self.assertEqual(Path(self.cfg).read_bytes(), before)


class TestScreenSideBidi(unittest.TestCase):
    """The spec said VERIFY the screen side with a real render, don't assume.

    The assumption ("PIL draws in logical order, no bidi shaping") is FALSE on
    a Raqm build — live-measured here: `A\u202eCB` renders pixel-identical to
    `ABC`. The tests below therefore pin the two facts that actually protect
    the board: the format char is zero-advance, and the shipped draw path
    (fit_text/ellipsize -> flatten_lines) never hands a bidi char to PIL.
    """

    @classmethod
    def setUpClass(cls):
        if not [p for p in screen.FONT_BOLD_PATHS if os.path.exists(p)]:
            raise unittest.SkipTest("no catalog font available")
        from PIL import Image, ImageDraw  # noqa: PLC0415

        cls.Image, cls.ImageDraw = Image, ImageDraw
        cls.font = screen._load_font(screen.FONT_BOLD_PATHS, 16)

    def _render(self, text):
        img = self.Image.new("L", (128, 32), 0)
        self.ImageDraw.Draw(img).text((2, 2), text, font=self.font, fill=255)
        return img.tobytes()

    def _bbox_w(self, text):
        img = self.Image.new("L", (128, 32), 0)
        b = self.ImageDraw.Draw(img).textbbox((0, 0), text, font=self.font)
        return b[2] - b[0]

    def test_format_char_is_zero_advance_and_order_is_a_defined_world(self):
        # Machine-independent pin (CI Pythons may or may not ship Raqm): the
        # override adds no pixels, and the drawn order is ONE of the two
        # defined worlds — logical (`ABC`) or shaped (`ACB`) — never a third.
        self.assertEqual(self._bbox_w("A\u202eBC"), self._bbox_w("ABC"))
        self.assertIn(self._render("A\u202eBC"), (self._render("ABC"), self._render("ACB")))
        # non-vacuity: the two worlds are distinguishable by this probe
        self.assertNotEqual(self._render("ABC"), self._render("ACB"))

    def test_fit_text_never_hands_bidi_to_the_board(self):
        # The structural protection: every user string is flattened BEFORE PIL
        # sees it, so whichever world the build lives in, the board is clean.
        img = self.Image.new("L", (128, 32), 0)
        draw = self.ImageDraw.Draw(img)
        _font, fitted = screen.fit_text(draw, TRIP, screen.FONT_BOLD_PATHS, 16, 10, 120)
        self.assertNotIn("\u202e", fitted)
        self.assertEqual(fitted, "Trip Home")

    def test_flatten_lines_strips_bidi_screen_twin(self):
        self.assertEqual(screen.flatten_lines(TRIP), "Trip Home")


class TestBidiVocabularyInvariants(unittest.TestCase):
    """Anti-recurrence: the vocabulary is closed, named, and single-assigned."""

    def test_bidi_vocabulary_is_exactly_bidi_control(self):
        # Nothing adjacent drifts in: U+2065 (unassigned), U+2029 (Zl — union
        # via the break set, NOT bidi), U+061B/061D, the joiners — all out.
        expected = set(BIDI_CODEPOINTS)
        battery = list(range(0x2000, 0x2070)) + [0x061B, 0x061C, 0x061D, 0x00AD, 0xFEFF]
        for cp in battery:
            with self.subTest(cp=f"U+{cp:04X}"):
                self.assertEqual(bool(configvalues._BIDI_RE.search(chr(cp))), cp in expected)

    def test_each_vocabulary_pattern_is_assigned_exactly_once(self):
        for rel, names in (
            ("ak820/configvalues.py", ("_LINE_BREAK_RE", "_WS_RUN_RE", "_CONTROL_RE", "_BIDI_RE")),
            ("ak820/screen.py", ("_LINE_BREAK_RE", "_WS_RUN_RE", "_CONTROL_RE")),
        ):
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
            stored = [
                n.id
                for n in ast.walk(tree)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and n.id.startswith("_")
            ]
            for name in names:
                with self.subTest(module=rel, name=name):
                    self.assertEqual(stored.count(name), 1, name)

    def test_screen_still_cannot_import_the_events_domain(self):
        # The twin exists because §10.2 keeps screen away from configvalues;
        # if that ever relaxes the twin ceremony is obsolete — loudly.
        tree = ast.parse((ROOT / "ak820" / "screen.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module)
                imported |= {"." + a.name for a in node.names}
        # (screen IS the PIL module — the lock is the events-domain edge only;
        # configvalues' own PIL-free lock lives in test_minors_v037.py.)
        self.assertFalse([m for m in imported if "configvalues" in m], imported)

    def test_raw_bidi_width_is_a_documented_residual(self):
        # display_width counts the invisible override as 1 cell (the spec's
        # "the WIDTH is right" stance): no shipped path measures raw bidi —
        # the table seam flattens first, and the flattened space IS 1 cell.
        self.assertEqual(textwidth.display_width(TRIP), 9)
        flat = configvalues.flatten_for_display(TRIP)
        self.assertEqual(textwidth.display_width(flat), textwidth.display_width("Trip Home"))

    def test_tables_still_own_the_flatten_call(self):
        # The print seam keeps its shape: pure formatters flatten (v0.40
        # structure, re-pinned for the widened vocabulary).
        src = (ROOT / "ak820" / "tables.py").read_text(encoding="utf-8")
        self.assertIn('configvalues.flatten_for_display(u["title"])', src)
        lines = tables.format_events_table([])
        self.assertEqual(lines[1], "-" * textwidth.display_width(lines[0]))

    def test_leaf_imports_in_a_fresh_interpreter(self):
        code = (
            f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
            "import ak820.configvalues, ak820.screen, ak820.tables; print('ok')"
        )
        done = subprocess.run(
            [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("ok", done.stdout)


if __name__ == "__main__":
    unittest.main()
