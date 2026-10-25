"""v0.33.0 — m29: `--note` on a category that never renders it, and `--clear-note`.

Live repro (before the fix, proven against the real CLI):

    events add --category birthday --note "ignored {years}"
    -> exit 0, stored note 'ignored {years}', `events list` rendered `Turns 37`
    events add --category anniversary --note "note says {years}"
    -> exit 0, stored, rendered `7 Years`

`compute_upcoming_events` consults `note` in the milestone branch ONLY, so the
flag was accepted, persisted, and never rendered — a silent no-op. The related
gap m28 left open had the same shape: omitting `--note` preserves the stored
note (v0.27.0) and blank is rejected (m28), so JSON hand-editing was the only
way to REMOVE a note.

DECIDED (v0.33.0):
* CLI — reject the inert combination as ConfigError 6 (`validate_note_category`,
  the m18/m27/m28 stance: never silently accept input that does nothing). The
  check runs against the RESULTING entry, so it also catches the note the m25
  prior-copy PRESERVES when `--category` switches a noted milestone to a
  birthday — the mirror path of the same defect.
* Loader — permissive, unchanged (m27/m28 boundary): a hand-edited birthday+note
  config still loads and renders `Turns N`; rejecting data the user already
  wrote would brick whole configs. Asymmetry locked as a table (CLI 6 / loader 0).
* `--clear-note` — an explicit flag, not a `--note -` sentinel (a sentinel
  collides with real note text and argparse treats leading dashes as options).
  The key goes ABSENT, not `""`. Combining it with `--note` contradicts itself
  -> exit 6, checked before the blank rule so the message names the real problem.
* Category vocabulary — ONE home (`configvalues.EVENT_CATEGORIES`), feeding the
  parser's `--category` choices AND the renderer, with `NOTE_INERT_CATEGORIES`
  derived from it, so a new category cannot be added without this rule knowing
  (the m23 bounds pattern).

Zero hardware, zero network.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(ROOT))

from ak820 import cli, configvalues, events
from ak820.errors import ConfigError
from ak820.parser import build_parser

# The exact note text used in the v0.32.0 live probes.
REPRO_NOTE = "ignored {years}"


def _main(argv):
    """cli.main(argv) capturing stdout+stderr; SystemExit code becomes the rc."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cli.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


def _write_cfg(tmp, name, cfg):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    return path


def _snapshot(path):
    with open(path, "rb") as f:
        return f.read(), os.stat(path).st_mtime_ns


def _add_argv(path, *, category=None, note=None, clear=False, title="Probe", short="PROBE"):
    argv = ["events", "add", "--config", path, "--title", title, "--short", short, "--date", "05-01"]
    if category is not None:
        argv += ["--category", category]
    if note is not None:
        argv += ["--note", note]
    if clear:
        argv += ["--clear-note"]
    return argv


def _entry(path, ev_id="probe"):
    for e in json.load(open(path))["events"]:
        if e["id"] == ev_id:
            return e
    raise AssertionError(f"no entry {ev_id!r} in {path}")


def _rows(cfg):
    """Rows through the real load choke point (coercion + shape checks)."""
    return events.compute_upcoming_events(events.load_events_config(cfg))


def _add_parser_actions():
    p = build_parser()
    ev = p._subparsers._group_actions[0].choices["events"]  # type: ignore[attr-defined]
    add = ev._subparsers._group_actions[0].choices["add"]  # type: ignore[attr-defined]
    return {a.dest: a for a in add._actions}


