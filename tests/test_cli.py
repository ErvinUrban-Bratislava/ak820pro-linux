#!/usr/bin/env python3
"""v0.9.0 regression suite: CLI dispatch (§2.3), exit-code contract (§3.4),
compat-shim semantics, module budget (<300 lines), m12 progress gating, and
the single shared --watch loop (§10.5). All device-free (FakeTransport / mocks).
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
from ak820 import actions, backlight, cli, handlers, state, transport


class TestParserParity(unittest.TestCase):
    """build_parser() must reproduce the historical ak820_ctrl tree flag-for-flag."""

    def test_radar_flags(self):
        args = cli.build_parser().parse_args(["radar", "--view", "hybrid", "--seconds", "3.5", "--force", "--sync-clock"])
        self.assertEqual((args.command, args.view, args.seconds, args.force, args.sync_clock),
                         ("radar", "hybrid", 3.5, True, True))

    def test_hud_alias_and_watch(self):
        args = cli.build_parser().parse_args(["stats", "--watch", "5"])
        self.assertEqual(args.command, "stats")
        self.assertEqual(args.watch, 5.0)

    def test_backlight_set_flags(self):
        args = cli.build_parser().parse_args(["backlight", "set", "--mode", "breath", "--brightness", "2", "--save"])
        self.assertEqual(args.bl_action, "set")
        self.assertEqual(args.mode, "breath")
        self.assertEqual(args.brightness, 2)
        self.assertTrue(args.save)

    def test_events_add_flags(self):
        args = cli.build_parser().parse_args(
            ["events", "add", "--title", "T", "--short", "S", "--date", "12-25", "--category", "birthday"]
        )
        self.assertEqual((args.events_action, args.date, args.category), ("add", "12-25", "birthday"))

    def test_run_remainder(self):
        args = cli.build_parser().parse_args(["run", "--no-wait", "--", "echo", "hi"])
        self.assertEqual(args.cmd, ["--", "echo", "hi"])

    def test_no_command_is_usage_error(self):
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as ctx:
                cli.build_parser().parse_args([])
        self.assertEqual(ctx.exception.code, 2)


class TestOfflineCommands(unittest.TestCase):
    """events CRUD + radar --save-preview run with zero USB access (§10.5)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = os.path.join(self.tmp, "events.json")
        with open(self.cfg, "w") as f:
            json.dump({"events": [
                {"id": "xmas", "title": "Christmas", "short_title": "XMAS", "date": "12-25", "category": "holiday"}
            ]}, f)

    def _quiet(self, argv):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            rc = cli.main(argv)
        return rc, buf.getvalue()

    def test_events_list_returns_zero(self):
        rc, out = self._quiet(["events", "--config", self.cfg, "list"])
        self.assertEqual(rc, 0)
        self.assertIn("Christmas", out)

    def test_events_list_json_is_machine_readable(self):
        rc, out = self._quiet(["events", "--config", self.cfg, "list", "--json"])
        self.assertEqual(rc, 0)
        rows = json.loads(out)
        self.assertEqual(rows[0]["title"], "Christmas")

    def test_events_add_valid_then_bad_date_exits_6(self):
        rc, _ = self._quiet(["events", "--config", self.cfg, "add",
                             "--title", "New", "--short", "NEW", "--date", "2027-01-01"])
        self.assertEqual(rc, 0)
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["events", "--config", self.cfg, "add",
                          "--title", "Bad", "--short", "BAD", "--date", "2026-02-30"])
        self.assertEqual(ctx.exception.code, 6)  # D4/D5 ConfigError contract preserved
        self.assertIn("Error:", err.getvalue())

    def test_radar_save_preview_is_device_free(self):
        out_dir = os.path.join(self.tmp, "prev")
        rc, _ = self._quiet(["radar", "--config", self.cfg, "--save-preview", out_dir, "--date", "2026-10-23"])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(os.path.join(out_dir, "radar_hero.png")))
        self.assertTrue(os.path.exists(os.path.join(out_dir, "radar_board.png")))


class TestExitCodeContract(unittest.TestCase):
    """§3.4: every AK820Error maps to its exit_code; ConfigError -> 6."""

    def _expect_exit(self, exc, argv):
        with mock.patch.object(cli, "_dispatch", side_effect=exc), \
             mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            with self.assertRaises(SystemExit) as ctx:
                cli.main(argv)
        return ctx.exception.code, err.getvalue()

    def test_upload_incomplete_is_5(self):
        code, err = self._expect_exit(ak820_ctrl.UploadIncompleteError("boom"), ["probe"])
        self.assertEqual(code, 5)
        self.assertIn("boom", err)

    def test_device_not_found_is_3(self):
        with mock.patch.object(cli, "find_hidraw_devices", return_value=(None, None)), \
             mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["upload", "whatever.png"])
        self.assertEqual(ctx.exception.code, 3)
        self.assertIn("not found", err.getvalue())

    def test_permission_denied_is_4(self):
        # D8 (v0.12.0): §3.4 reserves 4 for permission denied, so "udev rule
        # missing" is distinguishable from "device not found" (3) via $? alone.
        code, _ = self._expect_exit(ak820_ctrl.PermissionDeniedError("no perms"), ["probe"])
        self.assertEqual(code, 4)

    def test_config_error_is_6(self):
        import milestone_radar as mr

        code, err = self._expect_exit(mr.ConfigError("bad date"), ["probe"])
        self.assertEqual(code, 6)
        self.assertIn("bad date", err)

    def test_backlight_bad_mode_friendly_nonzero(self):
        # §10.5: main(["backlight","set","--mode","banana"]) -> non-zero + friendly message.
        with mock.patch.object(cli, "find_hidraw_devices", return_value=("/dev/x", "/dev/y")), \
             mock.patch.object(cli, "AK820ProDevice") as dev_cls:
            dev_cls.return_value.__enter__.return_value = mock.MagicMock()
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["backlight", "set", "--mode", "banana"])
        self.assertNotEqual(ctx.exception.code, 0)
        self.assertIn("Error:", str(ctx.exception.code))


