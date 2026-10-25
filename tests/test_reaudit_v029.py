#!/usr/bin/env python3
"""v0.29.0 comprehensive re-audit regression suite (zero hardware — MagicMock
devs and temp configs, no hidraw).

D26 (REVIEW.md §25): `"events": null` passed D22's `is not None` guard, but
every consumer reads `cfg.get("events", [])` — the default only covers an
ABSENT key, so the null reached the countdown/add loops as None and crashed
with a raw TypeError (exit 1) on `events list`, `events add`, and every radar
render. Live-probe proven pre-fix.

D25 (§25): a non-object `backlight` section (string/list/number/null) was
never shape-checked at all — `get_idle_backlight_cfg`'s `bl.get` crashed every
idle restore (`status idle`, `run` completion, keypress restore) with a raw
AttributeError (exit 1). Live-probe proven on real hardware pre-fix.

Both now die at the single `load_events_config` choke point as ConfigError (6)
naming the section and the JSON kind (`got null`, never `got NoneType`) —
missing sections stay fine (defaults apply), present-but-wrong-typed is named,
never guessed.

m22 (§25): `--dry-run probe` printed "nothing to preview" instead of the
interface report — probe opens no hidraw node and sends zero USB bytes, so
dry-run has nothing to withhold. probe now routes before the dry-run block.

Anti-recurrence invariants (the root causes, not just the instances):
* no `is not None` section guard may ever return to `load_events_config` —
  treating null as absent is exactly the D26 bug pattern;
* every JSON kind is rejected for both checked sections (table-driven), so a
  future relaxation fails the suite mechanically.
"""

import ast
import contextlib
import inspect
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
from ak820 import backlight, cli, configvalues, events, status
from ak820.errors import ConfigError


def _run_main(argv):
    """cli.main(argv) capturing stdout+stderr; returns (rc, out, err)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


def _main_exit(argv):
    """cli.main(argv) expected to sys.exit; returns the exit code.

    try/except (not contextlib.suppress(...).exception) — the `.exception`
    attribute is Python 3.11+, and CI runs the 3.10 floor.
    """
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            cli.main(argv)
        except SystemExit as exc:
            return exc.code
    raise AssertionError(f"expected SystemExit for {argv}")


def _write_cfg(tmp, name, cfg):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    return path


# Every JSON kind a hand-edited file can hold for a section value.
WRONG_KINDS = {
    "null": None,
    "str": "warm",
    "int": 5,
    "float": 1.5,
    "bool": True,
    "list": [],
    "object": {"idle_mode": 1},
}


class TestEventsNullRejected(unittest.TestCase):
    """D26: a present-but-null `events` is ConfigError 6 at the choke point."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = _write_cfg(self.tmp, "ev_null.json", {"events": None})

    def test_load_raises_config_error_naming_section_and_kind(self):
        with self.assertRaises(ConfigError) as ctx:
            events.load_events_config(self.path)
        msg = str(ctx.exception)
        self.assertIn("'events' must be a list", msg)
        self.assertIn("got null", msg)  # JSON spelling, never NoneType
        self.assertNotIn("NoneType", msg)

    def test_cli_list_exits_6_no_traceback(self):
        code = _main_exit(["events", "list", "--config", self.path])
        self.assertEqual(code, 6)

    def test_cli_add_exits_6_and_leaves_file_untouched(self):
        before = Path(self.path).read_bytes()
        code = _main_exit(
            [
                "events", "add", "--config", self.path,
                "--title", "T", "--short", "t", "--date", "01-01",
            ]
        )
        self.assertEqual(code, 6)
        self.assertEqual(Path(self.path).read_bytes(), before)

    def test_dry_run_radar_exits_6(self):
        code = _main_exit(["--dry-run", "radar", "--config", self.path])
        self.assertEqual(code, 6)

    def test_absent_events_key_still_fine(self):
        path = _write_cfg(self.tmp, "no_events.json", {"default_mode": "radar"})
        cfg = events.load_events_config(path)  # must not raise
        self.assertEqual(events.compute_upcoming_events(cfg), [])

    def test_empty_events_list_still_fine(self):
        path = _write_cfg(self.tmp, "empty.json", {"events": []})
        self.assertEqual(events.compute_upcoming_events(events.load_events_config(path)), [])