class TestInertNoteRejectedAtCli(unittest.TestCase):
    """m29: a note its category never renders dies as exit 6 instead of being stored."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_live_repro_birthday_note_now_exits_6(self):
        path = _write_cfg(self.tmp, "repro.json", {"events": []})
        before = _snapshot(path)
        rc, _, err = _main(_add_argv(path, category="birthday", note=REPRO_NOTE))
        self.assertEqual(rc, 6)
        self.assertEqual(_snapshot(path), before, "rejected add must not touch the file")
        self.assertNotIn("Traceback", err)
        self.assertEqual(json.load(open(path))["events"], [])

    def test_live_repro_anniversary_note_now_exits_6(self):
        path = _write_cfg(self.tmp, "anniv.json", {"events": []})
        rc, _, err = _main(_add_argv(path, category="anniversary", note="note says {years}"))
        self.assertEqual(rc, 6)
        self.assertIn("anniversary", err)
        self.assertEqual(json.load(open(path))["events"], [])

    def test_rejection_names_flag_category_and_both_fixes(self):
        path = _write_cfg(self.tmp, "msg.json", {"events": []})
        rc, _, err = _main(_add_argv(path, category="birthday", note=REPRO_NOTE))
        self.assertEqual(rc, 6)
        for hint in ("--note", "birthday", "--clear-note", "--category milestone"):
            self.assertIn(hint, err, f"message must name {hint}")

    def test_note_without_placeholder_is_still_inert(self):
        # inertness comes from the CATEGORY, not from the {years} placeholder
        path = _write_cfg(self.tmp, "plain.json", {"events": []})
        rc, _, _ = _main(_add_argv(path, category="birthday", note="plain text"))
        self.assertEqual(rc, 6)

    def test_milestone_note_still_accepted(self):
        path = _write_cfg(self.tmp, "mile.json", {"events": []})
        rc, out, _ = _main(_add_argv(path, category="milestone", note="Shipped {years} yrs"))
        self.assertEqual(rc, 0)
        self.assertIn("Saved", out)
        self.assertEqual(_entry(path)["note"], "Shipped {years} yrs")

    def test_birthday_without_note_still_accepted(self):
        path = _write_cfg(self.tmp, "bd.json", {"events": []})
        rc, _, _ = _main(_add_argv(path, category="birthday"))
        self.assertEqual(rc, 0)
        self.assertNotIn("note", _entry(path))

    def test_anniversary_without_note_still_accepted(self):
        path = _write_cfg(self.tmp, "an.json", {"events": []})
        rc, _, _ = _main(_add_argv(path, category="anniversary"))
        self.assertEqual(rc, 0)
        self.assertEqual(_entry(path)["category"], "anniversary")


class TestPreservedNotePath(unittest.TestCase):
    """The mirror defect: a re-add PRESERVES a note (m25) that the new category never renders."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_switch_to_birthday_with_stored_note_rejected(self):
        path = _write_cfg(
            self.tmp,
            "switch.json",
            {"events": [{"id": "probe", "title": "Probe", "short_title": "PROBE",
                         "date": "05-01", "category": "milestone", "note": "Kept {years}"}]},
        )
        before = _snapshot(path)
        rc, _, err = _main(_add_argv(path, category="birthday"))  # no --note at all
        self.assertEqual(rc, 6)
        self.assertIn("the stored note", err)
        self.assertEqual(_snapshot(path), before)

    def test_clear_note_is_the_escape_hatch(self):
        path = _write_cfg(
            self.tmp,
            "escape.json",
            {"events": [{"id": "probe", "title": "Probe", "short_title": "PROBE",
                         "date": "05-01", "category": "milestone", "note": "Kept {years}"}]},
        )
        rc, _, _ = _main(_add_argv(path, category="birthday", clear=True))
        self.assertEqual(rc, 0)
        entry = _entry(path)
        self.assertEqual(entry["category"], "birthday")
        self.assertNotIn("note", entry)

    def test_birthday_to_milestone_revives_stored_note(self):
        # the loader accepts the inert hand-edited pair; switching TO milestone
        # makes it live again, which is legal and must not be rejected
        path = _write_cfg(
            self.tmp,
            "revive.json",
            {"events": [{"id": "probe", "title": "Probe", "short_title": "PROBE",
                         "date": "05-01", "category": "birthday", "note": "Now visible {years}"}]},
        )
        rc, _, _ = _main(_add_argv(path, category="milestone"))
        self.assertEqual(rc, 0)
        self.assertEqual(_entry(path)["note"], "Now visible {years}")
        self.assertIn("Now visible", _rows(path)[0]["milestone_str"])

    def test_identical_readd_of_hand_edited_inert_entry_is_rejected(self):
        # DECIDED: the CLI never affirms an inert note, even when nothing would
        # change — the m20 no-op shortcut sits AFTER the rule, not before it.
        path = _write_cfg(
            self.tmp,
            "identical.json",
            {"events": [{"id": "probe", "title": "Probe", "short_title": "PROBE",
                         "date": "05-01", "category": "birthday", "note": "inert"}]},
        )
        before = _snapshot(path)
        rc, _, _ = _main(_add_argv(path, category="birthday"))
        self.assertEqual(rc, 6)
        self.assertEqual(_snapshot(path), before)


