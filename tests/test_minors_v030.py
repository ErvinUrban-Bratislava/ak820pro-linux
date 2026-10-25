#!/usr/bin/env python3
"""v0.30.0 regression suite: carried §25 minors m23-m26 (zero hardware).

m23: idle int fields were coerced but UNBOUNDED — a hand-edited
`"idle_mode": 255` reached the wire as MODE_DATA[0] == 255 (outside the
firmware's 0..19 mode range), `256` wrapped silently to 0 via `& 0xFF`, and
brightness/speed/direction were silently clamped or masked (9 -> 5, 7 -> 3).
`coercion._as_int` now range-checks against bounds that live in
`protocol.py` and feed BOTH the loader and the parser's argparse choices —
one home, CLI and loader can never drift. The field list is derived from the
ranges dict, so an unbounded idle int field is no longer representable.

m24: `events remove` matched id case-sensitively but title case-
insensitively (`events remove DAUGHTER` exited 6 next to a working
`events remove daughter`); `add` matched both case-sensitively (a re-add
with a differently-cased title duplicated the entry). One identity rule now
— `config._same_event`, both fields case-insensitive, shared by both actions.

m25: `events add` re-add rebuilt the entry from the schema fields only —
unknown hand-added fields (icon, priority, ...) were silently dropped on
every update (the v0.27.0 note-dropping precedent generalized). The rebuild
now starts from the prior entry and overwrites the schema fields.

m26: `finite_float` accepted negatives — `--dwell -5` cycled presets
instantly, `--watch -5` silently disabled the watch, `radar --seconds -5`
clamped to the 1-frame minimum. `non_negative_float` rejects `< 0` at parse
time (usage exit 2, the D24/m16 class); zero stays valid (default semantics).
"""

import argparse
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

import ak820_ctrl
from ak820 import backlight, cli, coercion, config, events
from ak820.parser import build_parser, finite_float, non_negative_float
from ak820.protocol import MAX_BRIGHTNESS, MAX_DIRECTION, MAX_LIGHT_MODE, MAX_SPEED


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


def _idle_cfg(tmp, name, **fields):
    """Custom-preset config so the cfg's own fields reach the wire (a
    SHOWCASE_PRESETS key would make apply_idle_backlight use the preset's)."""
    return _write_cfg(
        tmp, name, {"backlight": {"idle_preset": "custom", "idle_color": "#FF0000", **fields}}
    )


def _mode_byte(cfg_path):
    """MODE_DATA (3rd feature report) byte 0 — the firmware mode byte."""
    dev = mock.MagicMock()
    backlight.apply_idle_backlight(dev, cfg_path)
    return dev.send_feature.call_args_list[2][0][0][0]


def _subparser(parser, name):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction) and name in action.choices:
            return action.choices[name]
    raise AssertionError(f"no subparser {name!r}")


def _choices(parser, dest):
    for action in parser._actions:
        if action.dest == dest:
            return action.choices
    raise AssertionError(f"no action --{dest}")


