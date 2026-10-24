#!/usr/bin/env python3
"""v0.26.0 regression suite: CLI surface parity + carried §21 minors (zero hardware).

--config parity: `events list|add|remove --config F` (flag AFTER the
     sub-action) was rejected as unrecognized (exit 2) while `radar --config F`
     worked — every events-config consumer must accept the same override.
     Both positions now parse; the sub-action position wins when both are given.
m18: non-string field values inside otherwise-valid configs crashed downstream
     consumers with raw tracebacks — int `title` -> `.lower()`/`.upper()`
     AttributeError and a sort TypeError, `note` "{years} in {place}" ->
     KeyError in format, `"idle_mode": "1"` -> TypeError in byte assembly.
     Scalars coerce at the load choke point; null/list/object and bad note
     placeholders -> ConfigError (6), the D22 class.
m20: an identical `events add` rewrote the file byte-for-byte — the write is
     now skipped (no mtime churn, no misleading "Saved" line).
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ak820 import backlight, cli, config, events


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


class TestEventsConfigFlagParity(unittest.TestCase):
    """`events <sub-action> --config F` parses and routes like `radar --config F`."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = _write_cfg(
            self.tmp,
            "ev.json",
            {
                "events": [
                    {
                        "id": "xmas",
                        "title": "Christmas",
                        "short_title": "XMAS",
                        "date": "12-25",
                        "category": "holiday",
                    }
                ]
            },
        )

    def test_list_accepts_config_after_subaction(self):
        rc, out, _ = _run_main(["events", "list", "--config", self.cfg])
        self.assertEqual(rc, 0)
        self.assertIn("Christmas", out)

    def test_list_accepts_config_before_subaction(self):
        # Regression lock for the pre-v0.26 position (tests/test_cli.py form).
        rc, out, _ = _run_main(["events", "--config", self.cfg, "list"])
        self.assertEqual(rc, 0)
        self.assertIn("Christmas", out)

    def test_bare_events_accepts_config(self):
        rc, out, _ = _run_main(["events", "--config", self.cfg])
        self.assertEqual(rc, 0)
        self.assertIn("Christmas", out)

    def test_add_accepts_config_after_subaction(self):
        rc, out, _ = _run_main(
            ["events", "add", "--config", self.cfg, "--title", "New", "--short", "NEW", "--date", "01-01"]
        )
        self.assertEqual(rc, 0)
        self.assertIn("Saved", out)
        titles = [e["title"] for e in json.load(open(self.cfg))["events"]]
        self.assertIn("New", titles)

    def test_remove_accepts_config_after_subaction(self):
        rc, out, _ = _run_main(["events", "remove", "--config", self.cfg, "xmas"])
        self.assertEqual(rc, 0)
        self.assertIn("Removed 1", out)
        self.assertEqual(json.load(open(self.cfg))["events"], [])

    def test_remove_no_match_with_trailing_config_exits_6(self):
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()):
                cli.main(["events", "remove", "--config", self.cfg, "ghost"])
        self.assertEqual(ctx.exception.code, 6)

    def test_json_flag_combines_with_trailing_config(self):
        rc, out, _ = _run_main(["events", "list", "--json", "--config", self.cfg])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)[0]["title"], "Christmas")

    def test_subaction_position_wins_over_parent(self):
        a = _write_cfg(self.tmp, "a.json", {"events": [{"title": "Alpha", "date": "12-25"}]})
        b = _write_cfg(self.tmp, "b.json", {"events": [{"title": "Beta", "date": "12-25"}]})
        rc, out, _ = _run_main(["events", "--config", a, "list", "--config", b])
        self.assertEqual(rc, 0)
        self.assertIn("Beta", out)
        self.assertNotIn("Alpha", out)

    def test_no_config_leaves_attr_none(self):
        # SUPPRESS on the sub-action must not delete the parent's default —
        # cmd_events reads args.config unconditionally via getattr.
        for argv in (["events", "list"], ["events", "add", "--title", "T", "--short", "S", "--date", "12-25"]):
            args = cli.build_parser().parse_args(argv)
            self.assertIsNone(args.config)

    def test_radar_trailing_config_unchanged(self):
        out_dir = os.path.join(self.tmp, "prev")
        rc, _, _ = _run_main(
            ["radar", "--config", self.cfg, "--save-preview", out_dir, "--date", "2026-10-23"]
        )
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(os.path.join(out_dir, "radar_hero.png")))


