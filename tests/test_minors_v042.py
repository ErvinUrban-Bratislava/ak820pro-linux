"""v0.42.0 — the width rule's last misalignment class: GRAPHEME CLUSTERS (m36).

Live-reproved before fixing (v0.40.0 probes): `display_width` counted
CODEPOINTS while a terminal advances CELLS, and several codepoints share one
cell. `Family 👨\\u200d👩\\u200d👧 Reunion` measured 21 cells while the joined family
glyph renders as 17, so every following cell of that row shifted LEFT — the
mirror of m33's CJK shift, which moved them RIGHT. A skin-tone modifier
(`👍🏽` = 4 cells) and a variation selector are the same class.

DECIDED (v0.42.0): implement the deterministic cluster heuristic rather than
pin the boundary. The codepoint count is the same proxy class m33 rejected for
the screen, and relocating a known misalignment to another surface has never
been this project's answer. stdlib has no grapheme-cluster API, so the rule is
a heuristic over the classes real text contains — a U+200D-joined run holding
an emoji is ONE glyph (its widest member), marks/selectors/skin tones ride the
base cell — locked below against hand-computed cell counts, never against
itself. The residuals a heuristic cannot see (VS16 emoji-presentation
promotion, keycaps, a stray ZWJ between text and emoji, font-less terminals)
are documented at the seam and pinned here as DECIDED behavior, so a later
audit reads a decision instead of an oversight.

Carried (the v0.40.0 probe note): the trailing URGENCY/DESCRIPTION columns are
unpadded and unbounded. They are exempt BY CONSTRUCTION — nothing follows them
and their vocabulary is fixed shipped text — and the exemption is now documented
at its home and pinned structurally, the v0.39 numeric-strftime arrangement.

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
import unicodedata
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ak820 import backlight, cli, configvalues, events, palette, tables, textwidth
from ak820.backlight import SHOWCASE_PRESETS

# Explicit escapes everywhere: a joined sequence is invisible in an editor and
# an ordinary save can silently recompose or drop it — the exact codepoint
# sequence IS the test (the v0.39/v0.40 convention).
ZWJ = "\u200d"  # U+200D ZERO WIDTH JOINER
VS16 = "\ufe0f"  # U+FE0F VARIATION SELECTOR-16 (emoji presentation)
SKIN = "\U0001F3FD"  # U+1F3FD emoji modifier (Fitzpatrick type-4)
DARK_SKIN = "\U0001F3FF"
HANDSHAKE = "\U0001F91D"
FAMILY = "\U0001F468\u200d\U0001F469\u200d\U0001F467"  # man ZWJ woman ZWJ girl
HEART = "❤"  # U+2764 — text-default presentation (narrow!)
FIRE = "\U0001F525"
FLAG_SK = "\U0001F1F8\U0001F1F0"  # regional-indicator PAIR (each is Neutral)
FLAG_AT = "\U0001F1E6\U0001F1F9"
BIRD = "\U0001F426"
SQUARE = "⬛"  # U+2B1B — BMP member of a joined emoji run
WOMAN = "\U0001F469"
LAPTOP = "\U0001F4BB"
RUNNER = "\U0001F3C3"
FEMALE_SIGN = "♀"
RAINBOW = "\U0001F308"
THUMB = "\U0001F44D"
GRIN = "\U0001F600"
COPYRIGHT = "©"

EMOJI_TITLE = "Family " + FAMILY + " Reunion"  # the live-reproved title

# Verbatim pre-v0.42 `display_width` — the oracle that proves the defect existed
# and that the new rule changes ONLY joined/riding sequences (m31b's pattern).
_LEGACY_ZERO_WIDTH = frozenset({0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF})


def _legacy_width(text: str) -> int:
    width = 0
    for ch in text:
        if unicodedata.category(ch) in ("Mn", "Me") or ord(ch) in _LEGACY_ZERO_WIDTH:
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def _main(argv):
    """cli.main(argv) capturing stdout+stderr; SystemExit code becomes the rc."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cli.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