class TestIdleIntBounds(unittest.TestCase):
    """m23: out-of-range idle int values die at the choke point as exit 6."""

    OUT_OF_RANGE = [
        ("idle_mode", 20),
        ("idle_mode", 255),  # the §25 repro: byte 255 on the wire
        ("idle_mode", 256),  # wrapped silently to 0 via & 0xFF
        ("idle_mode", -1),  # wrapped silently to 255 via & 0xFF
        ("idle_brightness", 6),
        ("idle_speed", 6),
        ("idle_direction", 4),
    ]

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_in_range_values_pass_and_reach_wire_unchanged(self):
        for mode in (0, 19):
            path = _idle_cfg(self.tmp, f"ok{mode}.json", idle_mode=mode)
            rc, _, _ = _run_main(["events", "list", "--config", path])
            self.assertEqual(rc, 0)
            self.assertEqual(_mode_byte(path), mode)

    def test_out_of_range_rejected_6_naming_field_and_range(self):
        for i, (field, bad) in enumerate(self.OUT_OF_RANGE):
            path = _idle_cfg(self.tmp, f"bad{i}.json", **{field: bad})
            with self.subTest(field=field, value=bad), self.assertRaises(SystemExit) as ctx:
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    cli.main(["events", "list", "--config", path])
            self.assertEqual(ctx.exception.code, 6)
            self.assertIn(f"backlight.{field}", err.getvalue())
            self.assertIn("must be in", err.getvalue())
            self.assertIn(f"got {bad}", err.getvalue())

    def test_numeric_string_out_of_range_rejected_in_range_coerced(self):
        path = _idle_cfg(self.tmp, "s255.json", idle_mode="255")
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()):
                cli.main(["events", "list", "--config", path])
        self.assertEqual(ctx.exception.code, 6)
        path = _idle_cfg(self.tmp, "s19.json", idle_mode="19")
        self.assertEqual(backlight.get_idle_backlight_cfg(path)["mode"], 19)

    def test_consumer_seam_raises_before_touching_device(self):
        # the m23 repro form: the wire byte is never assembled for a rejected value
        path = _idle_cfg(self.tmp, "repro.json", idle_mode=255)
        dev = mock.MagicMock()
        with self.assertRaises(events.ConfigError):
            backlight.apply_idle_backlight(dev, path)
        dev.send_feature.assert_not_called()

    def test_bounds_single_home_and_derived_fields(self):
        # loader ranges ARE the protocol constants (no drift, no second copy)
        self.assertEqual(
            coercion._IDLE_INT_RANGES,
            {
                "idle_mode": (0, MAX_LIGHT_MODE),
                "idle_brightness": (0, MAX_BRIGHTNESS),
                "idle_speed": (0, MAX_SPEED),
                "idle_direction": (0, MAX_DIRECTION),
            },
        )
        # m23's root cause is structurally dead: the int field list is DERIVED
        # from the ranges dict — an unbounded idle int field cannot exist.
        # v0.34.0: the ranges/derived-field lists moved with the coercion
        # engine into ak820.coercion; the derivation lock is unchanged.
        self.assertEqual(coercion._IDLE_INT_FIELDS, tuple(coercion._IDLE_INT_RANGES))

    def test_parser_choices_derive_from_protocol_constants(self):
        # CLI <-> loader drift lock: argparse choices are built from the same
        # protocol constants the loader ranges use.
        p = build_parser()
        bl = _subparser(_subparser(p, "backlight"), "set")
        self.assertEqual(list(_choices(bl, "brightness")), list(range(MAX_BRIGHTNESS + 1)))
        self.assertEqual(list(_choices(bl, "speed")), list(range(MAX_SPEED + 1)))
        self.assertEqual(list(_choices(bl, "direction")), list(range(MAX_DIRECTION + 1)))

    def test_cli_and_loader_reject_the_same_mode_20(self):
        with self.assertRaises(ValueError):
            from ak820.protocol import parse_mode

            parse_mode("20")
        path = _idle_cfg(self.tmp, "m20.json", idle_mode=20)
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()):
                cli.main(["events", "list", "--config", path])
        self.assertEqual(ctx.exception.code, 6)


