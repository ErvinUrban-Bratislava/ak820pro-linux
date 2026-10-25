"""v0.32.0 — m28: whitespace-only `--note` and the note trust boundary.

Live repro (before the fix, proven against the shim):

    events add --short m28 --title "M28 Probe" --date 01-01 --note "   "
    -> exit 0, stored note `'   '`, `events list` rendered an EMPTY
       MILESTONE column

`validate_note` passed it (no placeholder to reject) and the entry persisted
the blank verbatim. The blank is not inert: `compute_upcoming_events` does
`note_tpl.format(...) if "{years}" in note_tpl else (note_tpl or "Milestone")`,
and `"   "` is truthy — so the documented empty-note fallback never fired and
the screen column went blank. The same held for hand-edited configs
(`"note": "   "` listed blank too).

DECIDED (v0.32.0):
* CLI — reject blank via the existing `validate_nonblank` (one rule, one home,
  the m27 precedent). Treating blank as absent would *guess* at the user's
  intent (the stance every m18/m21/m23/m26/m27 fix takes against guessing) and
  would not even deliver a way to clear a note, since absent preserves the
  prior one.
* Loader — `validate_note` now returns the STRIPPED text and the coercion
  persists it, so hand-edited blank/padded notes normalize at the choke point
  instead of per consumer. The CLI-only *rejection* boundary is kept: `"note":
  ""` is a legal, working spelling (explicit "Milestone" fallback), so
  rejecting blanks at load would brick otherwise-fine hand-edited configs —
  every command would exit 6, including the ones needed to inspect the file.

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

BLANKS = ["", " ", "   ", "\t", "\n", " \t\n "]


def _run_main(argv):
    """cli.main(argv) capturing stdout+stderr; returns (rc, out, err)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


def _write_cfg(tmp, name, cfg):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    return path


def _add_argv(path, note=None):
    argv = ["events", "add", "--config", path, "--title", "Probe", "--short", "PROBE",
            "--date", "05-01"]
    if note is not None:
        argv += ["--note", note]
    return argv


def _milestone(path):
    """milestone_str of the single entry, through the real load choke point."""
    cfg = events.load_events_config(path)
    rows = events.compute_upcoming_events(cfg)
    return rows[0]["milestone_str"]