def trailing_column(source: str, func: str):
    """(kind, key) of `func`'s trailing row column: 'raw' or 'padded'.

    The row is the f-string handed to `lines.append(...)` inside the formatter's
    loop; its LAST formatted segment is the trailing column. 'padded' means a
    `pad(...)` call wraps it — which the trailing-column exemption says must
    never happen, so a checker that can see both is provably not vacuous.
    """
    tree = ast.parse(source)
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef) or fn.name != func:
            continue
        for loop in [n for n in ast.walk(fn) if isinstance(n, ast.For)]:
            for call in [n for n in ast.walk(loop) if isinstance(n, ast.Call)]:
                if not (isinstance(call.func, ast.Attribute) and call.func.attr == "append"):
                    continue
                if not (call.args and isinstance(call.args[0], ast.JoinedStr)):
                    continue
                fvs = [v for v in call.args[0].values if isinstance(v, ast.FormattedValue)]
                last = fvs[-1].value
                padded = isinstance(last, ast.Call) and getattr(last.func, "attr", "") == "pad"
                return ("padded" if padded else "raw"), _column_key(last)
    raise AssertionError(f"no row f-string found in {func}()")


def _column_key(node):
    """The dict key a row column reads, whether raw or wrapped in `pad(...)`."""
    if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "pad" and node.args:
        node = node.args[0]
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        return node.slice.value
    return None


class TestClusterWidth(unittest.TestCase):
    """The cluster rule, locked against hand-computed cell counts."""

    def test_live_reproved_title_now_measures_the_rendered_run(self):
        self.assertEqual(_legacy_width(EMOJI_TITLE), 21)  # the defect, verbatim
        self.assertEqual(textwidth.display_width(EMOJI_TITLE), 17)

    def test_zwj_family_is_one_glyph(self):
        self.assertEqual(textwidth.display_width(FAMILY), 2)
        self.assertEqual(_legacy_width(FAMILY), 6)

    def test_two_joined_runs_are_two_glyphs_not_one(self):
        # Clusters collapse per RUN, never across a whole string.
        self.assertEqual(textwidth.display_width(FAMILY + FAMILY), 4)
        self.assertEqual(textwidth.display_width("a" + FAMILY + "b"), 4)

    def test_skin_tone_modifier_rides_the_base(self):
        self.assertEqual(textwidth.display_width(THUMB + SKIN), 2)
        self.assertEqual(_legacy_width(THUMB + SKIN), 4)

    def test_variation_selector_rides_the_base(self):
        self.assertEqual(textwidth.display_width(HEART + VS16), 1)
        self.assertEqual(textwidth.display_width(COPYRIGHT + VS16), 1)

    def test_joined_run_may_start_on_a_text_default_glyph(self):
        # U+2764 is NARROW (EAW Neutral): the rule keys on "any member is an
        # emoji", not "the first member is", or heart-on-fire over-counts.
        self.assertEqual(textwidth.display_width(HEART + VS16 + ZWJ + FIRE), 2)

    def test_joined_run_mixes_astral_and_bmp_members(self):
        self.assertEqual(textwidth.display_width(BIRD + ZWJ + SQUARE), 2)
        self.assertEqual(textwidth.display_width(WOMAN + ZWJ + LAPTOP), 2)

    def test_compound_sequences_from_every_class(self):
        self.assertEqual(textwidth.display_width(RUNNER + SKIN + ZWJ + FEMALE_SIGN + VS16), 2)
        self.assertEqual(
            textwidth.display_width("\U0001F468" + DARK_SKIN + ZWJ + HANDSHAKE + ZWJ + "\U0001F468" + DARK_SKIN),
            2,
        )
        self.assertEqual(textwidth.display_width("\U0001F3F3" + VS16 + ZWJ + RAINBOW), 2)

    def test_regional_indicator_pair_needs_no_cluster_rule(self):
        # Documented finding: each RI is Neutral (1 cell) and a flag glyph is 2
        # cells, so the per-codepoint rule already measured a pair correctly.
        # Collapsing the pair would have BROKEN it (measured live: 1 cell).
        self.assertEqual(textwidth.display_width(FLAG_SK), 2)
        self.assertEqual(textwidth.display_width(FLAG_SK + FLAG_AT), 4)
        self.assertEqual(textwidth.display_width(FLAG_SK + "\U0001F1E6"), 3)

    def test_narrow_zwj_run_still_does_not_collapse(self):
        # The v0.39 pin, restated: no ligature exists for two letters, so an
        # all-narrow run sums. The cluster scanner must not eat this.
        self.assertEqual(textwidth.display_width("a" + ZWJ + "b"), 2)
        self.assertEqual(textwidth.display_width("ab" + ZWJ), 2)
        self.assertEqual(textwidth.display_width(ZWJ), 0)
        self.assertEqual(textwidth.display_width(ZWJ + ZWJ), 0)

    def test_a_joiner_binds_nothing_across_whitespace(self):
        # Live-reproved: a space cannot be joined, so `man ZWJ space woman` is
        # three glyphs (2 + 1 + 2). The flatten seam CREATES this shape when a
        # hand-edited config breaks a joined run mid-sequence.
        self.assertEqual(textwidth.display_width("\U0001F468" + ZWJ + " " + WOMAN), 5)
        self.assertEqual(textwidth.display_width("a" + ZWJ + " b"), 3)
        self.assertEqual(
            textwidth.display_width(
                configvalues.flatten_for_display("\U0001F468" + ZWJ + "\n" + WOMAN)
            ),
            5,
        )

    def test_documented_residuals_are_pinned_behavior(self):
        # Each of these is a terminal-dependent choice recorded in the module
        # docstring — pinned so a change is a decision, never a drift.
        self.assertEqual(textwidth.display_width("a" + ZWJ + GRIN), 2)  # may render 3
        self.assertEqual(textwidth.display_width(GRIN + ZWJ + "a"), 2)
        self.assertEqual(textwidth.display_width("1" + VS16 + "\u20e3"), 1)  # keycap: may render 2

    def test_the_pre_cluster_rule_is_untouched(self):
        # The cluster scanner is a strict EXTENSION: every value v0.39 pinned
        # still holds, byte for byte.
        cases = [
            ("", 0),
            ("Grandmother Birthday", 20),
            ("お盆休み", 8),
            ("春节", 4),
            ("Ａ", 2),
            ("ｱ", 1),
            ("e" + "\u0301", 1),
            ("a" + "\u0300" + "\u0301", 1),
            ("\u200b\ufeff", 0),
            ("°é→", 3),
            (GRIN, 2),
            ("xおy", 4),
        ]
        for text, want in cases:
            with self.subTest(text=text):
                self.assertEqual(textwidth.display_width(text), want)


