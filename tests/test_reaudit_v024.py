#!/usr/bin/env python3
"""v0.24.0 comprehensive re-audit regression suite (REVIEW.md §21, zero hardware).

D20: a *successful* upload exited 1 — upload_frames_to_screen returns True,
     `main()` returned it verbatim, and the console-script wrapper's
     sys.exit(True) -> exit 1 (bool is an int subclass). §3.4: 0 is the ONLY
     success status; both True (uploaded) and False (dedup skip) map to 0.
D21: bare `ak820 backlight` dumped an AttributeError traceback (the "set"
     fallback reads attributes only a parsed sub-action defines) -> argparse
     now requires the action: friendly usage error, exit 2.
D22: events config shape violations (top level not an object, `events` not a
     list, entry not an object, directory/unreadable config, unwritable write
     target) leaked raw AttributeError/OSError tracebacks -> ConfigError (6).
D23: `--save-preview` write failures (unwritable parent/target, unknown file
     extension) leaked raw makedirs/PIL tracebacks -> InvalidFileError (9).
D24: `--seconds/--watch/--dwell nan|inf` (accepted by type=float) crashed with
     ValueError/OverflowError or slept forever -> usage error exit 2.
m17: wait_for_keypress stdin fallback crashed on a closed stdin -> False.
m19: a hand-edited idle_color crashed the restore-idle step -> ConfigError (6).
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

from PIL import Image

sys.path.insert(0, str(ROOT / "tests"))
from fake_transport import FakeTransport

from ak820 import backlight, cli, transport
from ak820.device import AK820Pro

import milestone_radar as mr


def _run_main(argv):
    """cli.main(argv) capturing stdout+stderr; returns (rc, out, err)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