class TestM18ValueCoercion(unittest.TestCase):
    """Wrong-typed field values coerce (scalars) or exit 6 — never a raw traceback."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _list(self, cfg):
        return _run_main(["events", "list", "--config", cfg])

    def test_int_title_coerced_for_all_consumers(self):
        cfg = _write_cfg(self.tmp, "t.json", {"events": [{"title": 123, "date": "12-25"}]})
        rc, out, _ = self._list(cfg)
        self.assertEqual(rc, 0)
        self.assertIn("123", out)
        rc, out, _ = _run_main(["events", "list", "--json", "--config", cfg])
        self.assertEqual(json.loads(out)[0]["title"], "123")

    def test_int_title_sorts_with_string_titles(self):
        # The exact §21 crash: sort key (days_left, title) compared int vs str.
        cfg = _write_cfg(
            self.tmp,
            "s.json",
            {"events": [{"title": "A", "date": "12-25"}, {"title": 5, "date": "11-01", "id": "z"}]},
        )
        rc, _, _ = self._list(cfg)
        self.assertEqual(rc, 0)

    def test_int_category_and_id_coerced(self):
        cfg = _write_cfg(
            self.tmp, "c.json", {"events": [{"id": 7, "title": "X", "date": "12-25", "category": 1}]}
        )
        rc, out, _ = _run_main(["events", "list", "--json", "--config", cfg])
        self.assertEqual(rc, 0)
        row = json.loads(out)[0]
        self.assertEqual((row["category"], row["id"]), ("1", "7"))

    def test_null_title_rejected_6(self):
        cfg = _write_cfg(self.tmp, "n.json", {"events": [{"title": None, "date": "12-25"}]})
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()) as err:
                cli.main(["events", "list", "--config", cfg])
        self.assertEqual(ctx.exception.code, 6)
        self.assertIn("events[0].title", err.getvalue())
        self.assertIn("null", err.getvalue())

    def test_list_short_title_rejected_6(self):
        cfg = _write_cfg(
            self.tmp, "l.json", {"events": [{"title": "X", "date": "12-25", "short_title": ["a"]}]}
        )
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()):
                cli.main(["events", "list", "--config", cfg])
        self.assertEqual(ctx.exception.code, 6)

    def test_int_date_still_friendly_6(self):
        # Coercion turns 1225 into "1225"; D4 still rejects it — exit 6 either way.
        cfg = _write_cfg(self.tmp, "d.json", {"events": [{"title": "X", "date": 1225}]})
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()) as err:
                cli.main(["events", "list", "--config", cfg])
        self.assertEqual(ctx.exception.code, 6)
        self.assertIn("Invalid date", err.getvalue())

    def test_note_stray_placeholder_rejected_6(self):
        cfg = _write_cfg(
            self.tmp,
            "p.json",
            {"events": [{"title": "X", "date": "12-25", "note": "{years} in {place}"}]},
        )
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()) as err:
                cli.main(["events", "list", "--config", cfg])
        self.assertEqual(ctx.exception.code, 6)
        self.assertIn("years", err.getvalue())

    def test_note_empty_and_positional_placeholders_rejected_6(self):
        for i, note in enumerate(("{}", "{0}")):
            cfg = _write_cfg(
                self.tmp, f"e{i}.json", {"events": [{"title": "X", "date": "12-25", "note": note}]}
            )
            with self.assertRaises(SystemExit) as ctx:
                with contextlib.redirect_stderr(io.StringIO()):
                    cli.main(["events", "list", "--config", cfg])
            self.assertEqual(ctx.exception.code, 6, note)

    def test_note_unbalanced_brace_rejected_6(self):
        cfg = _write_cfg(
            self.tmp, "u.json", {"events": [{"title": "X", "date": "12-25", "note": "100% {oops"}]}
        )
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()):
                cli.main(["events", "list", "--config", cfg])
        self.assertEqual(ctx.exception.code, 6)

    def test_note_years_variants_and_escaped_braces_ok(self):
        for i, note in enumerate(("done in {years!r} yrs", "{years:02d} to go", "{{literal}} {years}")):
            cfg = _write_cfg(
                self.tmp, f"ok{i}.json", {"events": [{"title": "X", "date": "12-25", "note": note}]}
            )
            rc, _, _ = self._list(cfg)
            self.assertEqual(rc, 0, note)

    def test_idle_mode_numeric_string_and_integral_float_coerced(self):
        cfg = _write_cfg(
            self.tmp,
            "bl.json",
            {"backlight": {"idle_preset": "none", "idle_mode": "1", "idle_speed": 3.0}},
        )
        with mock.patch.dict(os.environ, {"AK820_EVENTS_PATH": cfg}):
            idle = backlight.get_idle_backlight_cfg()
        self.assertEqual((idle["mode"], idle["speed"]), (1, 3))

    def test_idle_mode_garbage_rejected_6(self):
        cfg = _write_cfg(self.tmp, "bg.json", {"backlight": {"idle_mode": "fast"}})
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()) as err:
                cli.main(["events", "list", "--config", cfg])
        self.assertEqual(ctx.exception.code, 6)
        self.assertIn("backlight.idle_mode", err.getvalue())

    def test_idle_direction_null_rejected_6(self):
        cfg = _write_cfg(self.tmp, "bn.json", {"backlight": {"idle_direction": None}})
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()):
                cli.main(["events", "list", "--config", cfg])
        self.assertEqual(ctx.exception.code, 6)

    def test_coercion_persisted_by_add(self):
        # add saves the loaded (coerced) cfg — the hand-edited int never survives.
        cfg = _write_cfg(
            self.tmp, "p2.json", {"events": [{"title": 2026, "date": "12-25", "id": "launch"}]}
        )
        rc, _, _ = _run_main(
            ["events", "add", "--config", cfg, "--title", "New", "--short", "NEW", "--date", "01-01"]
        )
        self.assertEqual(rc, 0)
        titles = [e["title"] for e in json.load(open(cfg))["events"]]
        self.assertIn("2026", titles)
        self.assertNotIn(2026, titles)

    def test_radar_preview_survives_coerced_config(self):
        cfg = _write_cfg(self.tmp, "rp.json", {"events": [{"title": 123, "date": "12-25"}]})
        out_dir = os.path.join(self.tmp, "prev")
        rc, _, _ = _run_main(
            ["radar", "--config", cfg, "--save-preview", out_dir, "--date", "2026-10-23"]
        )
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(os.path.join(out_dir, "radar_hero.png")))


class TestM20NoOpAdd(unittest.TestCase):
    """An identical `events add` changes nothing on disk and says so."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = _write_cfg(
            self.tmp,
            "ev.json",
            {
                "events": [
                    {
                        "id": "xmas",
                        "title": "Christmas",
                        "short_title": "XMAS",
                        "date": "12-25",
                        "category": "milestone",
                    }
                ]
            },
        )

    def _add(self, *extra):
        argv = [
            "events",
            "add",
            "--config",
            self.cfg,
            "--title",
            "Christmas",
            "--short",
            "XMAS",
            "--date",
            "12-25",
        ]
        argv.extend(extra)
        return _run_main(argv)

    def test_identical_add_skips_save(self):
        with mock.patch.object(
            events, "save_events_config", wraps=events.save_events_config
        ) as spy:
            rc, out, _ = self._add()
        self.assertEqual(rc, 0)
        self.assertEqual(spy.call_count, 0)
        self.assertIn("already up to date", out)
        self.assertIn(self.cfg, out)

    def test_identical_add_leaves_file_untouched(self):
        before = open(self.cfg, "rb").read()
        mtime = os.stat(self.cfg).st_mtime_ns
        self._add()
        self.assertEqual(open(self.cfg, "rb").read(), before)
        self.assertEqual(os.stat(self.cfg).st_mtime_ns, mtime)

    def test_changed_date_still_saves(self):
        with mock.patch.object(
            events, "save_events_config", wraps=events.save_events_config
        ) as spy:
            rc, out, _ = self._add("--date", "12-26")
        self.assertEqual(rc, 0)
        self.assertEqual(spy.call_count, 1)
        self.assertIn("Saved", out)
        dates = [e["date"] for e in json.load(open(self.cfg))["events"]]
        self.assertEqual(dates, ["12-26"])

    def test_same_title_different_short_still_replaces(self):
        # Dedup/replace semantics are unchanged — only true no-ops are skipped.
        rc, out, _ = _run_main(
            [
                "events",
                "add",
                "--config",
                self.cfg,
                "--title",
                "Christmas",
                "--short",
                "X-MAS",
                "--date",
                "12-25",
            ]
        )
        self.assertEqual(rc, 0)
        self.assertIn("Saved", out)
        evs = json.load(open(self.cfg))["events"]
        self.assertEqual([(e["id"], e["short_title"]) for e in evs], [("x-mas", "X-MAS")])

    def test_add_to_missing_config_still_writes(self):
        path = os.path.join(self.tmp, "fresh.json")
        rc, out, _ = _run_main(
            ["events", "add", "--config", path, "--title", "New", "--short", "NEW", "--date", "01-01"]
        )
        self.assertEqual(rc, 0)
        self.assertIn("Saved", out)
        self.assertTrue(os.path.exists(path))

    def test_noop_add_does_not_touch_save_via_config_module(self):
        # config.cmd_events resolves save through the events module attribute —
        # lock that the skip happens BEFORE any save path is reached.
        with mock.patch.object(config.events, "save_events_config") as spy:
            rc, _, _ = self._add()
        self.assertEqual(rc, 0)
        spy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