class TestPadWithClusters(unittest.TestCase):
    """Padding measures clusters now; every other `pad` promise stands."""

    def test_ascii_parity_with_ljust_is_unchanged(self):
        for s, w in (("", 5), ("ab", 5), ("ab", 2), ("abcdef", 3), ("Grandmother", 22)):
            with self.subTest(s=s, w=w):
                self.assertEqual(textwidth.pad(s, w), s.ljust(w))

    def test_emoji_title_pads_to_the_target_cells(self):
        padded = textwidth.pad(EMOJI_TITLE, 22)
        self.assertEqual(textwidth.display_width(padded), 22)
        self.assertEqual(padded, EMOJI_TITLE + " " * 5)

    def test_overwide_emoji_run_is_still_never_truncated(self):
        text = FAMILY + FAMILY + FAMILY
        self.assertEqual(textwidth.pad(text, 2), text)


class TestEventsTableWithEmoji(unittest.TestCase):
    """The seam end-to-end: an emoji title aligns like any other row."""

    ROW = {
        "title": EMOJI_TITLE,
        "next_date": date(2026, 11, 15),
        "days_left": 21,
        "milestone_str": "Anniversary",
        "theme_key": "soon",
    }

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = os.path.join(self.tmp.name, "events.json")
        with open(self.cfg, "w", encoding="utf-8") as fh:
            json.dump({"events": []}, fh)
        self.addCleanup(self.tmp.cleanup)

    def _list(self, rows):
        def fake(cfg, today=None):
            return [dict(r) for r in rows]

        with mock.patch.object(events, "compute_upcoming_events", side_effect=fake):
            rc, out, err = _main(["events", "list", "--config", self.cfg])
        self.assertEqual(rc, 0, out + err)
        return out.splitlines()

    def test_the_emoji_row_is_the_exact_expected_line(self):
        lines = self._list([self.ROW])
        expected = (
            EMOJI_TITLE + " " * 5 + " "  # title padded to 22 CELLS (17 used)
            + "2026-11-15" + " " * 2 + " "  # date to 12
            + "21d" + " " * 5 + " "  # left to 8
            + "Anniversary" + " " + " "  # milestone to 12
            + "soon"
        )
        self.assertEqual(lines[2], expected)

    def test_the_legacy_rule_would_have_left_the_title_short(self):
        # The defect as geometry: padding to the CODEPOINT count leaves the
        # field 4 cells short of its column, so NEXT DATE landed early.
        legacy_field = EMOJI_TITLE + " " * (tables.EVENT_COL_W - _legacy_width(EMOJI_TITLE))
        self.assertEqual(textwidth.display_width(legacy_field), 18)
        self.assertEqual(tables.EVENT_COL_W - textwidth.display_width(legacy_field), 4)
        self.assertEqual(textwidth.display_width(textwidth.pad(EMOJI_TITLE, 22)), 22)

    def test_every_column_starts_at_the_header_cell(self):
        lines = self._list([self.ROW])
        header = lines[0]
        cols = {t: header.index(t) for t in ("NEXT DATE", "LEFT", "MILESTONE", "URGENCY")}
        line = lines[2]
        checks = (
            ("NEXT DATE", line.index("2026-11-15")),
            ("LEFT", line.index("21d", line.index("2026-11-15") + 1)),
            ("MILESTONE", line.rindex("Anniversary")),
            ("URGENCY", line.rindex("soon")),
        )
        for token, pos in checks:
            with self.subTest(token=token):
                self.assertEqual(textwidth.display_width(line[:pos]), cols[token])

    def test_a_flattened_break_inside_a_joined_run_stays_aligned(self):
        # m32 flatten + m36 clusters compose: one row, and the space the
        # flattener substituted is its own cell (it cannot be joined).
        row = dict(self.ROW, title="\U0001F468" + ZWJ + "\n" + WOMAN)
        lines = self._list([row])
        self.assertEqual(len(lines), 3)  # header + rule + exactly one row
        self.assertEqual(
            textwidth.display_width(lines[2][: lines[2].index("2026-11-15")]),
            lines[0].index("NEXT DATE"),
        )

    def test_json_keeps_the_raw_joined_codepoints(self):
        with open(self.cfg, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "events": [
                        {
                            "id": "fam",
                            "title": EMOJI_TITLE,
                            "short_title": "fam",
                            "date": "2026-11-15",
                            "category": "milestone",
                        }
                    ]
                },
                fh,
            )
        rc, out, err = _main(["events", "list", "--config", self.cfg, "--json"])
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(json.loads(out)[0]["title"], EMOJI_TITLE)  # raw, unpadded

    def test_stored_bytes_keep_the_raw_joined_codepoints(self):
        rc, out, err = _main(
            [
                "events",
                "add",
                "--short",
                "fam",
                "--title",
                EMOJI_TITLE,
                "--date",
                "2026-11-15",
                "--category",
                "milestone",
                "--config",
                self.cfg,
            ]
        )
        self.assertEqual(rc, 0, out + err)
        with open(self.cfg, encoding="utf-8") as fh:
            stored = json.load(fh)["events"][0]
        self.assertEqual(stored["title"], EMOJI_TITLE)
        self.assertNotIn("  ", stored["title"])  # no pad spaces stored


