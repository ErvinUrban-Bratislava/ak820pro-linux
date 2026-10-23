#!/usr/bin/env python3
"""v0.12.0 comprehensive re-audit regressions — zero hardware attached.

Every test here pins a defect the v0.12.0 audit found and closed:
  * D8: exit-code contract reconciled with §3.4 — PermissionDeniedError -> 4
    (was 3, indistinguishable from DeviceNotFoundError), ProtocolError -> 7
    (was squatting on §3.4's permission code 4).
  * D9: §3.6 dead-control-pipe detection — handshake() now RESETS the counter
    on success and raises DeviceDisconnectedError after 2 consecutive failures
    (v0.11.0 shipped the counter but never acted on it).
  * m5: import-time DEFAULT_EVENTS_PATH deleted (froze $AK820_EVENTS_PATH).
  * m6: save_events_config is atomic (mkstemp + os.replace) and tolerates a
    bare relative filename.
  * m7: corrupt events JSON raises ConfigError (exit 6), not JSONDecodeError.
  * m10: `backlight set --color banana` fails with a friendly exit, no traceback.
  * m11: protocol.PIDS and the shipped udev rule cover exactly the same PIDs.
"""

import io
import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import milestone_radar as mr
from ak820 import cli
from ak820.backlight import dispatch_backlight
from ak820.device import AK820Pro
from ak820.errors import DeviceDisconnectedError, PermissionDeniedError, ProtocolError
from ak820.protocol import PIDS, VID
from fake_transport import FakeTransport


class TestExitCodeContractD8(unittest.TestCase):
    """§3.4: permission denied is 4, protocol desync is 7, device missing is 3."""

    def test_permission_denied_is_4(self):
        self.assertEqual(PermissionDeniedError.exit_code, 4)

    def test_protocol_error_is_7(self):
        self.assertEqual(ProtocolError.exit_code, 7)

    def test_codes_are_distinct(self):
        codes = {cls.exit_code for cls in (PermissionDeniedError, ProtocolError)}
        self.assertEqual(len(codes), 2, "D8 regression: codes collapsed onto each other")
        self.assertNotIn(3, codes, "D8 regression: 3 belongs to DeviceNotFoundError")

    def test_main_maps_permission_denied_to_4(self):
        with mock.patch.object(cli, "_dispatch", side_effect=PermissionDeniedError("nope")), \
             redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["probe"])
        self.assertEqual(ctx.exception.code, 4)

    def test_main_maps_protocol_error_to_7(self):
        with mock.patch.object(cli, "_dispatch", side_effect=ProtocolError("desync")), \
             redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["probe"])
        self.assertEqual(ctx.exception.code, 7)


class TestDeadControlPipeD9(unittest.TestCase):
    """§3.6: two consecutive failed handshakes = dead control pipe -> exit 75."""

    def test_single_failure_tolerated_and_counted(self):
        dev = AK820Pro(FakeTransport(ctrl_dead=True))
        self.assertIsNone(dev.handshake())
        self.assertEqual(dev.handshake_failures, 1)

    def test_two_consecutive_failures_raise_disconnected(self):
        dev = AK820Pro(FakeTransport(ctrl_dead=True))
        dev.handshake()
        with self.assertRaises(DeviceDisconnectedError):
            dev.handshake()

    def test_success_resets_counter(self):
        dev = AK820Pro(FakeTransport())
        dev.handshake_failures = 1  # a blip already happened
        self.assertIsNotNone(dev.handshake())
        self.assertEqual(dev.handshake_failures, 0)

    def test_align_aborts_on_dead_control_pipe(self):
        dev = AK820Pro(FakeTransport(ctrl_dead=True))
        with self.assertRaises(DeviceDisconnectedError):
            dev.align_data_pipe()


class TestMinorDefectClosures(unittest.TestCase):
    """m5/m6/m7/m10/m11 — the last open rows of the REVIEW.md minor table."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_m5_import_time_default_path_deleted(self):
        self.assertFalse(
            hasattr(mr, "DEFAULT_EVENTS_PATH"),
            "m5 regression: import-time DEFAULT_EVENTS_PATH froze $AK820_EVENTS_PATH",
        )

    def test_m6_atomic_save_with_bare_relative_filename(self):
        cfg = {"events": [{"title": "T", "short": "T", "date": "05-01"}]}
        cwd = os.getcwd()
        try:
            os.chdir(self.tmp)
            mr.save_events_config(cfg, "events.json")  # dirname == "" pre-fix
        finally:
            os.chdir(cwd)
        saved = os.path.join(self.tmp, "events.json")
        with open(saved, encoding="utf-8") as f:
            self.assertEqual(json.load(f), cfg)
        leftovers = [p for p in os.listdir(self.tmp) if p.endswith(".tmp")]
        self.assertEqual(leftovers, [], "m6: temp file leaked next to the config")

    def test_m7_corrupt_json_raises_config_error(self):
        bad = os.path.join(self.tmp, "corrupt.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("{ not json")
        with self.assertRaises(mr.ConfigError) as ctx:
            mr.load_events_config(bad)
        self.assertIn("corrupt.json", str(ctx.exception))

    def test_m10_bad_color_friendly_exit(self):
        dev = AK820Pro(FakeTransport())
        args = cli.build_parser().parse_args(["backlight", "set", "--color", "banana"])
        # m16 (v0.21.0): bad flag value -> documented usage-error exit 2, message on stderr.
        err = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, redirect_stderr(err):
            dispatch_backlight(dev, args)
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("Invalid hex color", err.getvalue())

    def test_m11_pids_match_udev_rule(self):
        rules = (ROOT / "udev" / "70-ajazz-ak820pro.rules").read_text(encoding="utf-8")
        rule_pids = {int(p, 16) for p in re.findall(r'idProduct\}=="([0-9a-fA-F]{4})"', rules)}
        self.assertEqual(set(PIDS), rule_pids,
                         "m11 regression: discovery PIDs and udev coverage diverged")
        self.assertNotIn(0xFEFE, PIDS, "m11: test-fixture PID must not ship in discovery")
        self.assertEqual(VID, 0x0C45)


if __name__ == "__main__":
    unittest.main()