class TestWatchLoop(unittest.TestCase):
    def test_single_shared_loop_pushes_then_stops(self):
        calls = []

        def push():
            calls.append(1)
            raise KeyboardInterrupt

        # v0.16.0: run_watch_loop lives in handlers.py (cli re-exports it)
        with mock.patch.object(handlers.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            cli.run_watch_loop(push, 10, "test HUD")
        self.assertEqual(len(calls), 1)


class TestM12ProgressGating(unittest.TestCase):
    class _Dev:
        def align_data_pipe(self):
            return (0, True)

        def send_feature(self, data):
            pass

        def handshake(self, post_delay=0):
            pass

        def drain_data_acks(self):
            pass

        def send_data_chunk(self, chunk, timeout_ms=0):
            return b"\x01\x5a\x02" + b"\x00" * 61

    def _upload_capture(self, isatty):
        buf = io.StringIO()
        # v0.13.0: upload_frames_to_screen now persists the §3.5 pipe flag —
        # isolate the state file so the suite never touches ~/.cache.
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, {"AK820_STATE_PATH": os.path.join(tmp, "state.json")}), \
             mock.patch.object(state, "save_payload_hash"), \
             contextlib.redirect_stdout(buf), \
             mock.patch.object(sys.stdout, "isatty", return_value=isatty):
            actions.upload_frames_to_screen(self._Dev(), [b"\xAB" * 100], None, label="t", force=True)
        return buf.getvalue()

    def test_no_carriage_returns_when_not_a_tty(self):
        out = self._upload_capture(isatty=False)
        self.assertNotIn("\r", out)  # m12: journald never sees \r progress spam
        self.assertIn("Upload and flash save complete!", out)

    def test_progress_streamed_on_a_tty(self):
        out = self._upload_capture(isatty=True)
        self.assertIn("Progress:", out)


class TestModuleBudget(unittest.TestCase):
    """§10 DoD: every module in ak820/ stays strictly under 300 lines."""

    def test_all_package_modules_under_300_lines(self):
        for py in sorted((ROOT / "ak820").glob("*.py")):
            n = len(py.read_text().splitlines())
            self.assertLess(n, 300, f"{py.name} is {n} lines")


class TestShimV09(unittest.TestCase):
    """ak820_ctrl must keep every v0.8.0 name AND the new v0.9.0 entry points."""

    NAMES = [
        # v0.7.0/v0.8.0 surface (regression guard)
        "VID", "PIDS", "MAX_FRAMES", "LIGHT_MODES", "parse_mode", "build_payload",
        "UploadIncompleteError", "AK820Pro", "stream_payload", "AK820ProDevice",
        "find_hidraw_devices", "find_keyboard_hidraw", "image_to_rgb565",
        "upload_frames_to_screen", "build_radar_frames", "wait_for_keypress",
        "set_backlight", "set_status_backlight", "SHOWCASE_PRESETS", "main", "state",
        # v0.9.0 additions
        "build_parser", "run_watch_loop", "dispatch_backlight", "DeviceNotFoundError",
    ]

    def test_shim_exposes_every_name(self):
        for name in self.NAMES:
            self.assertTrue(hasattr(ak820_ctrl, name), f"ak820_ctrl.{name} missing")

    def test_shim_names_are_the_same_objects(self):
        self.assertIs(ak820_ctrl.main, cli.main)
        self.assertIs(ak820_ctrl.build_parser, cli.build_parser)
        self.assertIs(ak820_ctrl.upload_frames_to_screen, actions.upload_frames_to_screen)
        self.assertIs(ak820_ctrl.SHOWCASE_PRESETS, backlight.SHOWCASE_PRESETS)
        self.assertIs(ak820_ctrl.set_backlight, backlight.set_backlight)
        self.assertIs(ak820_ctrl.wait_for_keypress, transport.wait_for_keypress)
        self.assertIs(ak820_ctrl.AK820ProDevice, transport.AK820ProDevice)

    def test_os_and_select_patch_points_survive(self):
        # test_hidraw.py patches os.open / select.select *through* ak820_ctrl;
        # the shim must keep those module attributes and they must be the
        # same singletons the transport code actually calls.
        import select as select_mod

        self.assertIs(ak820_ctrl.os, os)
        self.assertIs(ak820_ctrl.select, select_mod)


if __name__ == "__main__":
    unittest.main()
