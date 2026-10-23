#!/usr/bin/env python3
"""v0.21.0 comprehensive re-audit regressions (REVIEW.md §20) — zero hardware.

Every test here pins a defect the v0.21.0 audit found and closed:
  * D17: `run <missing-command>` leaked a raw FileNotFoundError traceback
    (exit 1) — now FileMissingError (8); a non-executable command becomes
    InvalidFileError (9), never 4 (4 stays "device node rejected us").
  * D18: Ctrl+C during upload / keypress wait / `run` child dumped a raw
    traceback — main() now exits 130 (128+SIGINT) with a friendly line.
    The --watch loop keeps its local clean-stop behavior (exit 0).
  * D19: the §3.4 exit-5 full-retry path discarded the align result and
    streamed into a provably un-aligned pipe (m4 invariant bypass) — both
    call sites now share `_ensure_aligned`, which aborts with ProtocolError (7).
  * m14: a `run` child killed by signal N exited (-N) & 0xFF (247 for SIGKILL)
    — now the shell convention 128+N (137).
  * m15: `run` with no operand exits documented usage code 2, not 1.
  * m16: `backlight set --mode/--color <bad>` exits documented usage code 2.
"""

import contextlib
import inspect
import io
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fake_transport import FakeTransport
from ak820 import actions, cli, handlers
from ak820.device import AK820Pro
from ak820.errors import (
    FileMissingError,
    InvalidFileError,
    ProtocolError,
    UploadIncompleteError,
)

FRAME = b"\x00" * (128 * 128 * 2)  # one valid 128x128 RGB565 frame


def _run_args(*argv):
    return cli.build_parser().parse_args(["run", *argv])


