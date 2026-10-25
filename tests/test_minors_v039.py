"""v0.39.0 — the print seam's own m31b, queued by v0.38.0.

Live-reproved before fixing: `events list` padded its columns by CHARACTER
COUNT (`:<N` format specs), the exact proxy m31b rejected for the screen —
a row titled `お盆休み` (4 chars, 8 terminal cells) pushed NEXT DATE 4
cells past its header, and a CJK milestone shifted URGENCY again. The data
was always fine; only the table was ragged.

DECIDED (v0.39.0): pad by DISPLAY width via the new stdlib-only leaf
`ak820/textwidth.py` (`east_asian_width` W/F = 2 cells, combining marks and
zero-width codepoints = 0, everything else = 1; ambiguous = 1, the Western
terminal this project ships on). Documenting the ragged table as accepted
would pin a defect the screen side already closed. `--json` and the stored
bytes keep the raw text (the m27/m28 boundary again).

Carried items from the v0.38.0 probes, both recorded here:
(a) the numeric-strftime exemption class (`telemetry.py` `%H:%M`,
    `device.py` `%Y-%m-%d %H:%M:%S`) is locale-independent by construction —
    documented in `dates.py` and pinned by a directive allow-list;
(b) `ak820-smartd` stays OUT of the repo (personal-data coupling) — the
    decision is documented in README and locked structurally.

Zero hardware, zero network; no test depends on today's date.
"""

import ast
import contextlib
import io
import json
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

from ak820 import backlight, cli, configvalues, events, textwidth

# Hand-built countdown rows: the table FORMATTING is under test, not the
# countdown math (which owns its own fixtures), so the seam is mocked and
# every expected line below is computed by hand — no circular use of
# `display_width` to verify its own output.
ROWS_CJK = [
    {
        "title": "お盆休み",
        "next_date": date(2026, 11, 15),
        "days_left": 21,
        "milestone_str": "0 回忌",
        "theme_key": "soon",
    },
    {
        "title": "春节",
        "next_date": date(2026, 12, 31),
        "days_left": 67,
        "milestone_str": "Anniversary",
        "theme_key": "calm",
    },
    {
        "title": "Grandmother Birthday",
        "next_date": date(2027, 3, 14),
        "days_left": 140,
        "milestone_str": "Birthday",
        "theme_key": "calm",
    },
]
ROWS_ASCII = [
    {
        "title": "Spring Festival",
        "next_date": date(2026, 12, 31),
        "days_left": 67,
        "milestone_str": "Anniversary",
        "theme_key": "calm",
    },
    {
        "title": "Grandmother Birthday",
        "next_date": date(2027, 3, 14),
        "days_left": 140,
        "milestone_str": "Birthday",
        "theme_key": "calm",
    },
]