class TestD20SuccessExitZero(unittest.TestCase):
    """§3.4: a successful upload exits 0 — never 1, never a bool."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.png = os.path.join(self.tmp, "pic.png")
        Image.new("RGB", (64, 64), (10, 20, 30)).save(self.png)
        self.state = os.path.join(self.tmp, "state.json")

    def _main_upload(self):
        pro = AK820Pro(FakeTransport())
        with mock.patch.dict(os.environ, {"AK820_STATE_PATH": self.state}), mock.patch.object(
            cli, "find_hidraw_devices", return_value=("/dev/tft", "/dev/ctrl")
        ), mock.patch.object(cli, "AK820ProDevice") as dev_cls, contextlib.redirect_stdout(
            io.StringIO()
        ):
            dev_cls.return_value.__enter__.return_value = pro
            return cli.main(["upload", self.png])

    def test_successful_upload_returns_int_zero(self):
        rc = self._main_upload()
        self.assertEqual(rc, 0)
        # The exact D20 trap: True == 1, so equality alone is not enough —
        # a bool return would make the console script sys.exit(True) -> exit 1.
        self.assertNotIsInstance(rc, bool)

    def test_console_script_wrapper_exits_zero(self):
        # End-to-end §3.4 lock: emulate the pipx wrapper `sys.exit(main())`.
        rc = self._main_upload()
        with self.assertRaises(SystemExit) as ctx:
            sys.exit(rc)
        self.assertEqual(ctx.exception.code, 0)

    def test_dedup_skip_still_exits_zero(self):
        self.assertEqual(self._main_upload(), 0)  # first run uploads
        rc = self._main_upload()  # second run: identical payload -> skip
        self.assertEqual(rc, 0)
        self.assertNotIsInstance(rc, bool)


class TestD21BareBacklight(unittest.TestCase):
    """Bare `backlight` is a usage error (exit 2), never an AttributeError."""

    def test_bare_backlight_exits_2(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.main(["backlight"])
        self.assertEqual(ctx.exception.code, 2)

    def test_bare_backlight_message_names_the_missing_action(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit):
                cli.main(["backlight"])
        self.assertIn("bl_action", err.getvalue())

    def test_backlight_subactions_still_parse(self):
        args = cli.build_parser().parse_args(["backlight", "set", "--mode", "static"])
        self.assertEqual(args.bl_action, "set")
        args = cli.build_parser().parse_args(["backlight", "list"])
        self.assertEqual(args.bl_action, "list")

    def test_backlight_list_runs_unchanged(self):
        with mock.patch.object(
            cli, "find_hidraw_devices", return_value=(None, "/dev/ctrl")
        ), mock.patch.object(cli, "AK820ProDevice") as dev_cls, contextlib.redirect_stdout(
            io.StringIO()
        ) as out:
            dev_cls.return_value.__enter__.return_value = mock.MagicMock()
            rc = cli.main(["backlight", "list"])
        self.assertEqual(rc or 0, 0)
        self.assertIn("warm-white", out.getvalue())


class TestD22ConfigShape(unittest.TestCase):
    """Every events-config shape/read/write failure is ConfigError -> exit 6."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _cfg_with(self, content):
        path = os.path.join(self.tmp, "events.json")
        with open(path, "w") as f:
            f.write(content)
        return path

    def _expect_6(self, argv):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(argv)
        self.assertEqual(ctx.exception.code, 6)

    def test_top_level_list_exit_6(self):
        cfg = self._cfg_with("[1, 2, 3]")
        self._expect_6(["events", "--config", cfg, "list"])

    def test_top_level_string_exit_6(self):
        cfg = self._cfg_with('"just a string"')
        self._expect_6(["events", "--config", cfg, "list"])

    def test_events_not_a_list_exit_6(self):
        cfg = self._cfg_with('{"events": "nope"}')
        self._expect_6(["events", "--config", cfg, "list"])

    def test_event_entry_not_an_object_exit_6(self):
        cfg = self._cfg_with('{"events": ["notadict"]}')
        self._expect_6(["events", "--config", cfg, "list"])

    def test_directory_as_config_exit_6(self):
        self._expect_6(["events", "--config", self.tmp, "list"])

    @unittest.skipIf(os.geteuid() == 0, "chmod-based unreadability is root-proof")
    def test_unreadable_config_exit_6(self):
        cfg = self._cfg_with('{"events": []}')
        os.chmod(cfg, 0o000)
        try:
            self._expect_6(["events", "--config", cfg, "list"])
        finally:
            os.chmod(cfg, 0o644)

    @unittest.skipIf(os.geteuid() == 0, "chmod-based unwritability is root-proof")
    def test_unwritable_write_target_exit_6(self):
        ro_dir = os.path.join(self.tmp, "ro")
        os.makedirs(ro_dir)
        os.chmod(ro_dir, 0o555)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as ctx:
                    cli.main(
                        [
                            "events",
                            "--config",
                            os.path.join(ro_dir, "events.json"),
                            "add",
                            "--title",
                            "T",
                            "--short",
                            "S",
                            "--date",
                            "12-25",
                        ]
                    )
            self.assertEqual(ctx.exception.code, 6)
        finally:
            os.chmod(ro_dir, 0o755)

    def test_malformed_json_still_exit_6(self):
        cfg = self._cfg_with("{not json")
        self._expect_6(["events", "--config", cfg, "list"])

    def test_valid_config_still_loads(self):
        cfg = self._cfg_with(
            json.dumps({"events": [{"id": "x", "title": "X", "date": "12-25", "category": "birthday"}]})
        )
        rc, out, _ = _run_main(["events", "--config", cfg, "list"])
        self.assertEqual(rc, 0)
        self.assertIn("X", out)


