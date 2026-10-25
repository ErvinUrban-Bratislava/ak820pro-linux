#!/usr/bin/env python3
"""v0.27.0 regression suite: --config parity on the device-bound surface +
`events add --note` + the compute_upcoming_events trust-boundary contract
(zero hardware — MagicMock devs and direct handler calls, no hidraw).

--config parity (v0.26.0 finding): `backlight set --save` / `backlight off
     --save` and the `status`/`run` idle restore called
     `load_events_config()`/`save_events_config()` with no path and exposed no
     flag — they honored only `$AK820_EVENTS_PATH`. `config_path` is now
     threaded through get/save/apply and every subcommand accepts `--config`
     wherever `radar`/`events` do (backlight: parent + SUPPRESS sub-action
     positions, sub-action wins; status/run: plain flag).
--note: `events add` could not write `note` — the one field requiring
     hand-edited JSON. `--note TEXT` validates through the shipped
     `configvalues.validate_note` `{years}`-only rule (ConfigError 6), and a
     re-add without `--note` preserves the existing entry's note instead of
     silently dropping it.
Trust boundary: hand-injected dicts bypass `configvalues` by design — the
     decision is documented in the `compute_upcoming_events` docstring and
     locked here so the contract cannot silently vanish.
Cohesion: SHOWCASE_PRESETS moved to `ak820/presets.py` (pure data) to keep
     `backlight.py` under the §10 budget — re-export identity locked.
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

import ak820_ctrl
from ak820 import backlight, cli, configvalues, events, handlers, presets, status


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


def _parse(*argv):
    return cli.build_parser().parse_args(list(argv))


class TestBacklightConfigFlagParsing(unittest.TestCase):
    """`backlight [--config F] <action> [--config F]` parses like `events`."""

    def test_set_accepts_config_after_subaction(self):
        args = _parse("backlight", "set", "--preset", "warm-white", "--save", "--config", "F")
        self.assertEqual(args.config, "F")

    def test_accepts_config_before_subaction(self):
        args = _parse("backlight", "--config", "F", "set", "--save")
        self.assertEqual(args.config, "F")

    def test_subaction_position_wins_over_parent(self):
        args = _parse("backlight", "--config", "A", "set", "--save", "--config", "B")
        self.assertEqual(args.config, "B")

    def test_off_accepts_config_both_positions(self):
        self.assertEqual(_parse("backlight", "off", "--save", "--config", "F").config, "F")
        self.assertEqual(_parse("backlight", "--config", "F", "off", "--save").config, "F")

    def test_demo_and_list_accept_config_uniformly(self):
        # demo/list never touch the config — the flag parses and is ignored,
        # so scripts may pass one $CONFIG to every backlight invocation.
        self.assertEqual(_parse("backlight", "demo", "--config", "F").config, "F")
        self.assertEqual(_parse("backlight", "list", "--config", "F").config, "F")
        self.assertEqual(_parse("backlight", "--config", "F", "list").config, "F")

    def test_bare_subaction_config_is_none(self):
        # SUPPRESS on the sub-action must not delete the parent's None default.
        self.assertIsNone(_parse("backlight", "list").config)
        self.assertIsNone(_parse("backlight", "set", "--preset", "off").config)


class TestStatusRunConfigParsing(unittest.TestCase):
    def test_status_accepts_config(self):
        args = _parse("status", "idle", "--config", "F")
        self.assertEqual((args.config, args.state), ("F", "idle"))

    def test_run_accepts_config_before_cmd(self):
        args = _parse("run", "--config", "F", "--no-wait", "--", "true")
        self.assertEqual((args.config, args.cmd), ("F", ["--", "true"]))

    def test_defaults_none(self):
        self.assertIsNone(_parse("status", "idle").config)
        self.assertIsNone(_parse("run", "--no-wait", "--", "true").config)


class TestIdleCfgPathThreading(unittest.TestCase):
    """get/save/apply honor config_path over $AK820_EVENTS_PATH."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env_cfg = _write_cfg(
            self.tmp, "env.json", {"backlight": {"idle_preset": "warm-white"}}
        )
        self.path_cfg = _write_cfg(
            self.tmp, "path.json", {"backlight": {"idle_preset": "off", "idle_mode": 0}}
        )

    def test_get_config_path_beats_env(self):
        with mock.patch.dict(os.environ, {"AK820_EVENTS_PATH": self.env_cfg}):
            idle = backlight.get_idle_backlight_cfg(self.path_cfg)
        self.assertEqual(idle["preset"], "off")

    def test_get_without_path_still_uses_env(self):
        with mock.patch.dict(os.environ, {"AK820_EVENTS_PATH": self.env_cfg}):
            idle = backlight.get_idle_backlight_cfg()
        self.assertEqual(idle["preset"], "warm-white")

    def test_save_writes_only_config_path(self):
        with mock.patch.dict(os.environ, {"AK820_EVENTS_PATH": self.env_cfg}):
            backlight.save_idle_backlight_cfg(idle_preset="off", mode=0, brightness=0,
                                              config_path=self.path_cfg)
        saved = json.load(open(self.path_cfg))
        self.assertEqual(saved["backlight"]["idle_preset"], "off")
        env_saved = json.load(open(self.env_cfg))
        self.assertEqual(env_saved["backlight"]["idle_preset"], "warm-white")  # untouched

    def test_apply_idle_backlight_uses_config_path(self):
        # Wire proof: MODE_DATA (3rd feature report) carries the preset's mode
        # byte — 0 from the path config's "off", not 1 from the env "warm-white".
        dev = mock.MagicMock()
        with mock.patch.dict(os.environ, {"AK820_EVENTS_PATH": self.env_cfg}):
            backlight.apply_idle_backlight(dev, self.path_cfg)
        mode_data = dev.send_feature.call_args_list[2][0][0]
        self.assertEqual(mode_data[0], 0)

    def test_apply_idle_backlight_bad_color_in_path_still_config_error(self):
        # m19 regression through the NEW seam: the override path is validated
        # by the same choke point (load_events_config) as the default path.
        bad = _write_cfg(
            self.tmp, "bad.json", {"backlight": {"idle_preset": "custom", "idle_color": "#zzz"}}
        )
        with self.assertRaises(events.ConfigError):
            backlight.apply_idle_backlight(mock.MagicMock(), bad)