class TestEventIdentityMatching(unittest.TestCase):
    """m24: one case-insensitive id-or-title rule for add AND remove."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = _write_cfg(
            self.tmp,
            "m24.json",
            {"events": [{"id": "daughter", "title": "Daughter's Birthday", "date": "05-01"}]},
        )

    def test_remove_id_uppercase_works(self):
        rc, out, _ = _run_main(["events", "remove", "DAUGHTER", "--config", self.path])
        self.assertEqual(rc, 0)
        self.assertIn("Removed 1", out)
        self.assertEqual(json.load(open(self.path))["events"], [])

    def test_remove_title_mixed_case_works(self):
        rc, _, _ = _run_main(["events", "remove", "dAUGHTER'S BIRTHDAY", "--config", self.path])
        self.assertEqual(rc, 0)
        self.assertEqual(json.load(open(self.path))["events"], [])

    def test_remove_no_match_still_exits_6_file_untouched(self):
        before = open(self.path, "rb").read()
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()):
                cli.main(["events", "remove", "nobody", "--config", self.path])
        self.assertEqual(ctx.exception.code, 6)  # D13 preserved
        self.assertEqual(open(self.path, "rb").read(), before)

    def test_remove_empty_target_exits_6(self):
        # an empty needle must not match entries with an absent id/title
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()):
                cli.main(["events", "remove", "", "--config", self.path])
        self.assertEqual(ctx.exception.code, 6)

    def test_add_differently_cased_title_replaces_not_duplicates(self):
        rc, _, _ = _run_main(
            [
                "events",
                "add",
                "--config",
                self.path,
                "--title",
                "daughter's birthday",
                "--short",
                "D",
                "--date",
                "05-01",
            ]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(len(json.load(open(self.path))["events"]), 1)

    def test_add_id_match_is_case_insensitive(self):
        rc, _, _ = _run_main(
            [
                "events",
                "add",
                "--config",
                self.path,
                "--title",
                "Daughter's Birthday",
                "--short",
                "DAUGHTER",
                "--date",
                "05-01",
            ]
        )
        self.assertEqual(rc, 0)
        events_saved = json.load(open(self.path))["events"]
        self.assertEqual(len(events_saved), 1)
        self.assertEqual(events_saved[0]["id"], "daughter")

    def test_same_event_unit_semantics(self):
        self.assertTrue(config._same_event({"id": "a"}, "A", None))
        self.assertTrue(config._same_event({"title": "Bee"}, "x", "BEE"))
        self.assertFalse(config._same_event({"id": "a"}, "b", "c"))
        # empty needles never match (absent-id entries stay untouchable)
        self.assertFalse(config._same_event({"title": "X"}, "", ""))
        self.assertFalse(config._same_event({}, "", ""))
        self.assertFalse(config._same_event({}, None, None))


class TestReAddPreservesUnknownFields(unittest.TestCase):
    """m25: hand-added fields survive an update; schema fields win."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _cfg_with_extras(self, name="m25.json"):
        return _write_cfg(
            self.tmp,
            name,
            {
                "events": [
                    {
                        "id": "launch",
                        "title": "Launch",
                        "short_title": "LAUNCH",
                        "date": "01-01",
                        "category": "milestone",
                        "icon": "rocket",
                        "priority": 1,
                    }
                ]
            },
        )

    def _add(self, path, **over):
        argv = ["events", "add", "--config", path, "--title", "Launch", "--short", "Launch"]
        argv += ["--date", over.get("date", "01-01")]
        if "note" in over:
            argv += ["--note", over["note"]]
        return _run_main(argv)

    def test_unknown_fields_survive_readd(self):
        path = self._cfg_with_extras()
        rc, _, _ = self._add(path)
        self.assertEqual(rc, 0)
        saved = json.load(open(path))["events"][0]
        self.assertEqual(saved["icon"], "rocket")
        self.assertEqual(saved["priority"], 1)

    def test_schema_fields_are_overwritten(self):
        path = self._cfg_with_extras()
        self._add(path, date="02-02")
        saved = json.load(open(path))["events"][0]
        self.assertEqual(saved["date"], "02-02")
        self.assertEqual(saved["icon"], "rocket")  # extras kept, schema wins

    def test_note_preserved_without_flag_and_overridden_with_it(self):
        path = self._cfg_with_extras("note.json")
        # v0.27.0 regression: omitted --note keeps the prior note
        rc, _, _ = self._add(path, note="Shipped {years} yrs")
        self.assertEqual(rc, 0)
        rc, _, _ = self._add(path)
        self.assertEqual(rc, 0)
        self.assertEqual(json.load(open(path))["events"][0]["note"], "Shipped {years} yrs")
        rc, _, _ = self._add(path, note="New {years}")
        self.assertEqual(json.load(open(path))["events"][0]["note"], "New {years}")

    def test_noop_readd_with_extras_leaves_file_stable(self):
        # m20 extends over m25: identical re-add (extras included) writes nothing
        path = self._cfg_with_extras("noop.json")
        mtime = os.stat(path).st_mtime_ns
        rc, out, _ = self._add(path)
        self.assertEqual(rc, 0)
        self.assertIn("already up to date", out)
        self.assertEqual(os.stat(path).st_mtime_ns, mtime)

    def test_fresh_add_has_only_schema_fields(self):
        path = _write_cfg(self.tmp, "fresh.json", {"events": []})
        rc, _, _ = _run_main(
            ["events", "add", "--config", path, "--title", "New", "--short", "New", "--date", "12-25"]
        )
        self.assertEqual(rc, 0)
        saved = json.load(open(path))["events"][0]
        self.assertEqual(
            set(saved), {"id", "title", "short_title", "date", "category"}
        )