class TestClearNote(unittest.TestCase):
    """m29's companion gap: a CLI path that REMOVES a stored note."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = _write_cfg(
            self.tmp,
            "cn.json",
            {"events": [{"id": "probe", "title": "Probe", "short_title": "PROBE",
                         "date": "05-01", "category": "milestone", "note": "Old {years}"}]},
        )

    def test_clear_note_removes_the_key_not_empty_string(self):
        rc, out, _ = _main(_add_argv(self.path, clear=True))
        self.assertEqual(rc, 0)
        self.assertIn("Saved", out)
        self.assertNotIn("note", _entry(self.path))

    def test_cleared_entry_renders_the_documented_fallback(self):
        _main(_add_argv(self.path, clear=True))
        self.assertEqual(_rows(self.path)[0]["milestone_str"], "Milestone")

    def test_clear_note_without_note_is_an_m20_noop(self):
        _main(_add_argv(self.path, clear=True))
        before = _snapshot(self.path)
        rc, out, _ = _main(_add_argv(self.path, clear=True))
        self.assertEqual(rc, 0)
        self.assertIn("already up to date", out)
        self.assertEqual(_snapshot(self.path), before, "no-op must not touch bytes or mtime")

    def test_clear_note_on_new_entry_stores_no_note_key(self):
        path = _write_cfg(self.tmp, "fresh.json", {"events": []})
        rc, _, _ = _main(_add_argv(path, clear=True))
        self.assertEqual(rc, 0)
        self.assertNotIn("note", _entry(path))

    def test_note_plus_clear_note_contradiction_exits_6(self):
        before = _snapshot(self.path)
        rc, _, err = _main(_add_argv(self.path, note="both {years}", clear=True))
        self.assertEqual(rc, 6)
        self.assertIn("--note", err)
        self.assertIn("--clear-note", err)
        self.assertEqual(_snapshot(self.path), before)

    def test_contradiction_is_checked_before_the_blank_rule(self):
        # `--note ""` alone is m28's error; with --clear-note the contradiction
        # is the real problem and must be what the user is told.
        rc, _, err = _main(_add_argv(self.path, note="", clear=True))
        self.assertEqual(rc, 6)
        self.assertIn("contradict", err)

    def test_clear_note_preserves_unknown_extra_fields(self):
        # m25: user data is never silently dropped — only the note goes
        path = _write_cfg(
            self.tmp,
            "extras.json",
            {"events": [{"id": "probe", "title": "Probe", "short_title": "PROBE",
                         "date": "05-01", "category": "milestone", "note": "x",
                         "icon": "rocket", "priority": 1}]},
        )
        rc, _, _ = _main(_add_argv(path, clear=True))
        self.assertEqual(rc, 0)
        entry = _entry(path)
        self.assertNotIn("note", entry)
        self.assertEqual(entry["icon"], "rocket")
        self.assertEqual(entry["priority"], 1)


class TestCategoryNoteRenderTable(unittest.TestCase):
    """The render semantics of 3 categories x note, explicit and date-independent."""

    def _row(self, category, note=None, date="1990-03-04"):
        ev = {"id": "t", "title": "T", "short_title": "T", "date": date, "category": category}
        if note is not None:
            ev["note"] = note
        return events.compute_upcoming_events({"events": [ev]})[0]

    def test_birthday_and_anniversary_ignore_the_note(self):
        for category in ("birthday", "anniversary"):
            with self.subTest(category=category):
                bare = self._row(category)
                noted = self._row(category, "NEVER RENDERED {years}")
                self.assertEqual(bare["milestone_str"], noted["milestone_str"])
                self.assertEqual(bare["short_age"], noted["short_age"])

    def test_birthday_and_anniversary_fixed_text(self):
        for category, expected in (
            ("birthday", lambda y: f"Turns {y}"),
            ("anniversary", lambda y: f"{y} Years"),
        ):
            row = self._row(category, note="ignored")
            self.assertGreater(row["years"], 0, "fixture must yield a past year")
            self.assertEqual(row["milestone_str"], expected(row["years"]))

    def test_milestone_renders_the_note(self):
        row = self._row("milestone", "Shipped {years} yrs")
        self.assertEqual(row["milestone_str"], f"Shipped {row['years']} yrs")
        self.assertEqual(row["short_age"], row["milestone_str"])

    def test_milestone_without_note_falls_back(self):
        self.assertEqual(self._row("milestone")["milestone_str"], "Milestone")

    def test_milestone_with_empty_note_falls_back(self):
        # m28: "" is the legal explicit "no note" spelling
        self.assertEqual(self._row("milestone", "")["milestone_str"], "Milestone")

    def test_unknown_category_takes_the_note_branch(self):
        # hand-edited categories are note-driven — why validate_note_category
        # must NOT treat "unknown" as inert
        row = self._row("wedding", "Tied {years} yrs ago")
        self.assertEqual(row["milestone_str"], f"Tied {row['years']} yrs ago")

    def test_trust_boundary_cli_rejects_loader_accepts(self):
        # the asymmetry table (m27/m28 class): CLI 6 / loader 0
        tmp = tempfile.mkdtemp()
        for category in ("birthday", "anniversary"):
            with self.subTest(category=category):
                path = _write_cfg(
                    tmp, f"tb-{category}.json",
                    {"events": [{"id": "probe", "title": "Probe", "short_title": "PROBE",
                                 "date": "05-01", "category": category, "note": "inert {years}"}]},
                )
                rows = _rows(path)  # loader: permissive, renders fixed text
                self.assertNotIn("inert", rows[0]["milestone_str"])
                rc, _, _ = _main(_add_argv(path, category=category, note="inert {years}"))
                self.assertEqual(rc, 6)  # CLI: refuses to create/affirm it


class TestValidateNoteCategoryContract(unittest.TestCase):
    """Unit contract of the single-home rule itself."""

    def test_table(self):
        cases = [
            ("milestone", "note {years}", None),
            ("milestone", "", None),
            ("milestone", None, None),
            ("birthday", "note", ConfigError),
            ("anniversary", "note", ConfigError),
            ("birthday", "", None),  # nothing inert to store
            ("birthday", None, None),
            ("wedding", "note", None),  # unknown category renders the note
        ]
        for category, note, raises in cases:
            with self.subTest(category=category, note=note):
                if raises is None:
                    self.assertIsNone(
                        configvalues.validate_note_category(category, note, "--note 'x'")
                    )
                else:
                    with self.assertRaises(raises):
                        configvalues.validate_note_category(category, note, "--note 'x'")

    def test_message_names_origin_and_category(self):
        with self.assertRaises(ConfigError) as ctx:
            configvalues.validate_note_category("birthday", "x", "the stored note")
        msg = str(ctx.exception)
        self.assertIn("the stored note", msg)
        self.assertIn("--category birthday", msg)


class TestOneHomeAntiRecurrence(unittest.TestCase):
    """The category vocabulary has one home; the rule cannot be bypassed."""

    def test_vocabulary_is_derived(self):
        self.assertEqual(
            configvalues.EVENT_CATEGORIES,
            (configvalues.CATEGORY_BIRTHDAY, configvalues.CATEGORY_ANNIVERSARY,
             configvalues.CATEGORY_MILESTONE),
        )
        self.assertEqual(
            configvalues.NOTE_INERT_CATEGORIES,
            (configvalues.CATEGORY_BIRTHDAY, configvalues.CATEGORY_ANNIVERSARY),
        )

    def test_parser_choices_come_from_the_constant(self):
        src = (ROOT / "ak820" / "parser.py").read_text(encoding="utf-8")
        self.assertIn("choices=list(EVENT_CATEGORIES)", src)
        self.assertIn("default=CATEGORY_MILESTONE", src)
        for literal in ('"birthday"', '"anniversary"', '"milestone"'):
            self.assertNotIn(literal, src, f"parser.py must not hardcode {literal}")

    def test_parser_surface_matches_constant(self):
        actions = _add_parser_actions()
        self.assertEqual(actions["category"].choices, list(configvalues.EVENT_CATEGORIES))
        self.assertEqual(actions["category"].default, configvalues.CATEGORY_MILESTONE)

    def test_renderer_uses_the_constants(self):
        src = (ROOT / "ak820" / "events.py").read_text(encoding="utf-8")
        for literal in ('"birthday"', '"anniversary"'):
            self.assertNotIn(literal, src, f"events.py must render via configvalues {literal}")
        self.assertIn("configvalues.CATEGORY_BIRTHDAY", src)
        self.assertIn("configvalues.CATEGORY_ANNIVERSARY", src)

    def test_cmd_events_validates_the_resulting_entry(self):
        src = (ROOT / "ak820" / "config.py").read_text(encoding="utf-8")
        self.assertIn("configvalues.validate_note_category(", src)
        # ordering invariant: the check sees the entry AFTER the prior-copy merge
        # and the --clear-note pop, not the raw flag
        self.assertLess(src.index("entry.update("), src.index("validate_note_category("))
        self.assertLess(src.index('entry.pop("note", None)'), src.index("validate_note_category("))
        self.assertIn('entry.get("note")', src)

    def test_clear_note_flag_surface(self):
        actions = _add_parser_actions()
        self.assertIn("clear_note", actions)
        self.assertIs(actions["clear_note"].default, False)

    def test_note_help_documents_the_rule(self):
        help_text = _add_parser_actions()["note"].help
        self.assertIn("non-empty", help_text)  # m28's lock still holds
        self.assertIn("milestone", help_text)
        self.assertIn("--clear-note", help_text)

    def test_clear_note_parses(self):
        ns = build_parser().parse_args(
            ["events", "add", "--title", "T", "--short", "S", "--date", "05-01", "--clear-note"]
        )
        self.assertTrue(ns.clear_note)
        self.assertIsNone(ns.note)


if __name__ == "__main__":
    unittest.main()
