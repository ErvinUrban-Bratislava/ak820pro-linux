#!/usr/bin/env python3
"""v0.28.0 regression suite: m21 — `backlight.idle_rainbow` bool coercion
(zero hardware — MagicMock devs, no hidraw).

m21 (REVIEW.md §23.1 finding, §24 fix): `idle_rainbow` was the one idle field
in no `configvalues` list. A hand-edited `"idle_rainbow": "false"` is a
non-empty string — truthy in `set_backlight`'s `1 if rainbow else 0` byte
assembly, so rainbow turned ON when the user meant OFF (live-probe proven:
MODE_DATA[8] == 1) — and `null` silently meant OFF instead of a fix-the-config
error. `_as_bool` at the same m18/D22 choke point now coerces the lossless
spellings (bool, int 0/1, "true"/"false"/"1"/"0" text) and rejects everything
else with ConfigError (6) naming `backlight.idle_rainbow`.

Coverage: wire-byte proof (MODE_DATA[8]) for every accepted form through the
consumer seam `apply_idle_backlight(dev, path)`; exit 6 for every rejected
value through the load choke point `events list --config` AND ConfigError at
the consumer seam; coercion persisted by `events add`; and an anti-recurrence
invariant — every `bl.get(...)` key `get_idle_backlight_cfg` reads must appear
in one of the three coercion field lists, so a future idle field in no list
fails the suite mechanically (m21's root cause).
"""

import contextlib
import inspect
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ak820_ctrl
from ak820 import backlight, cli, coercion, events


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


def _idle_cfg(tmp, name, rainbow):
    """Custom-preset config so the cfg's own rainbow reaches the wire (a
    SHOWCASE_PRESETS key would make apply_idle_backlight use the preset's)."""
    return _write_cfg(
        tmp,
        name,
        {"backlight": {"idle_preset": "custom", "idle_color": "#FF0000", "idle_rainbow": rainbow}},
    )


def _rainbow_byte(cfg_path):
    """MODE_DATA (3rd feature report) byte 8 — the firmware rainbow bit."""
    dev = mock.MagicMock()
    backlight.apply_idle_backlight(dev, cfg_path)
    return dev.send_feature.call_args_list[2][0][0][8]


class TestIdleRainbowWireBytes(unittest.TestCase):
    """Every accepted spelling reaches MODE_DATA[8] as the intended bit."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_native_bools(self):
        self.assertEqual(_rainbow_byte(_idle_cfg(self.tmp, "b1.json", True)), 1)
        self.assertEqual(_rainbow_byte(_idle_cfg(self.tmp, "b0.json", False)), 0)

    def test_text_spellings_including_the_m21_repro(self):
        forms = (("true", 1), ("false", 0), ("TRUE", 1), ("False", 0), (" true ", 1), ("1", 1), ("0", 0))
        for i, (text, bit) in enumerate(forms):
            with self.subTest(text=text):
                self.assertEqual(_rainbow_byte(_idle_cfg(self.tmp, f"t{i}.json", text)), bit)

    def test_int_zero_and_one(self):
        self.assertEqual(_rainbow_byte(_idle_cfg(self.tmp, "i1.json", 1)), 1)
        self.assertEqual(_rainbow_byte(_idle_cfg(self.tmp, "i0.json", 0)), 0)

    def test_absent_field_defaults_off(self):
        cfg = _write_cfg(
            self.tmp, "absent.json", {"backlight": {"idle_preset": "custom", "idle_color": "#FF0000"}}
        )
        self.assertEqual(_rainbow_byte(cfg), 0)
        self.assertIs(backlight.get_idle_backlight_cfg(cfg)["rainbow"], False)


class TestIdleRainbowRejected6(unittest.TestCase):
    """Every non-lossless value is the fix-the-config class (6) at both seams."""

    REJECTED = ("yes", "no", "on", "", "2", 2, -1, None, 1.0, [True], {"v": 1})

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_events_list_exits_6_naming_the_field(self):
        for i, bad in enumerate(self.REJECTED):
            cfg = _idle_cfg(self.tmp, f"r{i}.json", bad)
            with self.subTest(value=bad), self.assertRaises(SystemExit) as ctx:
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    cli.main(["events", "list", "--config", cfg])
            self.assertEqual(ctx.exception.code, 6)
            self.assertIn("backlight.idle_rainbow", err.getvalue())
            self.assertIn("must be a boolean", err.getvalue())

    def test_null_message_uses_json_kind(self):
        cfg = _idle_cfg(self.tmp, "null.json", None)
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()) as err:
                cli.main(["events", "list", "--config", cfg])
        self.assertEqual(ctx.exception.code, 6)
        self.assertIn("got null", err.getvalue())

    def test_apply_idle_backlight_consumer_seam_raises_config_error(self):
        for i, bad in enumerate(self.REJECTED):
            cfg = _idle_cfg(self.tmp, f"a{i}.json", bad)
            with self.subTest(value=bad), self.assertRaises(events.ConfigError):
                backlight.apply_idle_backlight(mock.MagicMock(), cfg)


class TestCoercionEffects(unittest.TestCase):
    """Downstream consumers see a real bool, and saves persist the coerced form."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_load_returns_real_bool_not_the_string(self):
        cfg = _idle_cfg(self.tmp, "c.json", "false")
        self.assertIs(backlight.get_idle_backlight_cfg(cfg)["rainbow"], False)
        cfg = _idle_cfg(self.tmp, "t.json", "1")
        self.assertIs(backlight.get_idle_backlight_cfg(cfg)["rainbow"], True)

    def test_events_add_persists_coerced_bool(self):
        # add saves the loaded (coerced) cfg — the hand-edited "false" never
        # survives a round-trip, mirroring the m18 int-title lock.
        cfg = _idle_cfg(self.tmp, "p.json", "false")
        rc, _, _ = _run_main(
            ["events", "add", "--config", cfg, "--title", "X", "--short", "X", "--date", "12-25"]
        )
        self.assertEqual(rc, 0)
        saved = json.load(open(cfg))["backlight"]["idle_rainbow"]
        self.assertIs(saved, False)


class TestIdleFieldCoverageInvariant(unittest.TestCase):
    """m21's root cause — an idle field in no coercion list — fails mechanically."""

    def test_every_idle_cfg_field_is_coerced(self):
        src = inspect.getsource(backlight.get_idle_backlight_cfg)
        fields = set(re.findall(r'bl\.get\("(\w+)"', src))
        self.assertTrue(fields)  # the regex must actually match the seam
        # v0.34.0: the idle-field coercion lists moved with the coercion engine
        # into ak820.coercion (the loader half of the configvalues contract).
        # The invariant is unchanged — every bl.get field must be coerced.
        covered = (
            set(coercion._IDLE_TEXT_FIELDS)
            | set(coercion._IDLE_INT_FIELDS)
            | set(coercion._IDLE_BOOL_FIELDS)
        )
        self.assertEqual(fields - covered, set())

    def test_bool_list_holds_exactly_idle_rainbow(self):
        self.assertEqual(coercion._IDLE_BOOL_FIELDS, ("idle_rainbow",))


if __name__ == "__main__":
    unittest.main()
