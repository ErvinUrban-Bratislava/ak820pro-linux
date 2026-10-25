#!/usr/bin/env python3
"""v0.31.0 regression suite: m27 blank `--short`/`--title` (zero hardware).

m27 (found during the v0.30.0 probes): `events add --short` accepted an
empty or whitespace-only value — `--short " "` yielded the degenerate id
`"-"` and `--short ""` the empty id, each with a blank `short_title` screen
label (live-proven pre-fix: all three forms exited 0 and persisted). The
v0.30.0 `_same_event` empty-needle guard kept these from colliding, but the
entry was unusable: radar rendered an empty label and removal needed the
title.

Fix: `configvalues.validate_nonblank` is the single home of the
non-empty-after-strip rule (ConfigError 6, the D4 precedent — `--short` is
the id's only source), shared by `events add --short`/`--title` and the
`events remove` target. DECIDED: `--title` gets the same guard (a blank
title renders blank in `events list`/radar and degrades the remove-by-title
identity surface). Returning the stripped text keeps normalization
single-home: `--short " DAUGHTER "` yields the canonical id `daughter`,
never the dash-padded `-daughter-` (m24's class — identity must not be
sensitive to incidental formatting). The loader stays permissive for
hand-edited data (trust boundary, mirrors v0.27.0's render-path decision).
"""

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

from ak820 import cli, config, configvalues
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


def _add_argv(path, title, short, date="05-01"):
    return ["events", "add", "--config", path, "--title", title, "--short", short, "--date", date]


class TestBlankShortTitleRejected(unittest.TestCase):
    """m27: empty/whitespace-only --short and --title die as exit 6 (D4 family)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_short_blank_forms_exit_6_naming_flag(self):
        for i, blank in enumerate(BLANKS):
            path = _write_cfg(self.tmp, f"s{i}.json", {"events": []})
            with self.subTest(value=blank), self.assertRaises(SystemExit) as ctx:
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    cli.main(_add_argv(path, "Probe", blank))
            self.assertEqual(ctx.exception.code, 6)
            self.assertIn("--short", err.getvalue())
            self.assertIn("must not be empty or whitespace-only", err.getvalue())

    def test_title_blank_forms_exit_6_naming_flag(self):
        for i, blank in enumerate(BLANKS):
            path = _write_cfg(self.tmp, f"t{i}.json", {"events": []})
            with self.subTest(value=blank), self.assertRaises(SystemExit) as ctx:
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    cli.main(_add_argv(path, blank, "PROBE"))
            self.assertEqual(ctx.exception.code, 6)
            self.assertIn("--title", err.getvalue())
            self.assertIn("must not be empty or whitespace-only", err.getvalue())

    def test_rejected_add_leaves_config_untouched(self):
        # nothing saved: bytes AND mtime stable (the m20/D13 stability class)
        path = _write_cfg(self.tmp, "untouched.json", {"events": []})
        before = open(path, "rb").read()
        mtime = os.stat(path).st_mtime_ns
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                cli.main(_add_argv(path, "Probe", " "))
        self.assertEqual(open(path, "rb").read(), before)
        self.assertEqual(os.stat(path).st_mtime_ns, mtime)

    def test_live_repro_forms_now_exit_6(self):
        # the exact v0.30.0 probe argv that persisted id "-" / "" with exit 0
        path = _write_cfg(self.tmp, "repro.json", {"events": []})
        for short in (" ", ""):
            with self.subTest(short=short), self.assertRaises(SystemExit) as ctx:
                with contextlib.redirect_stderr(io.StringIO()):
                    cli.main(_add_argv(path, "Probe One", short))
            self.assertEqual(ctx.exception.code, 6)
        self.assertEqual(json.load(open(path))["events"], [])

    def test_friendly_message_never_a_traceback(self):
        path = _write_cfg(self.tmp, "friendly.json", {"events": []})
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                cli.main(_add_argv(path, "Probe", "\t"))
        self.assertNotIn("Traceback", err.getvalue())


class TestStrippedValuesStored(unittest.TestCase):
    """validate_nonblank returns the stripped text — canonical id/label home."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_padded_short_stores_canonical_id_and_label(self):
        path = _write_cfg(self.tmp, "pad.json", {"events": []})
        rc, _, _ = _run_main(_add_argv(path, "Pad Probe", "  PAD  "))
        self.assertEqual(rc, 0)
        saved = json.load(open(path))["events"][0]
        self.assertEqual(saved["id"], "pad")  # was "-pad-" pre-strip
        self.assertEqual(saved["short_title"], "PAD")

    def test_padded_title_stored_stripped(self):
        path = _write_cfg(self.tmp, "padt.json", {"events": []})
        rc, _, _ = _run_main(_add_argv(path, "  Daughter's Birthday  ", "D"))
        self.assertEqual(rc, 0)
        self.assertEqual(json.load(open(path))["events"][0]["title"], "Daughter's Birthday")

    def test_internal_spaces_still_dash_id_and_spaced_label(self):
        path = _write_cfg(self.tmp, "inner.json", {"events": []})
        rc, _, _ = _run_main(_add_argv(path, "New Year", "New Year"))
        self.assertEqual(rc, 0)
        saved = json.load(open(path))["events"][0]
        self.assertEqual(saved["id"], "new-year")
        self.assertEqual(saved["short_title"], "NEW YEAR")

    def test_noop_readd_with_padded_forms_stable(self):
        # m20 extends over m27's strip: padded re-add of an identical entry
        # is still a no-op (bytes + mtime untouched).
        path = _write_cfg(
            self.tmp,
            "noop.json",
            {
                "events": [
                    {
                        "id": "launch",
                        "title": "Launch",
                        "short_title": "LAUNCH",
                        "date": "01-01",
                        "category": "milestone",
                    }
                ]
            },
        )
        mtime = os.stat(path).st_mtime_ns
        rc, out, _ = _run_main(_add_argv(path, " Launch ", " launch ", date="01-01"))
        self.assertEqual(rc, 0)
        self.assertIn("already up to date", out)
        self.assertEqual(os.stat(path).st_mtime_ns, mtime)

    def test_padded_readd_updates_not_duplicates(self):
        path = _write_cfg(
            self.tmp,
            "dup.json",
            {
                "events": [
                    {"id": "launch", "title": "Launch", "short_title": "LAUNCH", "date": "01-01"}
                ]
            },
        )
        rc, _, _ = _run_main(_add_argv(path, "Launch", " launch ", date="02-02"))
        self.assertEqual(rc, 0)
        saved = json.load(open(path))["events"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["date"], "02-02")


