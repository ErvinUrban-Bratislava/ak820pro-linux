#!/usr/bin/env python3
"""v0.16.0 regression suite: the cli.py -> parser.py + handlers.py split.

Locks the modularity refactor against the failure modes that matter:
1. Re-export identity — `cli.X is parser.X / handlers.X` (one object, no copy
   drift between the split modules and the compat surface).
2. Shim parity — `ak820_ctrl` and `ak820/__init__` still resolve every CLI
   entry point through `ak820.cli` unchanged.
3. Structural parity — every device-bound subcommand the parser accepts has
   a `DEVICE_HANDLERS` entry (dispatch can never 404 on a parsed command).
4. Module budget — every `ak820/` module stays strictly < 300 lines (§10 DoD).
5. No import cycle: `ak820.cli` imports cleanly in a fresh interpreter.
All offline, zero hardware.
"""

import contextlib
import io
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ak820
import ak820_ctrl
from ak820 import cli, handlers, parser


class TestReexportIdentity(unittest.TestCase):
    """cli must re-export the split modules' names — same objects, not copies."""

    def test_build_parser_is_the_parser_module_object(self):
        self.assertIs(cli.build_parser, parser.build_parser)

    def test_handlers_surface_is_the_handlers_module_objects(self):
        self.assertIs(cli.run_watch_loop, handlers.run_watch_loop)
        self.assertIs(cli.DEVICE_HANDLERS, handlers.DEVICE_HANDLERS)
        self.assertIs(cli.TFT_COMMANDS, handlers.TFT_COMMANDS)
        for name in ("cmd_upload", "cmd_radar", "cmd_card", "cmd_hud", "cmd_media",
                     "cmd_sync", "cmd_backlight", "cmd_status", "cmd_run"):
            self.assertIs(getattr(cli, name), getattr(handlers, name), f"cli.{name} drifted")

    def test_package_and_shim_resolve_through_cli(self):
        self.assertIs(ak820.build_parser, parser.build_parser)
        self.assertIs(ak820.main, cli.main)
        self.assertIs(ak820.run_watch_loop, handlers.run_watch_loop)
        self.assertIs(ak820_ctrl.build_parser, parser.build_parser)
        self.assertIs(ak820_ctrl.main, cli.main)
        self.assertIs(ak820_ctrl.run_watch_loop, handlers.run_watch_loop)


class TestDispatchStructuralParity(unittest.TestCase):
    """Every parsed device command must have a handler; offline ones must not."""

    OFFLINE_COMMANDS = {"events", "probe"}  # handled before DEVICE_HANDLERS

    def test_every_device_subcommand_has_a_handler(self):
        p = parser.build_parser()
        choices = set(p._subparsers._group_actions[0].choices)
        for cmd in choices - self.OFFLINE_COMMANDS:
            self.assertIn(cmd, handlers.DEVICE_HANDLERS, f"'{cmd}' parses but never dispatches")

    def test_tft_commands_are_a_subset_of_device_handlers(self):
        for cmd in handlers.TFT_COMMANDS:
            self.assertIn(cmd, handlers.DEVICE_HANDLERS)

    def test_stats_alias_shares_hud_handler(self):
        self.assertIs(handlers.DEVICE_HANDLERS["stats"], handlers.DEVICE_HANDLERS["hud"])


class TestParserStillWorks(unittest.TestCase):
    """Spot-check the moved argparse tree end-to-end through the cli surface."""

    def test_global_flags_parse(self):
        args = cli.build_parser().parse_args(["--dry-run", "-v", "probe"])
        self.assertTrue(args.dry_run and args.verbose)

    def test_run_remainder_parses(self):
        args = cli.build_parser().parse_args(["run", "--no-wait", "--", "make", "-j8"])
        self.assertEqual(args.cmd, ["--", "make", "-j8"])

    def test_events_add_requires_fields(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                cli.build_parser().parse_args(["events", "add", "--title", "x"])

    def test_main_still_maps_dispatch_errors(self):
        # _dispatch must stay in cli (mock.patch target used across the suite)
        from ak820.errors import DeviceNotFoundError
        with mock.patch("ak820.cli._dispatch", side_effect=DeviceNotFoundError("x")):
            with self.assertRaises(SystemExit) as ctx:
                with contextlib.redirect_stderr(io.StringIO()):
                    cli.main(["probe"])
        self.assertEqual(ctx.exception.code, 3)


class TestWatchLoopViaReexport(unittest.TestCase):
    def test_keyboard_interrupt_stops_cleanly(self):
        calls = []

        def push():
            calls.append(1)
            raise KeyboardInterrupt

        with mock.patch.object(handlers.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            cli.run_watch_loop(push, 1, "x")
        self.assertEqual(len(calls), 1)


class TestModuleBudgetV016(unittest.TestCase):
    """§10 DoD re-locked including the two v0.16.0 modules."""

    def test_all_package_modules_under_300_lines(self):
        for py in sorted((ROOT / "ak820").glob("*.py")):
            n = len(py.read_text(encoding="utf-8").splitlines())
            self.assertLess(n, 300, f"{py.name} is {n} lines")

    def test_new_modules_exist(self):
        self.assertTrue((ROOT / "ak820" / "parser.py").is_file())
        self.assertTrue((ROOT / "ak820" / "handlers.py").is_file())


class TestNoImportCycle(unittest.TestCase):
    def test_fresh_interpreter_imports_cli(self):
        code = (f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
                "import ak820.cli, ak820.parser, ak820.handlers, ak820_ctrl; print('ok')")
        res = subprocess.run([sys.executable, "-I", "-c", code], cwd=ROOT,
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("ok", res.stdout)


if __name__ == "__main__":
    unittest.main()