class TestTrailingColumnExemptionClass(unittest.TestCase):
    """Carried item: URGENCY/DESCRIPTION are exempt BY CONSTRUCTION — the
    exemption is documented at its home and pinned so no audit re-opens it."""

    def test_the_exemption_is_documented_at_its_home_and_sites(self):
        src = (ROOT / "ak820" / "tables.py").read_text(encoding="utf-8")
        self.assertIn("Trailing-column exemption class", src)  # the home
        # ...and at both row sites, so the raw column reads as a choice.
        self.assertIn("theme_key is emitted raw: trailing-column exemption", src)
        self.assertIn("desc is emitted raw: trailing-column exemption", src)

    def test_theme_key_vocabulary_is_ascii_and_bounded(self):
        keys = sorted(palette.THEMES)
        self.assertEqual(keys, ["calm", "green", "soon", "today", "urgent", "warn"])
        for key in keys:
            with self.subTest(key=key):
                self.assertTrue(key.isascii(), key)
                self.assertLessEqual(textwidth.display_width(key), 8)

    def test_events_assigns_theme_keys_only_from_that_vocabulary(self):
        # The bound is structural: the producer picks from a closed chain, so
        # user text can never reach the unpadded column.
        src = (ROOT / "ak820" / "events.py").read_text(encoding="utf-8")
        assigned: set = set()
        for node in ast.walk(ast.parse(src)):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "theme_key"
            ):
                self.assertIsInstance(node.value, ast.Constant)
                assigned.add(node.value.value)
        self.assertTrue(assigned, "the closed chain vanished — re-check")
        self.assertTrue(assigned <= set(palette.THEMES), assigned)

    def test_preset_descriptions_are_ascii_catalog_text(self):
        self.assertTrue(SHOWCASE_PRESETS)
        for p in SHOWCASE_PRESETS:
            with self.subTest(key=p["key"]):
                self.assertTrue(p["desc"].isascii(), p["desc"])
                self.assertTrue(p["desc"].strip())

    def test_both_rows_emit_their_trailing_column_raw(self):
        src = (ROOT / "ak820" / "tables.py").read_text(encoding="utf-8")
        self.assertEqual(trailing_column(src, "format_events_table"), ("raw", "theme_key"))
        self.assertEqual(trailing_column(src, "format_preset_table"), ("raw", "desc"))

    def test_the_trailing_column_starts_at_its_header_cell(self):
        rows = [
            {
                "title": "Plain",
                "next_date": date(2026, 12, 31),
                "days_left": 5,
                "milestone_str": "Anniversary",
                "theme_key": "urgent",
            }
        ]
        lines = tables.format_events_table(rows)
        self.assertEqual(
            textwidth.display_width(lines[2][: lines[2].rindex("urgent")]),
            lines[0].index("URGENCY"),
        )
        plines = tables.format_preset_table(SHOWCASE_PRESETS)
        for row, preset in zip(plines[2:], SHOWCASE_PRESETS):
            with self.subTest(key=preset["key"]):
                start = len(row) - len(preset["desc"])
                self.assertEqual(textwidth.display_width(row[:start]), plines[0].index("DESCRIPTION"))

    def test_the_ast_lock_is_not_vacuous(self):
        good = (
            "def format_events_table(upcoming):\n"
            "    for u in upcoming:\n"
            "        lines.append(f\"{a} {u['theme_key']}\")\n"
        )
        bad = (
            "def format_events_table(upcoming):\n"
            "    for u in upcoming:\n"
            "        lines.append(f\"{a} {textwidth.pad(u['theme_key'], 8)}\")\n"
        )
        self.assertEqual(trailing_column(good, "format_events_table"), ("raw", "theme_key"))
        self.assertEqual(trailing_column(bad, "format_events_table"), ("padded", "theme_key"))
        with self.assertRaises(AssertionError):
            trailing_column("def other():\n    pass\n", "format_events_table")