def _main(argv):
    """cli.main(argv) capturing stdout+stderr; SystemExit code becomes the rc."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cli.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


def _legacy_row(u: dict) -> str:
    """Verbatim pre-v0.39 row format (the parity oracle, m31b's pattern)."""
    title = configvalues.flatten_for_display(u["title"])
    milestone = configvalues.flatten_for_display(u["milestone_str"])
    return (
        f"{title:<22} {u['next_date']!s:<12} {str(u['days_left']) + 'd':<8} "
        f"{milestone:<12} {u['theme_key']}"
    )


def _imports_of(rel_path: str) -> set:
    tree = ast.parse((ROOT / rel_path).read_text(encoding="utf-8"))
    imported: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add("." * node.level + (node.module or ""))
    return imported


class TestDisplayWidth(unittest.TestCase):
    """The width rule, locked against hand-computed cell counts."""

    def test_ascii_and_empty(self):
        self.assertEqual(textwidth.display_width(""), 0)
        self.assertEqual(textwidth.display_width("Grandmother Birthday"), 20)

    def test_cjk_is_two_cells(self):
        self.assertEqual(textwidth.display_width("お盆休み"), 8)
        self.assertEqual(textwidth.display_width("春节"), 4)
        self.assertEqual(textwidth.display_width("日本語"), 6)

    def test_fullwidth_vs_halfwidth_forms(self):
        self.assertEqual(textwidth.display_width("Ａ"), 2)  # U+FF21 fullwidth A
        self.assertEqual(textwidth.display_width("ｱ"), 1)  # U+FF71 halfwidth a

    def test_combining_marks_add_no_cells(self):
        # Explicit escapes: a literal decomposed sequence is invisible and
        # an editor can silently recompose it (the exact codepoint IS the test).
        self.assertEqual(textwidth.display_width("e\u0301"), 1)  # e + COMBINING ACUTE
        self.assertEqual(textwidth.display_width("a\u0300\u0301"), 1)

    def test_zero_width_codepoints(self):
        self.assertEqual(textwidth.display_width("a\u200db"), 2)  # ZWJ adds no cell
        self.assertEqual(textwidth.display_width("\u200b\ufeff"), 0)  # ZWSP + BOM

    def test_ambiguous_counts_one(self):
        # The documented boundary: ambiguous = 1 cell (Western terminals).
        self.assertEqual(textwidth.display_width("\u00b0\u00e9\u2192"), 3)  # degree, e-acute, arrow

    def test_emoji_is_wide(self):
        self.assertEqual(textwidth.display_width("😀"), 2)

    def test_mixed(self):
        self.assertEqual(textwidth.display_width("xおy"), 4)


class TestPad(unittest.TestCase):
    """ljust semantics measured in cells; over-wide cells stay whole."""

    def test_ascii_parity_with_ljust(self):
        for s, w in (("", 5), ("ab", 5), ("ab", 2), ("abcdef", 3), ("Grandmother", 22)):
            with self.subTest(s=s, w=w):
                self.assertEqual(textwidth.pad(s, w), s.ljust(w))

    def test_cjk_pads_to_target_cells(self):
        padded = textwidth.pad("お盆休み", 22)
        self.assertEqual(padded, "お盆休み" + " " * 14)
        self.assertEqual(textwidth.display_width(padded), 22)

    def test_overwide_is_never_truncated(self):
        # Marked, not amputated (the v0.34/v0.36 overflow stance): the row
        # shifts right instead of losing text.
        self.assertEqual(textwidth.pad("日本語日本語日本語", 5), "日本語日本語日本語")

    def test_exact_fit_adds_nothing(self):
        self.assertEqual(textwidth.pad("ab", 2), "ab")


class TestEventsListTable(unittest.TestCase):
    """The m33 behavior: every column starts at the same cell in every row."""

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

    def test_cjk_row_is_the_exact_expected_line(self):
        lines = self._list(ROWS_CJK)
        expected = (
            "お盆休み" + " " * 14 + " "  # title padded to 22 CELLS (8 used)
            + "2026-11-15" + " " * 2 + " "  # date to 12
            + "21d" + " " * 5 + " "  # left to 8
            + "0 回忌" + " " * 6 + " "  # milestone to 12 CELLS (6 used)
            + "soon"
        )
        self.assertEqual(lines[2], expected)

    def test_every_column_starts_at_the_header_cell_in_every_row(self):
        lines = self._list(ROWS_CJK)
        header = lines[0]
        cols = {t: header.index(t) for t in ("NEXT DATE", "LEFT", "MILESTONE", "URGENCY")}
        self.assertEqual(list(cols.values()), [23, 36, 45, 58])
        for u, line in zip(ROWS_CJK, lines[2:]):
            date_tok = str(u["next_date"])
            left_tok = f"{u['days_left']}d"
            ms_tok = configvalues.flatten_for_display(u["milestone_str"])
            checks = (
                ("NEXT DATE", line.index(date_tok)),
                # searched AFTER the date so a title containing the token
                # ("Grandmother Birthday" holds "Birthday") cannot match early
                ("LEFT", line.index(left_tok, line.index(date_tok) + 1)),
                ("MILESTONE", line.rindex(ms_tok)),
                ("URGENCY", line.rindex(u["theme_key"])),
            )
            for token, pos in checks:
                with self.subTest(token=token, title=u["title"]):
                    self.assertEqual(textwidth.display_width(line[:pos]), cols[token])

    def test_ascii_rows_are_byte_identical_to_the_legacy_format(self):
        lines = self._list(ROWS_ASCII)
        legacy = [_legacy_row(u) for u in ROWS_ASCII]
        self.assertEqual(lines[2:], legacy)

    def test_hand_edited_break_still_flattened_before_padding(self):
        # m32 and m33 compose: flatten first (one row per event), pad second.
        row = dict(ROWS_ASCII[0], milestone_str="a\nb")
        lines = self._list([row])
        self.assertEqual(len(lines), 3)  # header + rule + exactly one row
        self.assertIn("a b" + " " * 9 + " calm", lines[2])

    def test_overwide_cjk_title_is_marked_not_amputated(self):
        row = dict(ROWS_ASCII[0], title="日本語のタイトルが長すぎる場合")
        lines = self._list([row])
        self.assertIn("日本語のタイトルが長すぎる場合", lines[2])  # whole, shifted right

    def test_json_output_keeps_raw_codepoints_and_no_padding(self):
        with open(self.cfg, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "events": [
                        {
                            "id": "obon",
                            "title": "お盆休み",
                            "short_title": "OBON",
                            "date": "2026-11-15",
                            "category": "milestone",
                        }
                    ]
                },
                fh,
            )
        rc, out, err = _main(["events", "list", "--config", self.cfg, "--json"])
        self.assertEqual(rc, 0, out + err)
        rows = json.loads(out)
        self.assertEqual(rows[0]["title"], "お盆休み")  # raw codepoints, unpadded