class TestBlankNoteRejectedAtCli(unittest.TestCase):
    """m28: empty/whitespace-only --note dies as exit 6 (D4/m27 family)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_note_blank_forms_exit_6_naming_flag(self):
        for i, blank in enumerate(BLANKS):
            path = _write_cfg(self.tmp, f"n{i}.json", {"events": []})
            with self.subTest(value=blank), self.assertRaises(SystemExit) as ctx:
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    cli.main(_add_argv(path, blank))
            self.assertEqual(ctx.exception.code, 6)
            self.assertIn("--note", err.getvalue())
            self.assertIn("must not be empty or whitespace-only", err.getvalue())

    def test_live_repro_form_now_exits_6(self):
        # the exact probe argv that stored `'   '` with exit 0
        path = _write_cfg(self.tmp, "repro.json", {"events": []})
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()):
                cli.main(_add_argv(path, "   "))
        self.assertEqual(ctx.exception.code, 6)
        self.assertEqual(json.load(open(path))["events"], [])

    def test_rejected_add_leaves_config_untouched(self):
        path = _write_cfg(self.tmp, "untouched.json", {"events": []})
        before = open(path, "rb").read()
        mtime = os.stat(path).st_mtime_ns
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                cli.main(_add_argv(path, " \t\n "))
        self.assertEqual(open(path, "rb").read(), before)
        self.assertEqual(os.stat(path).st_mtime_ns, mtime)

    def test_friendly_message_never_a_traceback(self):
        path = _write_cfg(self.tmp, "friendly.json", {"events": []})
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                cli.main(_add_argv(path, "  "))
        self.assertNotIn("Traceback", err.getvalue())
        self.assertIn("Error:", err.getvalue())

    def test_blank_note_readd_preserves_prior_note(self):
        path = _write_cfg(self.tmp, "prior.json", {"events": [
            {"id": "probe", "title": "Probe", "short_title": "PROBE",
             "date": "05-01", "category": "milestone", "note": "old {years}"}]})
        before = open(path, "rb").read()
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()):
                cli.main(_add_argv(path, "   "))
        self.assertEqual(ctx.exception.code, 6)
        self.assertEqual(open(path, "rb").read(), before)
        self.assertEqual(json.load(open(path))["events"][0]["note"], "old {years}")

    def test_placeholder_rule_still_applies_after_strip(self):
        # both note rules coexist: strip first, then the {years}-only check
        path = _write_cfg(self.tmp, "ph.json", {"events": []})
        for bad in ("{place}", "  {place}  ", "oops {unclosed"):
            with self.subTest(note=bad), self.assertRaises(SystemExit) as ctx:
                with contextlib.redirect_stderr(io.StringIO()):
                    cli.main(_add_argv(path, bad))
            self.assertEqual(ctx.exception.code, 6)

    def test_valid_note_still_accepted(self):
        path = _write_cfg(self.tmp, "ok.json", {"events": []})
        rc, out, _ = _run_main(_add_argv(path, "Shipped {years} yrs after V1"))
        self.assertEqual(rc, 0)
        self.assertIn("Saved", out)
        self.assertEqual(json.load(open(path))["events"][0]["note"],
                         "Shipped {years} yrs after V1")


class TestNoteNormalization(unittest.TestCase):
    """Surrounding whitespace is incidental — stripped on the way in (m27's class)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_padded_note_stored_stripped(self):
        path = _write_cfg(self.tmp, "pad.json", {"events": []})
        rc, _, _ = _run_main(_add_argv(path, "  {years} yrs  "))
        self.assertEqual(rc, 0)
        self.assertEqual(json.load(open(path))["events"][0]["note"], "{years} yrs")

    def test_internal_whitespace_preserved(self):
        path = _write_cfg(self.tmp, "inner.json", {"events": []})
        rc, _, _ = _run_main(_add_argv(path, "  a  b  "))
        self.assertEqual(rc, 0)
        self.assertEqual(json.load(open(path))["events"][0]["note"], "a  b")

    def test_noop_readd_with_padded_note_stable(self):
        # m20 extends over the strip: padded re-add of an identical entry is a
        # byte-for-byte no-op (normalization makes it identical, not "changed")
        path = _write_cfg(self.tmp, "noop.json", {"events": []})
        self.assertEqual(_run_main(_add_argv(path, "  {years} yrs  "))[0], 0)
        mtime = os.stat(path).st_mtime_ns
        rc, out, _ = _run_main(_add_argv(path, "  {years} yrs  "))
        self.assertEqual(rc, 0)
        self.assertIn("already up to date", out)
        self.assertEqual(os.stat(path).st_mtime_ns, mtime)

    def test_render_uses_stripped_form(self):
        # date-independent: compare against the row's own years, never a
        # hardcoded count (the next occurrence moves with the run date)
        path = _write_cfg(self.tmp, "render.json", {"events": []})
        self.assertEqual(_run_main(_add_argv(path, "  {years} yrs  "))[0], 0)
        rc, out, _ = _run_main(["events", "list", "--config", path])
        self.assertEqual(rc, 0)
        row = events.compute_upcoming_events(events.load_events_config(path))[0]
        self.assertEqual(row["milestone_str"], f"{row['years']} yrs")
        self.assertNotIn("  yrs", out)  # no padded gap left in the column


class TestLoaderNoteNormalization(unittest.TestCase):
    """Hand-edited configs: normalize at the choke point, never brick the file."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_hand_edited_blank_note_renders_fallback(self):
        # the live-proven blank column -> now the documented "Milestone"
        path = _write_cfg(self.tmp, "blank.json", {"events": [
            {"id": "x", "title": "Xmas", "short_title": "XMAS", "date": "12-25",
             "category": "milestone", "note": "   "}]})
        rc, out, _ = _run_main(["events", "list", "--config", path])
        self.assertEqual(rc, 0)
        self.assertIn("Milestone", out)
        self.assertEqual(_milestone(path), "Milestone")

    def test_hand_edited_padded_note_stripped_in_coercion(self):
        path = _write_cfg(self.tmp, "padload.json", {"events": [
            {"id": "x", "title": "Xmas", "short_title": "XMAS", "date": "12-25",
             "category": "milestone", "note": "  {years} yrs  "}]})
        # date-independent: years moves with the run date, the stripping does not
        row = events.compute_upcoming_events(events.load_events_config(path))[0]
        self.assertEqual(row["milestone_str"], f"{row['years']} yrs")
        self.assertEqual(events.load_events_config(path)["events"][0]["note"],
                         "{years} yrs")

    def test_empty_note_still_a_legal_fallback(self):
        # blast-radius lock: "note": "" is a WORKING hand-edited spelling;
        # rejecting it at load would turn every command into exit 6
        path = _write_cfg(self.tmp, "empty.json", {"events": [
            {"id": "x", "title": "Xmas", "short_title": "XMAS", "date": "12-25",
             "category": "milestone", "note": ""}]})
        rc, out, _ = _run_main(["events", "list", "--config", path])
        self.assertEqual(rc, 0)
        self.assertIn("Milestone", out)

    def test_bad_placeholder_still_rejected_at_load(self):
        # m18 regression across the validate_note signature change
        path = _write_cfg(self.tmp, "bad.json", {"events": [
            {"id": "x", "title": "X", "date": "12-25", "note": "{years} in {place}"}]})
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()) as err:
                cli.main(["events", "list", "--config", path])
        self.assertEqual(ctx.exception.code, 6)
        self.assertIn("events[0].note", err.getvalue())

    def test_note_absent_untouched_by_coercion(self):
        path = _write_cfg(self.tmp, "absent.json", {"events": [
            {"id": "x", "title": "Xmas", "short_title": "XMAS", "date": "12-25"}]})
        cfg = events.load_events_config(path)
        self.assertNotIn("note", cfg["events"][0])


class TestTrustBoundaryRevisited(unittest.TestCase):
    """DECIDED (v0.32.0): strict on the creation path, permissive on load."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_cli_rejects_and_loader_accepts_blank_note(self):
        # the asymmetry is the decision, stated as a table so it cannot drift
        cli_path = _write_cfg(self.tmp, "asym_cli.json", {"events": []})
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()):
                cli.main(_add_argv(cli_path, "   "))
        self.assertEqual(ctx.exception.code, 6)
        loader_path = _write_cfg(self.tmp, "asym_loader.json", {"events": [
            {"id": "x", "title": "X", "date": "12-25", "note": "   "}]})
        self.assertEqual(_run_main(["events", "list", "--config", loader_path])[0], 0)

    def test_loader_still_permissive_for_blank_identity_fields(self):
        # extends the v0.31.0 lock: id/title/short_title blanks load fine
        path = _write_cfg(self.tmp, "ident.json", {"events": [
            {"id": " ", "title": " ", "short_title": " ", "date": "01-01"}]})
        self.assertEqual(_run_main(["events", "list", "--config", path])[0], 0)

    def test_identity_fields_not_stripped_at_load(self):
        # deliberate: stripping id/title at load would silently change the
        # matching semantics of data the user wrote (remove-by-title would
        # newly match padded titles). The creation path normalizes instead.
        path = _write_cfg(self.tmp, "noident.json", {"events": [
            {"id": " x ", "title": " Padded ", "date": "01-01"}]})
        cfg = events.load_events_config(path)
        self.assertEqual((cfg["events"][0]["id"], cfg["events"][0]["title"]),
                         (" x ", " Padded "))

    def test_trust_boundary_documented(self):
        doc = configvalues.validate_nonblank.__doc__ or ""
        self.assertIn("Trust boundary", doc)
        self.assertIn("permissive", doc)