class TestNonNegativeDurations(unittest.TestCase):
    """m26: negative durations are usage errors (exit 2), never silent no-ops."""

    NEGATIVE_ARGV = [
        ["radar", "--seconds", "-5"],
        ["hud", "--watch", "-0.001"],
        ["media", "--watch", "-1"],
        ["backlight", "demo", "--dwell", "-5"],
    ]

    def test_negative_flags_exit_2_before_any_device_touch(self):
        for argv in self.NEGATIVE_ARGV:
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as ctx:
                with contextlib.redirect_stderr(io.StringIO()):
                    cli.main(argv)
            self.assertEqual(ctx.exception.code, 2)

    def test_zero_still_valid_default_semantics_intact(self):
        ns = build_parser().parse_args(["hud", "--watch", "0"])
        self.assertEqual(ns.watch, 0.0)
        ns = build_parser().parse_args(["radar", "--seconds", "0"])
        self.assertEqual(ns.seconds, 0.0)

    def test_positive_still_parses(self):
        ns = build_parser().parse_args(["backlight", "demo", "--dwell", "2.5"])
        self.assertEqual(ns.dwell, 2.5)

    def test_non_finite_still_rejected_d24_regression(self):
        for bad in ("nan", "inf"):
            with self.subTest(bad=bad), self.assertRaises(SystemExit) as ctx:
                with contextlib.redirect_stderr(io.StringIO()):
                    cli.main(["radar", "--seconds", bad])
            self.assertEqual(ctx.exception.code, 2)

    def test_non_negative_float_unit(self):
        self.assertEqual(non_negative_float("0"), 0.0)
        self.assertEqual(non_negative_float("1.5"), 1.5)
        for bad in ("-1", "-0.001", "nan", "inf"):
            with self.subTest(bad=bad), self.assertRaises(argparse.ArgumentTypeError):
                non_negative_float(bad)
        # finite_float keeps its own D24 contract (nan/inf only)
        self.assertEqual(finite_float("-5"), -5.0)


class TestShimParityV030(unittest.TestCase):
    """The three new protocol constants ship through every public surface."""

    def test_new_constants_reexported_with_identity(self):
        import ak820
        from ak820 import protocol

        for name, value in (
            ("MAX_BRIGHTNESS", MAX_BRIGHTNESS),
            ("MAX_DIRECTION", MAX_DIRECTION),
            ("MAX_SPEED", MAX_SPEED),
        ):
            self.assertEqual(value, getattr(protocol, name))
            self.assertIs(getattr(ak820, name), getattr(protocol, name))
            self.assertIs(getattr(ak820_ctrl, name), getattr(protocol, name))
        self.assertIn("MAX_BRIGHTNESS", ak820.__all__)
        self.assertIn("MAX_SPEED", ak820.__all__)
        self.assertIn("MAX_DIRECTION", ak820.__all__)


if __name__ == "__main__":
    unittest.main()