class TestRemoveTargetNonBlank(unittest.TestCase):
    """The remove target shares the one non-blank rule and the strip."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = _write_cfg(
            self.tmp,
            "rm.json",
            {"events": [{"id": "daughter", "title": "Daughter's Birthday", "date": "05-01"}]},
        )

    def test_padded_target_removes(self):
        # symmetric with add's stripped id/title (m24's class)
        rc, out, _ = _run_main(["events", "remove", " daughter ", "--config", self.path])
        self.assertEqual(rc, 0)
        self.assertIn("Removed 1", out)

    def test_blank_target_exit_6_names_target(self):
        for blank in ("", " ", "\t"):
            with self.subTest(target=blank), self.assertRaises(SystemExit) as ctx:
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    cli.main(["events", "remove", blank, "--config", self.path])
            self.assertEqual(ctx.exception.code, 6)
            self.assertIn("remove target", err.getvalue())

    def test_d13_no_match_still_exits_6_file_untouched(self):
        before = open(self.path, "rb").read()
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()):
                cli.main(["events", "remove", "nobody", "--config", self.path])
        self.assertEqual(ctx.exception.code, 6)
        self.assertEqual(open(self.path, "rb").read(), before)


class TestSameEventEmptyNeedleLock(unittest.TestCase):
    """Table-driven lock: an empty needle NEVER matches (m24's guard, m27's
    reason it mattered — the degenerate entries must not collide, and the
    guard must not erode if identity rules grow again)."""

    ENTRIES = [{"id": ""}, {"title": ""}, {}, {"id": "", "title": ""}, {"title": "X"}]

    def test_empty_and_none_needles_never_match(self):
        for entry in self.ENTRIES:
            for needle in ("", None):
                with self.subTest(entry=entry, needle=needle):
                    self.assertFalse(config._same_event(entry, needle, needle))

    def test_nonempty_needles_still_match_case_insensitively(self):
        # the guard must not have swallowed real matches (m24 regression)
        self.assertTrue(config._same_event({"id": "daughter"}, "DAUGHTER", None))
        self.assertTrue(config._same_event({"title": "Bee"}, "x", "BEE"))
        self.assertFalse(config._same_event({"id": "a"}, "b", "c"))


class TestValidateNonblankUnit(unittest.TestCase):
    def test_returns_stripped_text(self):
        self.assertEqual(configvalues.validate_nonblank(" a ", "--short ' a '"), "a")
        self.assertEqual(configvalues.validate_nonblank("tab\tstop", "x"), "tab\tstop")

    def test_raises_config_error_naming_where(self):
        for blank in BLANKS:
            with self.subTest(value=blank), self.assertRaises(ConfigError) as ctx:
                configvalues.validate_nonblank(blank, "--short ' '")
            self.assertIn("--short", str(ctx.exception))
            self.assertIn("must not be empty or whitespace-only", str(ctx.exception))


class TestLoaderTrustBoundary(unittest.TestCase):
    """DECIDED (v0.31.0): the CLI enforces non-blank; the loader stays
    permissive for hand-edited data — rejecting existing configs would turn
    every command into exit 6, and the CLI can no longer create such entries."""

    def test_hand_edited_blank_title_still_loads(self):
        tmp = tempfile.mkdtemp()
        path = _write_cfg(
            tmp,
            "hand.json",
            {"events": [{"id": "x", "title": " ", "short_title": " ", "date": "01-01"}]},
        )
        rc, _, _ = _run_main(["events", "list", "--config", path])
        self.assertEqual(rc, 0)


class TestParserSurface(unittest.TestCase):
    def test_valid_flags_still_parse(self):
        ns = build_parser().parse_args(
            ["events", "add", "--title", "T", "--short", "S", "--date", "01-01"]
        )
        self.assertEqual((ns.title, ns.short), ("T", "S"))

    def test_help_documents_non_empty(self):
        p = build_parser()
        ev_add = p._subparsers._group_actions[0].choices["events"]  # type: ignore[attr-defined]
        add = ev_add._subparsers._group_actions[0].choices["add"]  # type: ignore[attr-defined]
        helps = {a.dest: a.help for a in add._actions}
        self.assertIn("non-empty", helps["short"])
        self.assertIn("non-empty", helps["title"])


if __name__ == "__main__":
    unittest.main()