class TestSingleHomeInvariants(unittest.TestCase):
    """Anti-recurrence: the note rules live in configvalues, nowhere else."""

    def test_validate_note_returns_stripped_text(self):
        self.assertEqual(configvalues.validate_note("  a {years} b  ", "--note"),
                         "a {years} b")
        self.assertEqual(configvalues.validate_note("   ", "--note"), "")
        self.assertEqual(configvalues.validate_note("", "--note"), "")

    def test_validate_note_still_raises_config_error(self):
        for bad in ("{place}", "{}", "{0}", "unbalanced {"):
            with self.subTest(value=bad), self.assertRaises(ConfigError):
                configvalues.validate_note(bad, "--note")

    def test_cli_has_no_inline_note_strip(self):
        # normalization must stay single-home: config.py delegates, never strips
        src = (ROOT / "ak820" / "config.py").read_text(encoding="utf-8")
        self.assertNotIn("note.strip()", src)
        self.assertIn("validate_nonblank(note", src)
        self.assertIn("validate_note(note", src)

    def test_coercion_persists_the_normalized_note(self):
        # the choke point assigns the stripped form (not merely validates it)
        # v0.34.0: the choke-point coercion lives in ak820.coercion (split out of
        # configvalues along the loader/CLI seam); the lock is the same lock.
        src = (ROOT / "ak820" / "coercion.py").read_text(encoding="utf-8")
        self.assertIn('ev["note"] = validate_note(', src)

    def test_nonblank_rule_shared_by_all_four_entry_points(self):
        # one rule, four entry points — each binds the returned (stripped) text
        src = (ROOT / "ak820" / "config.py").read_text(encoding="utf-8")
        for dest in ("short", "title", "note", "target"):
            self.assertIn(f"{dest} = configvalues.validate_nonblank(", src, dest)


class TestParserSurface(unittest.TestCase):
    def test_note_help_documents_non_empty(self):
        p = build_parser()
        ev_add = p._subparsers._group_actions[0].choices["events"]  # type: ignore[attr-defined]
        add = ev_add._subparsers._group_actions[0].choices["add"]  # type: ignore[attr-defined]
        helps = {a.dest: a.help for a in add._actions}
        self.assertIn("non-empty", helps["note"])

    def test_note_still_parses(self):
        ns = build_parser().parse_args(
            ["events", "add", "--title", "T", "--short", "S", "--date", "01-01",
             "--note", "V{years}"]
        )
        self.assertEqual(ns.note, "V{years}")

    def test_note_defaults_to_none(self):
        ns = build_parser().parse_args(
            ["events", "add", "--title", "T", "--short", "S", "--date", "01-01"]
        )
        self.assertIsNone(ns.note)


if __name__ == "__main__":
    unittest.main()