class TestHandlerThreading(unittest.TestCase):
    """cmd_backlight/cmd_status/cmd_run thread args.config to the config seam."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = _write_cfg(self.tmp, "ev.json", {"events": []})

    def test_cmd_backlight_set_save_passes_config_path(self):
        args = _parse("backlight", "set", "--preset", "warm-white", "--save", "--config", self.cfg)
        with mock.patch.object(
            backlight, "save_idle_backlight_cfg", wraps=backlight.save_idle_backlight_cfg
        ) as spy:
            handlers.cmd_backlight(args, mock.MagicMock())
        self.assertEqual(spy.call_args.kwargs["config_path"], self.cfg)
        self.assertEqual(json.load(open(self.cfg))["backlight"]["idle_preset"], "warm-white")

    def test_cmd_backlight_off_save_passes_config_path(self):
        args = _parse("backlight", "off", "--save", "--config", self.cfg)
        with mock.patch.object(
            backlight, "save_idle_backlight_cfg", wraps=backlight.save_idle_backlight_cfg
        ) as spy:
            handlers.cmd_backlight(args, mock.MagicMock())
        self.assertEqual(spy.call_args.kwargs["config_path"], self.cfg)

    def test_cmd_backlight_without_flag_passes_none(self):
        args = _parse("backlight", "set", "--preset", "off", "--save")
        with mock.patch.object(
            backlight, "save_idle_backlight_cfg", wraps=backlight.save_idle_backlight_cfg
        ) as spy:
            with mock.patch.dict(os.environ, {"AK820_EVENTS_PATH": self.cfg}):
                handlers.cmd_backlight(args, mock.MagicMock())
        self.assertIsNone(spy.call_args.kwargs["config_path"])

    def test_cmd_status_idle_passes_config_path(self):
        args = _parse("status", "idle", "--config", self.cfg)
        dev = mock.MagicMock()
        with mock.patch.object(status, "apply_idle_backlight") as spy:
            handlers.cmd_status(args, dev)
        spy.assert_called_once_with(dev, self.cfg)

    def test_cmd_status_success_wait_passes_config_path(self):
        args = _parse("status", "success", "--config", self.cfg)
        dev = mock.MagicMock()
        with mock.patch.object(status, "wait_for_keypress", return_value=True), mock.patch.object(
            status, "apply_idle_backlight"
        ) as spy:
            handlers.cmd_status(args, dev)
        spy.assert_called_once_with(dev, self.cfg)

    def test_cmd_run_passes_config_path(self):
        args = _parse("run", "--config", self.cfg, "--no-wait", "--", "true")
        with mock.patch.object(handlers, "set_status_backlight") as spy:
            handlers.cmd_run(args, mock.MagicMock())
        self.assertGreaterEqual(spy.call_count, 2)  # in-progress + success
        for call in spy.call_args_list:
            self.assertEqual(call.kwargs["config_path"], self.cfg)


class TestEventsAddNote(unittest.TestCase):
    """`events add --note` writes the JSON-only field; the rule is configvalues'."""

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
                        "note": "old {years}",
                    }
                ]
            },
        )

    def _add(self, *extra):
        argv = [
            "events", "add", "--config", self.cfg,
            "--title", "Christmas", "--short", "XMAS", "--date", "12-25",
        ]
        argv.extend(extra)
        return _run_main(argv)

    def _entry(self):
        return json.load(open(self.cfg))["events"][0]

    def test_add_writes_note(self):
        rc, out, _ = self._add("--note", "Merry {years}!")
        self.assertEqual(rc, 0)
        self.assertIn("Saved", out)
        self.assertEqual(self._entry()["note"], "Merry {years}!")

    def test_add_note_bad_placeholder_exits_6_names_flag(self):
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()) as err:
                cli.main(
                    ["events", "add", "--config", self.cfg, "--title", "X", "--short", "X",
                     "--date", "12-25", "--note", "{years} in {place}"]
                )
        self.assertEqual(ctx.exception.code, 6)
        self.assertIn("--note", err.getvalue())
        self.assertIn("years", err.getvalue())

    def test_add_note_malformed_braces_exits_6(self):
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()):
                cli.main(
                    ["events", "add", "--config", self.cfg, "--title", "X", "--short", "X",
                     "--date", "12-25", "--note", "oops {unclosed"]
                )
        self.assertEqual(ctx.exception.code, 6)

    def test_invalid_note_leaves_file_untouched(self):
        before = open(self.cfg, "rb").read()
        with contextlib.suppress(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            cli.main(
                ["events", "add", "--config", self.cfg, "--title", "Christmas", "--short", "XMAS",
                 "--date", "12-25", "--note", "{nope}"]
            )
        self.assertEqual(open(self.cfg, "rb").read(), before)

    def test_readd_without_note_preserves_note(self):
        # The v0.27.0 fix: a re-add used to silently drop the entry's note.
        rc, out, _ = self._add()
        self.assertEqual(rc, 0)
        self.assertIn("already up to date", out)
        self.assertEqual(self._entry()["note"], "old {years}")

    def test_readd_date_change_without_note_keeps_note(self):
        rc, out, _ = self._add("--date", "12-26")
        self.assertEqual(rc, 0)
        self.assertIn("Saved", out)
        entry = self._entry()
        self.assertEqual((entry["date"], entry["note"]), ("12-26", "old {years}"))

    def test_readd_with_note_replaces_note(self):
        rc, _, _ = self._add("--note", "new {years}")
        self.assertEqual(rc, 0)
        self.assertEqual(self._entry()["note"], "new {years}")

    def test_noop_readd_with_preserved_note_skips_write(self):
        # m20 semantics extend over the preserved note: identical + preserved
        # note -> still a byte-for-byte no-op.
        mtime = os.stat(self.cfg).st_mtime_ns
        rc, out, _ = self._add("--note", "old {years}")
        self.assertEqual(rc, 0)
        self.assertIn("already up to date", out)
        self.assertEqual(os.stat(self.cfg).st_mtime_ns, mtime)

    def test_note_renders_milestone_str(self):
        rc, out, _ = _run_main(["events", "list", "--config", self.cfg])
        self.assertEqual(rc, 0)
        self.assertIn("old 0", out)  # "old {years}" formatted with years=0

    def test_empty_note_rejected(self):
        # SUPERSEDED in v0.32.0 (m28): an empty/whitespace --note used to be
        # stored verbatim and rendered an empty milestone column. The m27
        # non-blank rule now covers --note too, so the flag is either a real
        # note or a friendly exit 6 — omit it to keep the existing note.
        # The LOADER still accepts "note": "" (explicit "Milestone" fallback);
        # the strictness lives on the creation path only (trust boundary).
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()) as err:
                cli.main(
                    ["events", "add", "--config", self.cfg, "--title", "X", "--short", "X",
                     "--date", "12-25", "--note", ""]
                )
        self.assertEqual(ctx.exception.code, 6)
        self.assertIn("--note", err.getvalue())

    def test_add_note_to_fresh_config(self):
        path = os.path.join(self.tmp, "fresh.json")
        rc, out, _ = _run_main(
            ["events", "add", "--config", path, "--title", "Ship", "--short", "SHIP",
             "--date", "01-01", "--note", "V{years}"]
        )
        self.assertEqual(rc, 0)
        self.assertIn("Saved", out)
        self.assertEqual(json.load(open(path))["events"][0]["note"], "V{years}")


