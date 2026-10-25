"""v0.40.0 — the print seam's last unaligned input: control characters (m34).

Live-reproved before fixing: `validate_single_line` accepted `A\tB` — a TAB is
not a line break — the CLI stored it (exit 0), and `display_width` counted the
tab as ONE cell while the terminal advances to the next tab stop, shifting
every cell after it in the row. `\x01` is the same class: padded as one cell,
advanced as zero. The table geometry was duplicated magic numbers too: widths
22/12/8/12 in the header literal AND every `pad` call, separators `"-" * 68`
and `"-" * 80` sitting under 65- and 69-cell headers that never derived them.

DECIDED (v0.40.0):
(1) TAB joins the single-line vocabulary at BOTH homes, exactly the m31b+m32
    dual structure: the CLI refuses what it is about to CREATE (a tab is as
    unrenderable on a line as a break), the display seam flattens what a
    hand-edited config already holds (the m27/m28/m29/m31b trust boundary).
    The rule closes the CLASS, not the instance — every Unicode Cc control
    character has a terminal-defined, unpredictable advance — so the
    vocabulary is `[\\x00-\\x1f\\x7f-\\x9f\\u2028\\u2029]` (Cc plus the two
    separators the break set already carried), and the two homes stay locked
    equal the way v0.36/v0.37 locked the break twins.
(2) Table geometry has ONE home: new `ak820/tables.py` holds every column
    width as a constant, the header and every pad call name it, and the rule
    is as wide as the header (`textwidth.rule`) — 65 and 69 cells, replacing
    the undriven 68 and 80. The formatters are PURE, so `--json` and stored
    bytes cannot reach flattening or padding by construction.

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

from ak820 import backlight, cli, configvalues, events, screen, tables, textwidth
from ak820.backlight import SHOWCASE_PRESETS
from ak820.errors import ConfigError

# Every Cc spelling that is NOT already a line break (those keep their own
# message and their v0.36 lock). Explicit escapes — a literal control char is
# invisible in an editor and the exact codepoint IS the test.
CONTROL_CHARS = ["\t", "\x00", "\x01", "\x08", "\x0e", "\x1b", "\x1f", "\x7f", "\x80", "\x86", "\x9f"]
BREAK_CHARS = ["\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"]

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


def _legacy_events_row(u: dict) -> str:
    """Verbatim pre-v0.40 row format (the parity oracle, m31b's pattern)."""
    title = configvalues.flatten_for_display(u["title"])
    milestone = configvalues.flatten_for_display(u["milestone_str"])
    return (
        f"{title:<22} {u['next_date']!s:<12} {str(u['days_left']) + 'd':<8} "
        f"{milestone:<12} {u['theme_key']}"
    )


def _legacy_preset_row(p: dict) -> str:
    """Verbatim pre-v0.40 preset row (ASCII parity oracle — widths as literals)."""
    return f"{p['key']:<18} {p['name']:<20} {str(p['mode']):<6} {p['color']:<10} {p['desc']}"


class TestControlCharRejectedUnit(unittest.TestCase):
    """The CLI guard's widened unit contract (m34)."""

    def test_every_control_char_is_rejected(self):
        for ch in CONTROL_CHARS:
            with self.subTest(ch=repr(ch)):
                with self.assertRaises(ConfigError) as cm:
                    configvalues.validate_single_line(f"a{ch}b", "--note 'x'")
                msg = str(cm.exception)
                self.assertIn("--note", msg)
                self.assertIn("TAB", msg)
                self.assertIn("space", msg)

    def test_line_break_message_still_wins(self):
        # Ordering (v0.36): a string holding both names the break — the more
        # fundamental problem — not the generic control message.
        with self.assertRaises(ConfigError) as cm:
            configvalues.validate_single_line("a\n\tb", "--x")
        self.assertIn("line break", str(cm.exception))
        self.assertNotIn("TAB", str(cm.exception))

    def test_control_free_text_passes_unchanged(self):
        for value in ["Turns 5", "お盆休み", "a b", "", "A-B", "a  b"]:
            with self.subTest(value=value):
                self.assertIs(configvalues.validate_single_line(value, "--x"), value)


class TestCliRejectsControlChars(unittest.TestCase):
    """The creation path refuses what no renderer can align."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = f"{self.tmp}/events.json"
        with open(self.cfg, "w", encoding="utf-8") as f:
            json.dump({"events": []}, f)
        self.before = Path(self.cfg).read_bytes()

    def _add(self, *extra):
        return _main(
            [
                "events",
                "add",
                "--config",
                self.cfg,
                "--title",
                "T",
                "--short",
                "S",
                "--date",
                "06-01",
                *extra,
            ]
        )

    def test_events_flags_with_tab_exit_6_and_save_nothing(self):
        for flag in ["--note", "--short", "--title"]:
            with self.subTest(flag=flag):
                rc, _out, err = self._add(flag, "A\tB")
                self.assertEqual(rc, 6)
                self.assertIn(flag, err)
                self.assertIn("TAB", err)
                self.assertEqual(Path(self.cfg).read_bytes(), self.before, "config was written")
                self.assertNotIn("Traceback", err)

    def test_card_flags_with_tab_exit_6_on_dry_run(self):
        base = [
            "--dry-run",
            "card",
            "--title",
            "REMINDER",
            "--value",
            "27 DAYS",
            "--sub",
            "s",
            "--footer",
            "f",
        ]
        for flag in ["--title", "--value", "--sub", "--footer"]:
            argv = list(base)
            argv[argv.index(flag) + 1] = "a\tb"
            with self.subTest(flag=flag):
                rc, _out, err = _main(argv)
                self.assertEqual(rc, 6)
                self.assertIn(flag, err)
                self.assertIn("TAB", err)

    def test_valid_forms_still_pass(self):
        rc, _out, _err = self._add("--note", "Turns {years} today")
        self.assertEqual(rc, 0)
        rc, _out, _err = self._add("--title", "お盆休み")
        self.assertEqual(rc, 0)


class TestControlFlattenTwin(unittest.TestCase):
    """One rule, two homes (v0.36/v0.37 precedent): the twins widened together."""

    SPELLINGS = [f"a{c}b" for c in CONTROL_CHARS + BREAK_CHARS] + [
        "a\t \tb",
        "\t",
        "a\n\tb",
        "  padded\tvalue  ",
        "plain",
        "",
        "a  b",  # whitespace run WITHOUT a control char: trigger stays closed
    ]

    def test_pattern_is_one_pattern_in_two_homes(self):
        self.assertEqual(configvalues._CONTROL_RE.pattern, screen._CONTROL_RE.pattern)

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

    def test_tab_flattens_to_one_space(self):
        self.assertEqual(configvalues.flatten_for_display("A\tB"), "A B")
        self.assertEqual(configvalues.flatten_for_display("a \t b"), "a b")
        self.assertEqual(configvalues.flatten_for_display("\t"), "")

    def test_control_free_text_is_byte_identical(self):
        for text in ("DAUGHTER", "a  b", "Turns 5", ""):
            with self.subTest(text=text):
                self.assertEqual(configvalues.flatten_for_display(text), text)

    def test_flattening_is_idempotent_and_control_free(self):
        for text in self.SPELLINGS:
            once = configvalues.flatten_for_display(text)
            self.assertIsNone(configvalues._CONTROL_RE.search(once), repr(text))
            self.assertEqual(configvalues.flatten_for_display(once), once)

    def test_break_only_text_output_is_unchanged_by_the_widening(self):
        # The Cc set is a superset of the break set; the collapse step absorbs
        # the difference, so every v0.35/v0.37 break output is byte-identical.
        for text, expected in [
            ("a\nb", "a b"),
            ("line one\n\nline two", "line one line two"),
            ("  padded\nvalue  ", "padded value"),
            ("a\u2028b\u2029c", "a b c"),
        ]:
            with self.subTest(text=text):
                self.assertEqual(configvalues.flatten_for_display(text), expected)


class TestHandEditedTabAtPrintSeam(unittest.TestCase):
    """CLI 6 / loader 0: stored control chars load, print aligned, stay raw."""

    ROW = {
        "title": "A\tB",
        "next_date": date(2026, 12, 1),
        "days_left": 30,
        "milestone_str": "Turns\t5",
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

    def test_tabbed_row_prints_one_aligned_line(self):
        lines = self._list().splitlines()
        self.assertEqual(len(lines), 3)  # header + rule + exactly one row
        row = lines[2]
        self.assertNotIn("\t", row)
        self.assertIn("A B", row)
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

    def test_json_keeps_the_raw_control_characters(self):
        rows = json.loads(self._list("--json"))
        self.assertEqual(rows[0]["title"], "A\tB")
        self.assertEqual(rows[0]["milestone_str"], "Turns\t5")


class TestRulePrimitive(unittest.TestCase):
    def test_rule_is_as_wide_as_its_text(self):
        self.assertEqual(textwidth.rule("abc"), "---")
        self.assertEqual(textwidth.rule(""), "")
        self.assertEqual(textwidth.rule("お盆"), "----")  # 4 CELLS, not 2 chars
        self.assertEqual(textwidth.rule("ab", "="), "==")


class TestTableGeometryOneHome(unittest.TestCase):
    """m34: widths are constants, the rule is derived, nothing duplicates."""

    def test_rule_matches_the_header_exactly_in_both_tables(self):
        for lines in (tables.format_events_table([]), tables.format_preset_table([])):
            with self.subTest(header=lines[0][:12]):
                self.assertEqual(len(lines[1]), textwidth.display_width(lines[0]))

    def test_derived_rule_lengths_are_pinned(self):
        # v0.39 shipped "-" * 68 under a 65-cell header and "-" * 80 under a
        # 69-cell one. The derived values REPLACE the undriven magic numbers.
        self.assertEqual(tables.format_events_table([])[1], "-" * 65)
        self.assertEqual(tables.format_preset_table([])[1], "-" * 69)

    def test_header_bytes_are_unchanged_by_the_refactor(self):
        self.assertEqual(
            tables.format_events_table([])[0],
            "EVENT                  NEXT DATE    LEFT     MILESTONE    URGENCY",
        )

    def test_pad_widths_are_named_constants_never_literals(self):
        tree = ast.parse((ROOT / "ak820" / "tables.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "pad"
            ):
                self.assertEqual(len(node.args), 2)
                self.assertIsInstance(node.args[1], ast.Name, "pad width must be a constant")

    def test_no_dash_times_number_literal_in_the_tables(self):
        # AST, not source text: the module docstring names the dead `"-" * 68`
        # on purpose — prose may name what code may not do (v0.37 precedent).
        tree = ast.parse((ROOT / "ak820" / "tables.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.BinOp)
                and isinstance(node.op, ast.Mult)
                and isinstance(node.left, ast.Constant)
                and node.left.value == "-"
            ):
                self.fail("rule length must come from textwidth.rule, not a literal")

    def test_each_width_constant_is_assigned_exactly_once(self):
        src = (ROOT / "ak820" / "tables.py").read_text(encoding="utf-8")
        for name in (
            "EVENT_COL_W",
            "DATE_COL_W",
            "LEFT_COL_W",
            "MILESTONE_COL_W",
            "PRESET_KEY_W",
            "PRESET_NAME_W",
            "MODE_COL_W",
            "COLOR_COL_W",
        ):
            with self.subTest(name=name):
                self.assertEqual(len(re.findall(rf"^{name} = \d+$", src, re.M)), 1)

    def test_events_rows_are_byte_identical_to_the_legacy_format(self):
        def fake(cfg, today=None):
            return [dict(r) for r in ROWS_ASCII]

        tmp = tempfile.TemporaryDirectory()
        cfg = os.path.join(tmp.name, "events.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"events": []}, fh)
        with mock.patch.object(events, "compute_upcoming_events", side_effect=fake):
            rc, out, err = _main(["events", "list", "--config", cfg])
        tmp.cleanup()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(out.splitlines()[2:], [_legacy_events_row(u) for u in ROWS_ASCII])

    def test_preset_rows_are_byte_identical_to_the_legacy_format(self):
        lines = tables.format_preset_table(SHOWCASE_PRESETS)
        self.assertEqual(lines[2:], [_legacy_preset_row(p) for p in SHOWCASE_PRESETS])


class TestTablesLeaf(unittest.TestCase):
    """The new module is a legal, cycle-free, PIL-free print seam."""

    def _imports_of(self, rel_path: str) -> set:
        tree = ast.parse((ROOT / rel_path).read_text(encoding="utf-8"))
        imported: set = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                if node.module is None:  # `from . import a, b` — one entry per name
                    imported |= {"." + a.name for a in node.names}
                else:
                    imported.add("." * node.level + node.module)
        return imported

    def test_tables_imports_only_the_two_leaves(self):
        self.assertEqual(self._imports_of("ak820/tables.py"), {".configvalues", ".textwidth"})

    def test_fresh_interpreter_import_is_cycle_free(self):
        code = (
            f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
            "import ak820.tables, ak820.config, ak820.backlight; print('ok')"
        )
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("ok", done.stdout)

    def test_module_budget_holds(self):
        n = len((ROOT / "ak820" / "tables.py").read_text(encoding="utf-8").splitlines())
        self.assertLess(n, 300, f"ak820/tables.py is {n} lines")

    def test_delegating_seams_no_longer_format_by_hand(self):
        # One home: neither delegating module pads, flattens, or counts widths.
        for rel in ("ak820/config.py", "ak820/backlight.py"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("textwidth.pad(", src, rel)
            self.assertNotIn("flatten_for_display", src, rel)


class TestBacklightListEndToEnd(unittest.TestCase):
    """The delegating seam still prints the table + its domain footer."""

    def test_backlight_list_prints_table_and_footer(self):
        rc, out, err = _main(["backlight", "list"])
        self.assertEqual(rc, 0, out + err)
        lines = out.splitlines()
        expected = tables.format_preset_table(SHOWCASE_PRESETS)
        self.assertEqual(lines[: len(expected)], expected)
        # the footer print starts with "\n" — one blank line before the text
        self.assertIn("Supported mode names", "\n".join(lines[len(expected) :]))


if __name__ == "__main__":
    unittest.main()
