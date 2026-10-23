#!/usr/bin/env python3
"""v0.20.0 disconnect recovery & open-path robustness (REVIEW.md §19).

Zero hardware: every test drives either a mocked `os.open` at the transport
seam or the FakeTransport firmware simulator.

Covered defects:
  * D15 — `AK820ProDevice.__enter__` leaked raw OSError tracebacks (exit 1)
    when a node vanished between sysfs discovery and open (ENOENT/ENXIO/ENODEV)
    or the udev rule was missing (EACCES/EPERM). Now typed: 75 / 4.
  * D16 — `handshake()` swallowed PermissionDeniedError as a transient blip
    and misattributed the second strike as a dead pipe (75). Permission
    errors now propagate immediately (exit 4 — the fix is the udev rule).
"""

import errno
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ak820 import cli, state, transport
from ak820.actions import upload_frames_to_screen
from ak820.device import AK820Pro
from ak820.errors import (
    DeviceDisconnectedError,
    PermissionDeniedError,
)
from ak820.transport import AK820ProDevice, LinuxHidrawTransport
from fake_transport import FakeTransport

FRAMES = [b"\x00" * 32768]  # + 256B header -> 9 chunks


class TestErrnoTranslationV020(unittest.TestCase):
    """ENOENT/ENXIO join ENODEV as mid-op disconnects (§19)."""

    def test_enoent_maps_to_disconnect(self):
        exc = LinuxHidrawTransport._translate(OSError(errno.ENOENT, "gone"), "open")
        self.assertIsInstance(exc, DeviceDisconnectedError)
        self.assertEqual(exc.exit_code, 75)

    def test_enxio_maps_to_disconnect(self):
        exc = LinuxHidrawTransport._translate(OSError(errno.ENXIO, "detached"), "open")
        self.assertIsInstance(exc, DeviceDisconnectedError)

    def test_eio_still_passes_through(self):
        raw = OSError(errno.EIO, "io error")
        self.assertIs(LinuxHidrawTransport._translate(raw, "x"), raw)


class TestOpenPathTranslation(unittest.TestCase):
    """D15: open failures at the fd seam become typed exit codes, never tracebacks."""

    def _enter(self, open_exc):
        dev = AK820ProDevice("/dev/fake-tft", "/dev/fake-ctrl")
        with mock.patch.object(transport.os, "open", side_effect=open_exc):
            with self.assertRaises((DeviceDisconnectedError, PermissionDeniedError)) as cm:
                dev.__enter__()
        return cm.exception

    def test_open_eacces_maps_to_permission_denied(self):
        exc = self._enter(OSError(errno.EACCES, "denied"))
        self.assertEqual(exc.exit_code, 4)

    def test_open_eperm_maps_to_permission_denied(self):
        exc = self._enter(OSError(errno.EPERM, "not permitted"))
        self.assertEqual(exc.exit_code, 4)

    def test_open_enoent_maps_to_disconnect(self):
        exc = self._enter(OSError(errno.ENOENT, "node vanished after discovery"))
        self.assertEqual(exc.exit_code, 75)

    def test_open_enxio_maps_to_disconnect(self):
        exc = self._enter(OSError(errno.ENXIO, "driver detached"))
        self.assertEqual(exc.exit_code, 75)

    def test_open_enodev_maps_to_disconnect(self):
        exc = self._enter(OSError(errno.ENODEV, "gone"))
        self.assertEqual(exc.exit_code, 75)

    def test_second_open_failure_closes_first_fd(self):
        """m1 leak fix survives the v0.20.0 translation restructure."""
        dev = AK820ProDevice("/dev/fake-tft", "/dev/fake-ctrl")
        closes = []
        with mock.patch.object(transport.os, "open",
                               side_effect=[7, OSError(errno.ENOENT, "gone")]), \
                mock.patch.object(transport.os, "close", side_effect=closes.append):
            with self.assertRaises(DeviceDisconnectedError):
                dev.__enter__()
        self.assertEqual(closes, [7])  # the ctrl fd that did open is closed

    def test_successful_open_wires_transport_and_exit_closes_once(self):
        closes = []
        with mock.patch.object(transport.os, "open", side_effect=[7, 8]), \
                mock.patch.object(transport.os, "close", side_effect=closes.append):
            with AK820ProDevice("/dev/fake-tft", "/dev/fake-ctrl") as dev:
                self.assertIsNotNone(dev.pro)
                self.assertEqual(dev.pro.transport.ctrl_fd, 7)
                self.assertEqual(dev.pro.transport.data_fd, 8)
        self.assertEqual(sorted(closes), [7, 8])  # each fd closed exactly once