class TestClusterRuleInvariants(unittest.TestCase):
    """Anti-recurrence: the vocabulary the heuristic reads is closed and named."""

    def test_textwidth_is_still_a_stdlib_leaf(self):
        tree = ast.parse((ROOT / "ak820" / "textwidth.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add("." * node.level + (node.module or ""))
        self.assertEqual(imported, {"unicodedata"})

    def test_the_cluster_vocabulary_is_exactly_the_documented_sets(self):
        self.assertEqual(textwidth._ZWJ, 0x200D)
        self.assertEqual(textwidth._VARIATION_SELECTORS, frozenset(range(0xFE00, 0xFE10)))
        self.assertEqual(textwidth._SKIN_TONES, frozenset(range(0x1F3FB, 0x1F400)))
        self.assertEqual(
            textwidth._ZERO_WIDTH, frozenset({0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF})
        )
        # ZWJ is the join OPERATOR, never an extender — absorbing it would fold
        # `a\u200db` into one cluster and break the v0.39 pin.
        self.assertNotIn(textwidth._ZWJ, textwidth._EXTENDERS)
        self.assertIn(textwidth._ZWJ, textwidth._ZERO_WIDTH)

    def test_each_vocabulary_constant_is_assigned_once(self):
        tree = ast.parse((ROOT / "ak820" / "textwidth.py").read_text(encoding="utf-8"))
        names = [
            n.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and n.id.startswith("_")
        ]
        for name in ("_ZERO_WIDTH", "_ZWJ", "_VARIATION_SELECTORS", "_SKIN_TONES", "_EXTENDERS"):
            self.assertEqual(names.count(name), 1, name)

    def test_the_screen_side_still_measures_real_pixels(self):
        # The 128x128 surface is unaffected by any terminal-width rule: it
        # measures with textbbox and may not import the print-seam leaf (§10.2).
        src = (ROOT / "ak820" / "screen.py").read_text(encoding="utf-8")
        self.assertNotIn("textwidth", src)
        self.assertIn("textbbox", (ROOT / "ak820" / "cards.py").read_text(encoding="utf-8"))

    def test_module_budgets_hold(self):
        for rel in ("ak820/textwidth.py", "ak820/tables.py"):
            n = len((ROOT / rel).read_text(encoding="utf-8").splitlines())
            self.assertLess(n, 300, f"{rel} is {n} lines")

    def test_leaf_and_consumers_import_in_a_fresh_interpreter(self):
        code = (
            f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
            "import ak820.textwidth, ak820.tables, ak820.config, ak820.backlight; print('ok')"
        )
        done = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("ok", done.stdout)


if __name__ == "__main__":
    unittest.main()