class TestSharedRuleAndCohesion(unittest.TestCase):
    """One {years} rule for both entry points; presets re-export identity; budget."""

    def test_validate_note_public_rule(self):
        configvalues.validate_note("only {years} here", "--note")  # no raise
        for bad in ("{place}", "{}", "{0}", "unbalanced {"):
            with self.assertRaises(events.ConfigError, msg=bad):
                configvalues.validate_note(bad, "--note")

    def test_cli_and_loader_share_the_rule(self):
        # The same note string is rejected by BOTH entry points with exit 6 —
        # configvalues.validate_note is the single home of the rule.
        tmp = tempfile.mkdtemp()
        cfg = _write_cfg(tmp, "n.json", {"events": [{"title": "X", "date": "12-25",
                                                    "note": "{years} in {place}"}]})
        with self.assertRaises(SystemExit) as loader:
            with contextlib.redirect_stderr(io.StringIO()):
                cli.main(["events", "list", "--config", cfg])
        with self.assertRaises(SystemExit) as adder:
            with contextlib.redirect_stderr(io.StringIO()):
                cli.main(["events", "add", "--config", cfg, "--title", "Y", "--short", "Y",
                          "--date", "01-01", "--note", "{years} in {place}"])
        self.assertEqual((loader.exception.code, adder.exception.code), (6, 6))

    def test_trust_boundary_documented(self):
        doc = events.compute_upcoming_events.__doc__ or ""
        self.assertIn("trust boundary", doc)
        self.assertIn("choke point", doc)

    def test_presets_reexport_identity(self):
        self.assertIs(backlight.SHOWCASE_PRESETS, presets.SHOWCASE_PRESETS)
        self.assertIs(ak820_ctrl.SHOWCASE_PRESETS, presets.SHOWCASE_PRESETS)
        self.assertIs(status.SHOWCASE_PRESETS, presets.SHOWCASE_PRESETS)

    def test_touched_modules_under_budget(self):
        for rel in ("ak820/presets.py", "ak820/backlight.py", "ak820/parser.py",
                    "ak820/status.py", "ak820/handlers.py", "ak820/config.py",
                    "ak820/configvalues.py", "ak820/events.py"):
            n = len((ROOT / rel).read_text(encoding="utf-8").splitlines())
            self.assertLess(n, 300, f"{rel} is {n} lines")


if __name__ == "__main__":
    unittest.main()