class TestHandshakePermissionFastFail(unittest.TestCase):
    """D16: a rejected control pipe fails fast with exit 4, never miscounted."""

    def test_perm_denied_propagates_immediately(self):
        dev = AK820Pro(FakeTransport(perm_denied=True))
        with self.assertRaises(PermissionDeniedError):
            dev.handshake()
        self.assertEqual(dev.handshake_failures, 0)  # not counted as a blip

    def test_perm_denied_not_misattributed_after_a_blip(self):
        dev = AK820Pro(FakeTransport(perm_denied=True))
        dev.handshake_failures = 1  # a genuine transient blip came first
        with self.assertRaises(PermissionDeniedError):
            dev.handshake()  # still exit 4 — not the dead-pipe 75

    def test_ctrl_dead_two_strike_model_unchanged(self):
        dev = AK820Pro(FakeTransport(ctrl_dead=True))
        self.assertIsNone(dev.handshake())
        self.assertEqual(dev.handshake_failures, 1)
        with self.assertRaises(DeviceDisconnectedError):
            dev.handshake()

    def test_perm_denied_fault_is_typed_on_both_control_calls(self):
        fake = FakeTransport(perm_denied=True)
        with self.assertRaises(PermissionDeniedError):
            fake.get_feature()
        with self.assertRaises(PermissionDeniedError):
            fake.set_feature(b"\x00" * 64)


class StateIsolatedTestCase(unittest.TestCase):
    def setUp(self):
        self._state_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._state_dir.cleanup)
        self.state_path = os.path.join(self._state_dir.name, "state.json")
        env = mock.patch.dict(os.environ, {"AK820_STATE_PATH": self.state_path})
        env.start()
        self.addCleanup(env.stop)

    def _recorded_digest(self):
        if not os.path.exists(self.state_path):
            return None
        with open(self.state_path, encoding="utf-8") as f:
            return json.load(f).get("sha256")


class TestMidOpDisconnect(StateIsolatedTestCase):
    """disconnect_at=N unplug simulation through the real upload pipeline."""

    def test_disconnect_mid_stream_is_exit_75_not_traceback(self):
        dev = AK820Pro(FakeTransport(disconnect_at=1))
        with self.assertRaises(DeviceDisconnectedError) as cm:
            upload_frames_to_screen(dev, FRAMES, label="t", force=True)
        self.assertEqual(cm.exception.exit_code, 75)

    def test_disconnect_before_save_records_no_digest(self):
        dev = AK820Pro(FakeTransport(disconnect_at=1))
        with self.assertRaises(DeviceDisconnectedError):
            upload_frames_to_screen(dev, FRAMES, label="t", force=True)
        self.assertIsNone(self._recorded_digest())  # next run retries, no skip

    def test_disconnect_mid_stream_never_reaches_save(self):
        fake = FakeTransport(disconnect_at=1)
        dev = AK820Pro(fake)
        with self.assertRaises(DeviceDisconnectedError):
            dev.upload_frames(FRAMES, align=False)
        # SAVE is cmd 0x02 on the feature pipe — a dead device never got one
        saves = [f for f in fake.feature_log if f[0] == 0x04 and f[1] == 0x02]
        self.assertEqual(saves, [])

    def test_disconnect_during_align_propagates(self):
        dev = AK820Pro(FakeTransport(disconnect_at=1))
        with self.assertRaises(DeviceDisconnectedError):
            upload_frames_to_screen(dev, FRAMES, label="t", force=True)
        self.assertFalse(state.pipe_known_clean())  # next run re-aligns

    def test_disconnect_is_not_retried_like_upload_incomplete(self):
        """§3.4: exit-5 transient loss gets one full retry; a dead device
        (75) propagates immediately — retrying an unplug wastes the run."""
        dev = AK820Pro(FakeTransport())
        boom = DeviceDisconnectedError("unplugged")
        with mock.patch("ak820.actions.stream_payload", side_effect=boom) as sp:
            with self.assertRaises(DeviceDisconnectedError):
                upload_frames_to_screen(dev, FRAMES, label="t", force=True)
        self.assertEqual(sp.call_count, 1)  # no retry loop on disconnect

    def test_pipe_flag_dirty_after_disconnect(self):
        dev = AK820Pro(FakeTransport(disconnect_at=1))
        with self.assertRaises(DeviceDisconnectedError):
            upload_frames_to_screen(dev, FRAMES, label="t", force=True)
        self.assertFalse(state.pipe_known_clean())


class TestCliExitCodeEndToEnd(unittest.TestCase):
    """The open-path fix reaches the CLI contract: no traceback, exit 75/4."""

    def _run(self, open_exc):
        with mock.patch.object(cli, "find_hidraw_devices",
                               return_value=("/dev/fake-tft", "/dev/fake-ctrl")), \
                mock.patch.object(transport.os, "open", side_effect=open_exc), \
                self.assertRaises(SystemExit) as cm:
            cli.main(["upload", "anything.png"])
        return cm.exception.code

    def test_vanished_node_exits_75(self):
        self.assertEqual(self._run(OSError(errno.ENOENT, "gone")), 75)

    def test_permission_denied_exits_4(self):
        self.assertEqual(self._run(OSError(errno.EACCES, "no udev rule")), 4)


if __name__ == "__main__":
    unittest.main()