class RunOperandContractD17(unittest.TestCase):
    """D17/m14/m15: `run` operand + child-exit contract."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_state = os.environ.get("AK820_STATE_PATH")
        os.environ["AK820_STATE_PATH"] = os.path.join(self._tmp.name, "state.json")
        self.dev = AK820Pro(FakeTransport())

    def tearDown(self):
        if self._old_state is None:
            os.environ.pop("AK820_STATE_PATH", None)
        else:
            os.environ["AK820_STATE_PATH"] = self._old_state
        self._tmp.cleanup()

    def test_missing_binary_raises_file_missing_8(self):
        args = _run_args("--no-wait", "--", "definitely-not-a-real-binary-v021")
        with self.assertRaises(FileMissingError) as ctx:
            handlers.cmd_run(args, self.dev)
        self.assertEqual(ctx.exception.exit_code, 8)
        self.assertIn("definitely-not-a-real-binary-v021", str(ctx.exception))

    def test_missing_binary_cli_end_to_end_exits_8(self):
        with mock.patch.object(cli, "find_hidraw_devices", return_value=("/dev/x", "/dev/y")), \
             mock.patch.object(cli, "AK820ProDevice") as dev_cls:
            dev_cls.return_value.__enter__.return_value = AK820Pro(FakeTransport())
            with self.assertRaises(SystemExit) as ctx, redirect_stdout(io.StringIO()):
                cli.main(["run", "--no-wait", "--", "definitely-not-a-real-binary-v021"])
        self.assertEqual(ctx.exception.code, 8)

    def test_non_executable_raises_invalid_file_9_not_4(self):
        # 9 = present-but-unusable (chmod +x fixes it). 4 must stay reserved
        # for "the device node rejected us" (udev rule) so scripts keep that
        # distinction on `$?` alone.
        args = _run_args("--no-wait", "--", "./script-without-x-bit")
        with mock.patch("ak820.handlers.subprocess.run", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaises(InvalidFileError) as ctx:
                handlers.cmd_run(args, self.dev)
        self.assertEqual(ctx.exception.exit_code, 9)
        self.assertNotEqual(ctx.exception.exit_code, 4)

    def test_child_success_is_transparent(self):
        args = _run_args("--no-wait", "--", "true")
        with mock.patch("ak820.handlers.subprocess.run", return_value=mock.Mock(returncode=0)), \
             redirect_stdout(io.StringIO()):
            handlers.cmd_run(args, self.dev)  # must not raise

    def test_child_signal_maps_to_128_plus_signum(self):
        # m14: SIGKILL used to exit 247 ((-9) & 0xFF); convention is 137.
        args = _run_args("--no-wait", "--", "sleepy")
        with mock.patch("ak820.handlers.subprocess.run", return_value=mock.Mock(returncode=-9)), \
             redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                handlers.cmd_run(args, self.dev)
        self.assertEqual(ctx.exception.code, 137)

    def test_child_normal_exit_code_passthrough(self):
        args = _run_args("--no-wait", "--", "false")
        with mock.patch("ak820.handlers.subprocess.run", return_value=mock.Mock(returncode=3)), \
             redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                handlers.cmd_run(args, self.dev)
        self.assertEqual(ctx.exception.code, 3)

    def test_empty_operand_is_usage_error_2(self):
        # m15: documented usage-error code 2, not the undocumented generic 1.
        args = _run_args()
        self.assertEqual(args.cmd, [])
        err = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, redirect_stderr(err):
            handlers.cmd_run(args, self.dev)
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("No command specified", err.getvalue())


class KeyboardInterruptContractD18(unittest.TestCase):
    """D18: Ctrl+C anywhere under main() exits 130 with a friendly line."""

    def test_main_maps_keyboard_interrupt_to_130(self):
        err = io.StringIO()
        with mock.patch.object(cli, "_dispatch", side_effect=KeyboardInterrupt), \
             redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["probe"])
        self.assertEqual(ctx.exception.code, 130)
        self.assertIn("Interrupted", err.getvalue())

    def test_watch_loop_still_stops_cleanly(self):
        # Regression: --watch keeps its local Ctrl+C stop (exit 0 semantics);
        # the main-level catch is the safety net for everything else.
        calls = []

        def push():
            calls.append(1)
            raise KeyboardInterrupt

        with mock.patch.object(handlers.time, "sleep"), redirect_stdout(io.StringIO()):
            cli.run_watch_loop(push, 1, "x")  # must not raise
        self.assertEqual(len(calls), 1)

    def test_readme_documents_130(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^\|\s*`130`", "README Exit Codes table missing 130 row")


class RetryAlignGuardD19(unittest.TestCase):
    """D19: the exit-5 full retry may never stream into an un-aligned pipe."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_state = os.environ.get("AK820_STATE_PATH")
        os.environ["AK820_STATE_PATH"] = os.path.join(self._tmp.name, "state.json")
        self.dev = AK820Pro(FakeTransport())

    def tearDown(self):
        if self._old_state is None:
            os.environ.pop("AK820_STATE_PATH", None)
        else:
            os.environ["AK820_STATE_PATH"] = self._old_state
        self._tmp.cleanup()

    def test_first_attempt_align_failure_aborts_before_stream(self):
        align_calls, stream_calls = [], []
        self.dev.align_data_pipe = lambda: (align_calls.append(1) or ((0, 0), False))
        with mock.patch.object(actions, "stream_payload", side_effect=lambda *a, **k: stream_calls.append(1)):
            with self.assertRaises(ProtocolError):
                actions.upload_frames_to_screen(self.dev, [FRAME], label="t", force=True)
        self.assertEqual(len(align_calls), 2)   # m4: one retry, then abort
        self.assertEqual(len(stream_calls), 0)  # never streamed into the pipe

    def test_retry_path_aborts_when_realignment_fails(self):
        # First attempt aligns fine and streams (dropped ACK -> exit-5 retry);
        # the retry's realignment fails -> ProtocolError (7), no second stream.
        align_calls, stream_calls = [], []

        def fake_align():
            align_calls.append(1)
            if len(align_calls) == 1:  # main path aligns on the first attempt
                return ((8, 8), True)
            return ((0, 0), False)     # retry path: pipe will not align

        def fake_stream(*a, **k):
            stream_calls.append(1)
            raise UploadIncompleteError("dropped ACK")

        self.dev.align_data_pipe = fake_align
        with mock.patch.object(actions, "stream_payload", side_effect=fake_stream):
            with self.assertRaises(ProtocolError):
                actions.upload_frames_to_screen(self.dev, [FRAME], label="t", force=True)
        self.assertEqual(len(stream_calls), 1)  # retry never streamed un-aligned
        self.assertEqual(len(align_calls), 3)   # 1 main + 2 retry (m4 twice)

    def test_happy_retry_still_succeeds_end_to_end(self):
        # Regression: the transient-drop retry (FakeTransport drop_once) still
        # completes through the shared _ensure_aligned helper.
        dev = AK820Pro(FakeTransport(drop_once=True))
        with redirect_stdout(io.StringIO()):
            uploaded = actions.upload_frames_to_screen(dev, [FRAME], label="t", force=True)
        self.assertTrue(uploaded)
        self.assertTrue(state_pipe_clean())

    def test_align_calls_centralized_in_helper(self):
        # Structural invariant: no call site may bypass _ensure_aligned and
        # touch dev.align_data_pipe directly.
        full = (ROOT / "ak820" / "actions.py").read_text(encoding="utf-8")
        helper = inspect.getsource(actions._ensure_aligned)
        self.assertEqual(full.count("dev.align_data_pipe("), 2)
        self.assertEqual(helper.count("dev.align_data_pipe("), 2)


def state_pipe_clean():
    from ak820 import state
    return state.pipe_known_clean()


class BacklightUsageExit2M16(unittest.TestCase):
    """m16: bad flag values are usage errors -> documented exit 2."""

    def setUp(self):
        self.dev = AK820Pro(FakeTransport())

    def test_bad_mode_exits_2(self):
        args = cli.build_parser().parse_args(["backlight", "set", "--mode", "banana"])
        err = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, redirect_stderr(err):
            handlers.cmd_backlight(args, self.dev)
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("Unknown lighting mode", err.getvalue())

    def test_bad_color_exits_2(self):
        args = cli.build_parser().parse_args(["backlight", "set", "--color", "banana"])
        err = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, redirect_stderr(err):
            handlers.cmd_backlight(args, self.dev)
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("Invalid hex color", err.getvalue())


if __name__ == "__main__":
    unittest.main()