class TestD23PreviewWriteErrors(unittest.TestCase):
    """Unusable --save-preview targets exit 9, never a raw traceback."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _expect_9(self, argv):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(argv)
        self.assertEqual(ctx.exception.code, 9)

    @unittest.skipIf(os.geteuid() == 0, "chmod-based unwritability is root-proof")
    def test_hud_preview_unwritable_parent_exit_9(self):
        ro = os.path.join(self.tmp, "ro")
        os.makedirs(ro)
        os.chmod(ro, 0o555)
        try:
            self._expect_9(["hud", "--save-preview", os.path.join(ro, "sub", "hud.png")])
        finally:
            os.chmod(ro, 0o755)

    @unittest.skipIf(os.geteuid() == 0, "chmod-based unwritability is root-proof")
    def test_radar_preview_unwritable_dir_exit_9(self):
        ro = os.path.join(self.tmp, "ro2")
        os.makedirs(ro)
        os.chmod(ro, 0o555)
        try:
            self._expect_9(["radar", "--save-preview", ro])
        finally:
            os.chmod(ro, 0o755)

    def test_preview_unknown_extension_exit_9(self):
        target = os.path.join(self.tmp, "card.xyz")  # PIL: unknown file extension
        self._expect_9(["hud", "--save-preview", target])

    def test_radar_preview_existing_file_still_exit_9(self):
        blocker = os.path.join(self.tmp, "blocker")
        with open(blocker, "w") as f:
            f.write("x")
        self._expect_9(["radar", "--save-preview", blocker])

    def test_hud_preview_happy_path_still_zero(self):
        target = os.path.join(self.tmp, "hud.png")
        rc, out, _ = _run_main(["hud", "--save-preview", target])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(target))


class TestD24NonFiniteNumericFlags(unittest.TestCase):
    """nan/inf numeric flags are usage errors (exit 2), not tracebacks."""

    def _expect_2(self, argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(argv)
        self.assertEqual(ctx.exception.code, 2)
        return err.getvalue()

    def test_radar_seconds_nan_exit_2(self):
        msg = self._expect_2(["--dry-run", "radar", "--seconds", "nan"])
        self.assertIn("--seconds", msg)

    def test_radar_seconds_inf_exit_2(self):
        self._expect_2(["--dry-run", "radar", "--seconds", "inf"])

    def test_hud_watch_nan_exit_2(self):
        self._expect_2(["hud", "--watch", "nan"])

    def test_media_watch_neg_inf_exit_2(self):
        self._expect_2(["media", "--watch", "-inf"])

    def test_demo_dwell_inf_exit_2(self):
        self._expect_2(["backlight", "demo", "--dwell", "inf"])

    def test_finite_values_still_parse(self):
        args = cli.build_parser().parse_args(["radar", "--seconds", "3.5"])
        self.assertEqual(args.seconds, 3.5)
        args = cli.build_parser().parse_args(["hud", "--watch", "15"])
        self.assertEqual(args.watch, 15.0)
        args = cli.build_parser().parse_args(["backlight", "demo", "--dwell", "0"])
        self.assertEqual(args.dwell, 0.0)


class TestM17ClosedStdin(unittest.TestCase):
    """wait_for_keypress with an unusable stdin answers False, no traceback."""

    class _BadStdin:
        """Stdin stand-in whose fileno() raises — the closed-file-object model."""

        def __init__(self, exc):
            self._exc = exc

        def fileno(self):
            raise self._exc

    class _FdStdin:
        """Stdin stand-in exposing a raw fd — select() itself raises on a dead fd."""

        def __init__(self, fd):
            self._fd = fd

        def fileno(self):
            return self._fd

    def test_closed_file_object_stdin(self):
        with mock.patch.object(transport, "find_keyboard_hidraw", return_value=[]), mock.patch(
            "sys.stdin", self._BadStdin(ValueError("I/O operation on closed file."))
        ):
            self.assertFalse(transport.wait_for_keypress(timeout=0.01))

    def test_closed_fd_stdin(self):
        r_fd, w_fd = os.pipe()
        os.close(r_fd)
        os.close(w_fd)  # both ends gone -> select() on r_fd raises EBADF
        with mock.patch.object(transport, "find_keyboard_hidraw", return_value=[]), mock.patch(
            "sys.stdin", self._FdStdin(r_fd)
        ):
            self.assertFalse(transport.wait_for_keypress(timeout=0.01))


class TestM19IdleColorConfigError(unittest.TestCase):
    """A hand-edited idle_color fails friendly (ConfigError -> 6), not a crash."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = os.path.join(self.tmp, "events.json")
        with open(self.cfg, "w") as f:
            json.dump({"events": [], "backlight": {"idle_preset": "custom", "idle_color": "#zzz"}}, f)

    def test_apply_idle_backlight_raises_config_error(self):
        with mock.patch.dict(os.environ, {"AK820_EVENTS_PATH": self.cfg}):
            with self.assertRaises(mr.ConfigError):
                backlight.apply_idle_backlight(mock.MagicMock())

    def test_status_idle_via_main_exits_6(self):
        with mock.patch.dict(os.environ, {"AK820_EVENTS_PATH": self.cfg}), mock.patch.object(
            cli, "find_hidraw_devices", return_value=(None, "/dev/ctrl")
        ), mock.patch.object(cli, "AK820ProDevice") as dev_cls, contextlib.redirect_stderr(
            io.StringIO()
        ):
            dev_cls.return_value.__enter__.return_value = mock.MagicMock()
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["status", "idle"])
        self.assertEqual(ctx.exception.code, 6)


if __name__ == "__main__":
    unittest.main()