class TestStoredBytesBoundary(unittest.TestCase):
    """The m27/m28 boundary again: padding is display-only, never stored."""

    def test_add_with_cjk_title_stores_the_raw_title(self):
        tmp = tempfile.TemporaryDirectory()
        cfg = os.path.join(tmp.name, "events.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"events": []}, fh)
        rc, out, err = _main(
            [
                "events",
                "add",
                "--short",
                "obon",
                "--title",
                "お盆休み",
                "--date",
                "2026-11-15",
                "--category",
                "milestone",
                "--config",
                cfg,
            ]
        )
        self.assertEqual(rc, 0, out + err)
        with open(cfg, encoding="utf-8") as fh:
            stored = json.load(fh)["events"][0]
        self.assertEqual(stored["title"], "お盆休み")
        self.assertEqual(stored["title"].strip(), stored["title"])  # no pad spaces
        tmp.cleanup()


class TestSourceInvariants(unittest.TestCase):
    """Anti-recurrence: the print seams pad by width, from one home, forever."""

    def test_no_char_count_padding_of_dynamic_values_in_print_seams(self):
        # Every `:<N` format spec in the two table modules must format a
        # string CONSTANT (the header rows); dynamic columns go through pad.
        for rel in ("ak820/config.py", "ak820/backlight.py"):
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.FormattedValue) and node.format_spec:
                    spec = "".join(
                        p.value
                        for p in node.format_spec.values
                        if isinstance(p, ast.Constant) and isinstance(p.value, str)
                    )
                    if "<" in spec:
                        with self.subTest(rel=rel, spec=spec):
                            self.assertIsInstance(node.value, ast.Constant)

    def test_ljust_is_banned_in_the_print_seams(self):
        for rel in ("ak820/config.py", "ak820/backlight.py"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn(".ljust(", src, rel)

    def test_both_tables_route_every_dynamic_column_through_pad(self):
        cfg_src = (ROOT / "ak820" / "config.py").read_text(encoding="utf-8")
        bl_src = (ROOT / "ak820" / "backlight.py").read_text(encoding="utf-8")
        self.assertEqual(cfg_src.count("textwidth.pad("), 4)  # title/date/left/milestone
        self.assertEqual(bl_src.count("textwidth.pad("), 4)  # key/name/mode/color

    def test_textwidth_is_a_stdlib_leaf(self):
        imported = _imports_of("ak820/textwidth.py")
        self.assertEqual(imported, {"unicodedata"})

    def test_print_seams_import_textwidth_at_module_level(self):
        for rel in ("ak820/config.py", "ak820/backlight.py"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIsNotNone(
                re.search(r"^from \. import .*\btextwidth\b", src, re.M),
                f"{rel} must import textwidth at module level",
            )

    def test_module_budgets_hold(self):
        for rel in ("ak820/textwidth.py", "ak820/config.py", "ak820/backlight.py"):
            n = len((ROOT / rel).read_text(encoding="utf-8").splitlines())
            self.assertLess(n, 300, f"{rel} is {n} lines")

    def test_textwidth_is_pil_free_and_import_cycle_free(self):
        # Fresh interpreter: importing the leaf and both consumers must work
        # with nothing else of the package preloaded.
        code = (
            f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
            "import ak820.textwidth, ak820.config, ak820.backlight; print('ok')"
        )
        done = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("ok", done.stdout)


class TestStrftimeExemptionClass(unittest.TestCase):
    """Carried item (a): numeric strftime sites are locale-independent by
    construction — documented next to the `%b` ban and pinned by allow-list."""

    # Locale-INDEPENDENT directives (digits only). Locale-dependent ones
    # (%a %A %b %B %c %p %x %X %Z ...) are unrepresentable by this lock.
    ALLOWED = set("YmdHMSIjfzeGVUWu%")
    DIRECTIVE = re.compile(r"%[-+0#^0lh]*([a-zA-Z%])")

    def _strftime_formats(self):
        rels = [f"ak820/{p.name}" for p in sorted((ROOT / "ak820").glob("*.py"))]
        rels += ["milestone_radar.py", "widgets.py", "ak820_ctrl.py"]
        found = []
        for rel in rels:
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "strftime"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    found.append((rel, node.args[0].value))
        return found

    def test_every_shipped_strftime_format_is_numeric_only(self):
        formats = self._strftime_formats()
        self.assertTrue(formats, "the known numeric sites vanished — re-check")
        for rel, fmt in formats:
            for directive in self.DIRECTIVE.findall(fmt):
                with self.subTest(rel=rel, fmt=fmt, directive=directive):
                    self.assertIn(directive, self.ALLOWED)

    def test_the_two_known_sites_still_exist(self):
        tel = (ROOT / "ak820" / "telemetry.py").read_text(encoding="utf-8")
        dev = (ROOT / "ak820" / "device.py").read_text(encoding="utf-8")
        self.assertIn('strftime("%H:%M")', tel)
        self.assertIn("strftime('%Y-%m-%d %H:%M:%S')", dev)

    def test_the_exemption_is_documented_at_its_home_and_sites(self):
        dates_src = (ROOT / "ak820" / "dates.py").read_text(encoding="utf-8")
        self.assertIn("Numeric-strftime exemption class", dates_src)
        for rel in ("ak820/telemetry.py", "ak820/device.py"):
            self.assertIn("exemption class", (ROOT / rel).read_text(encoding="utf-8"), rel)


class TestSmartdBoundary(unittest.TestCase):
    """Carried item (b): DECIDED (v0.39.0) NOT to vendor `ak820-smartd`.

    The daemon's unit and script hard-code one machine's paths and probe
    personal tooling from `~/personal-data` (git-backed there); vendoring
    them would couple this repo to one machine's layout. `systemd/` ships
    only the generic units; the decision is documented in README.
    """

    def test_repo_ships_no_smartd_unit(self):
        units = sorted(p.name for p in (ROOT / "systemd").iterdir())
        self.assertFalse([u for u in units if "smartd" in u], units)
        for p in (ROOT / "systemd").iterdir():
            self.assertNotIn("smartd", p.read_text(encoding="utf-8"), p.name)

    def test_readme_documents_the_boundary(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("ak820-smartd", readme)
        self.assertIn("personal-data", readme)


if __name__ == "__main__":
    unittest.main()