class TestBacklightSectionShape(unittest.TestCase):
    """D25: a non-object `backlight` section is ConfigError 6 at the choke point."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _cfg(self, value, name="bl.json"):
        return _write_cfg(self.tmp, name, {"backlight": value, "events": []})

    def test_every_wrong_kind_rejected_naming_section_and_kind(self):
        for kind_name, value in WRONG_KINDS.items():
            if kind_name == "object":
                continue  # the declared type
            with self.subTest(kind_name):
                path = self._cfg(value)
                with self.assertRaises(ConfigError) as ctx:
                    events.load_events_config(path)
                msg = str(ctx.exception)
                self.assertIn("'backlight' must be an object", msg)
                self.assertIn(f"got {kind_name}", msg)
                self.assertNotIn("NoneType", msg)

    def test_object_section_still_fine(self):
        path = self._cfg({"idle_preset": "off", "idle_mode": 0})
        cfg = events.load_events_config(path)
        self.assertEqual(cfg["backlight"]["idle_preset"], "off")

    def test_absent_and_empty_sections_keep_defaults(self):
        absent = _write_cfg(self.tmp, "absent.json", {"events": []})
        empty = _write_cfg(self.tmp, "empty_bl.json", {"backlight": {}, "events": []})
        for path in (absent, empty):
            with self.subTest(path):
                cfg = backlight.get_idle_backlight_cfg(path)
                self.assertEqual(cfg["preset"], "warm-white")
                self.assertIs(cfg["rainbow"], False)

    def test_consumer_seam_raises_config_error_not_attribute_error(self):
        path = self._cfg("warm")
        with self.assertRaises(ConfigError):
            backlight.get_idle_backlight_cfg(path)

    def test_apply_idle_backlight_fails_before_touching_device(self):
        dev = mock.MagicMock()
        with self.assertRaises(ConfigError):
            backlight.apply_idle_backlight(dev, self._cfg([1, 2]))
        dev.send_feature.assert_not_called()

    def test_status_idle_consumer_seam(self):
        dev = mock.MagicMock()
        with self.assertRaises(ConfigError):
            status.set_status_backlight(dev, "idle", config_path=self._cfg(None))
        dev.send_feature.assert_not_called()

    def test_cli_status_idle_exits_6_on_device_path(self):
        # Full CLI seam (v0.27 pattern): discovery + device mocked, the config
        # violation must surface as exit 6 through main()'s taxonomy branch.
        path = self._cfg("warm")
        with mock.patch.object(cli, "find_hidraw_devices", return_value=("/dev/x", "/dev/y")), \
             mock.patch.object(cli, "AK820ProDevice") as dev_cls:
            code = _main_exit(["status", "idle", "--config", path])
        self.assertEqual(code, 6)
        dev_cls.return_value.__enter__.return_value.send_feature.assert_not_called()


class TestSectionKindVocabularyShared(unittest.TestCase):
    """One vocabulary for every config violation: `configvalues.kind` is the
    single home, used by the section-shape checks too (m18/m21 precedent)."""

    def test_kind_is_public_and_json_flavoured(self):
        self.assertEqual(configvalues.kind(None), "null")
        self.assertEqual(configvalues.kind("s"), "str")
        self.assertEqual(configvalues.kind(1), "int")
        self.assertEqual(configvalues.kind(True), "bool")

    def test_events_module_uses_the_shared_helper(self):
        src = inspect.getsource(events.load_events_config)
        self.assertIn("configvalues.kind(", src)
        self.assertNotIn("type(", src)  # no private-vocabulary drift back in


class TestNoIsNotNoneSectionGuard(unittest.TestCase):
    """Anti-recurrence (D26's root cause): a section guard written as
    `if cfg.get(x) is not None:` treats null as absent — the exact bug pattern.
    The choke point must use `in cfg` membership tests instead."""

    def test_choke_point_has_no_is_not_none_guard(self):
        # AST-level (not substring): the docstring legitimately NARRATES the
        # bug pattern; only executable None-comparisons are forbidden — any
        # `x is None` / `x is not None` / `== None` guard in the choke point
        # re-opens the D26 door (null slipping through as "absent").
        tree = ast.parse(inspect.getsource(events.load_events_config))
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for comparator in node.comparators:
                    self.assertFalse(
                        isinstance(comparator, ast.Constant) and comparator.value is None,
                        f"line {node.lineno}: None-comparison in load_events_config "
                        "(D26 bug pattern: null treated as absent)",
                    )


class TestTableDrivenSectionRejection(unittest.TestCase):
    """Every JSON kind is rejected for a section declared as the other type —
    a future relaxation of either check fails mechanically, per kind."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_events_accepts_only_list(self):
        for kind_name, value in WRONG_KINDS.items():
            if kind_name == "list":
                continue
            with self.subTest(kind_name):
                path = _write_cfg(self.tmp, f"ev_{kind_name}.json", {"events": value})
                with self.assertRaises(ConfigError):
                    events.load_events_config(path)

    def test_backlight_accepts_only_object(self):
        for kind_name, value in WRONG_KINDS.items():
            if kind_name == "object":
                continue
            with self.subTest(kind_name):
                path = _write_cfg(self.tmp, f"bl_{kind_name}.json", {"backlight": value})
                with self.assertRaises(ConfigError):
                    events.load_events_config(path)


class TestDryRunProbePassthrough(unittest.TestCase):
    """m22: probe sends zero USB bytes — --dry-run must show the report."""

    def test_dry_run_probe_reports_interfaces(self):
        with mock.patch.object(cli, "find_hidraw_devices", return_value=("/dev/hidraw2", "/dev/hidraw3")):
            rc, out, err = _run_main(["--dry-run", "probe"])
        self.assertEqual(rc, 0)
        self.assertIn("/dev/hidraw2", out)
        self.assertIn("/dev/hidraw3", out)
        self.assertNotIn("nothing to preview", out)

    def test_dry_run_probe_reports_not_found_headless(self):
        with mock.patch.object(cli, "find_hidraw_devices", return_value=(None, None)):
            rc, out, err = _run_main(["--dry-run", "probe"])
        self.assertEqual(rc, 0)
        self.assertIn("NOT FOUND", out)

    def test_plain_probe_unchanged(self):
        with mock.patch.object(cli, "find_hidraw_devices", return_value=("/dev/hidraw2", "/dev/hidraw3")):
            rc, out, err = _run_main(["probe"])
        self.assertEqual(rc, 0)
        self.assertIn("/dev/hidraw2", out)

    def test_dry_run_still_previews_renderers(self):
        # The dry-run block itself must stay intact for real render commands.
        tmp = tempfile.mkdtemp()
        cfg = _write_cfg(
            tmp,
            "ok.json",
            {"events": [{"id": "x", "title": "X", "date": "01-01", "category": "milestone"}]},
        )
        rc, out, err = _run_main(["--dry-run", "radar", "--config", cfg])
        self.assertEqual(rc, 0)
        self.assertIn("dry-run", out.lower())

    def test_dry_run_non_render_command_message_kept(self):
        # backlight list etc. keep the documented "nothing to preview" line —
        # only probe (pure observation) was rerouted.
        rc, out, err = _run_main(["--dry-run", "backlight", "list"])
        self.assertEqual(rc, 0)
        self.assertIn("nothing to preview", out)


class TestShimSurfaceIntact(unittest.TestCase):
    """The re-audit touched configvalues' public surface (`kind` promoted):
    the package/shim identity contracts must hold."""

    def test_shim_reexports_still_identity(self):
        # load_events_config rides the milestone_radar shim (v0.25 split),
        # get_idle_backlight_cfg the ak820_ctrl shim.
        import milestone_radar

        self.assertIs(milestone_radar.load_events_config, events.load_events_config)
        self.assertIs(ak820_ctrl.get_idle_backlight_cfg, backlight.get_idle_backlight_cfg)

    def test_coerce_config_values_unchanged_signature(self):
        cfg = {"events": [{"title": 123}], "backlight": {"idle_rainbow": "false"}}
        out = configvalues.coerce_config_values(cfg, "x")
        self.assertEqual(out["events"][0]["title"], "123")
        self.assertIs(out["backlight"]["idle_rainbow"], False)


if __name__ == "__main__":
    unittest.main()
