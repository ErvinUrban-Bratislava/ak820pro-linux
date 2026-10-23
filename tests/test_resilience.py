#!/usr/bin/env python3
"""v0.11.0 reliability & robustness regressions — zero hardware attached.

Covers the §3.4/§3.5/m4 reliability contract:
  * transient ACK loss recovers via exactly one full retry (re-align + re-stream),
  * a permanent drop still aborts with UploadIncompleteError (exit 5) and no SAVE,
  * a mid-upload unplug raises DeviceDisconnectedError -> exit 75 EX_TEMPFAIL,
  * alignment failure retries once then aborts with ProtocolError (m4),
  * the real transport translates ENODEV reads into DeviceDisconnectedError
    instead of masking an unplug as an ACK timeout,
  * handshake() is observable via the failure counter (§3.6),
  * cli.main() maps a disconnect to process exit 75.
"""

import errno
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import ak820_ctrl
from ak820 import cli
from ak820.actions import upload_frames_to_screen
from ak820.device import AK820Pro, stream_payload
from ak820.errors import (
    DeviceDisconnectedError,
    PermissionDeniedError,
    ProtocolError,
    UploadIncompleteError,
)
from ak820.transport import LinuxHidrawTransport
from fake_transport import FakeTransport

FRAMES = [b"\x00" * 32768]  # + 256B header -> 9 chunks


def _cmds(fake):
    return [p[:2] for p in fake.feature_log]


def _img_cfg_count(fake):
    """IMAGE_CFG (04 72) count: align sends one per attempt + one per stream."""
    return sum(1 for p in fake.feature_log if p[0] == 0x04 and p[1] == 0x72)


class ResilienceTestCase(unittest.TestCase):
    """Isolates the dedup state file so uploads never touch ~/.cache."""

    def setUp(self):
        self._state_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._state_dir.cleanup)
        env = mock.patch.dict(
            os.environ,
            {"AK820_STATE_PATH": os.path.join(self._state_dir.name, "state.json")},
        )
        env.start()
        self.addCleanup(env.stop)


class FlakyOnceAlign(FakeTransport):
    """First align attempt goes deaf (no ACKs while its 3-chunk IMAGE_CFG is
    active); the trailing SAVE disarms the fault so the m4 retry succeeds."""

    def __init__(self):
        super().__init__()
        self.deaf = True

    def set_feature(self, report64):
        super().set_feature(report64)
        if report64[0] == 0x04 and report64[1] == 0x02:  # align's trailing SAVE
            self.deaf = False

    def read_data(self, timeout):
        if self.deaf and self.chunks_expected == 3:
            return None
        return super().read_data(timeout)


class AlwaysDeafAlign(FakeTransport):
    """The data pipe never answers an alignment probe (broken-cable model)."""

    def read_data(self, timeout):
        if self.chunks_expected == 3:
            return None
        return super().read_data(timeout)


class DeafControlTransport(FakeTransport):
    """GET_FEATURE always fails — flaky control pipe / dying cable (§3.6)."""

    def get_feature(self):
        raise OSError(errno.ENODEV, "control pipe gone")


class TestTransientAckRecovery(ResilienceTestCase):
    def test_transient_drop_recovers_on_full_retry(self):
        # One-shot lost ACK (drop_ack_at=2 lands inside the first align probe,
        # so the m4 align retry re-probes; the stream itself then runs clean).
        fake = FakeTransport(drop_ack_at=2, drop_once=True)
        dev = AK820Pro(fake)
        with redirect_stdout(io.StringIO()):
            ok = upload_frames_to_screen(dev, FRAMES, None, label="t", force=True)
        self.assertTrue(ok)
        self.assertIn(b"\x04\x02", _cmds(fake))       # SAVE reached the device
        self.assertEqual(_img_cfg_count(fake), 3)     # align + retry-align + stream

    def test_stream_level_transient_loss_recovers_via_actions_retry(self):
        # drop_ack_at=5 is beyond align's 3 ring wraps, so it lands on upload
        # chunk 5 of every attempt; drop_once makes attempt 2 fully ACKed.
        fake = FakeTransport(drop_ack_at=5, drop_once=True)
        dev = AK820Pro(fake)
        with redirect_stdout(io.StringIO()):
            ok = upload_frames_to_screen(dev, FRAMES, None, label="t", force=True)
        self.assertTrue(ok)
        self.assertIn(b"\x04\x02", _cmds(fake))
        self.assertEqual(_img_cfg_count(fake), 4)     # align + stream + retry-align + retry-stream

    def test_permanent_drop_still_fails_after_retry(self):
        fake = FakeTransport(drop_ack_at=5)           # sticky: drops every attempt
        dev = AK820Pro(fake)
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(UploadIncompleteError) as ctx:
                upload_frames_to_screen(dev, FRAMES, None, label="t", force=True)
        self.assertEqual(ctx.exception.total, 9)
        self.assertLess(ctx.exception.acks, ctx.exception.total)
        # The retry's stream aborted before its flash SAVE: the last feature
        # reports are the retry's START + IMAGE_CFG, never a trailing 04 02.
        self.assertEqual(_cmds(fake)[-2:], [b"\x04\x18", b"\x04r"])


class TestDisconnectRecovery(ResilienceTestCase):
    def test_unplug_mid_stream_raises_tempfail(self):
        fake = FakeTransport(disconnect_at=2)
        dev = AK820Pro(fake)
        with self.assertRaises(DeviceDisconnectedError):
            stream_payload(dev, b"\x00" * (9 * 4096), 9)
        self.assertEqual(DeviceDisconnectedError.exit_code, 75)
        self.assertNotIn(b"\x04\x02", _cmds(fake))    # no SAVE after the unplug

    def test_unplug_during_align_propagates_from_upload(self):
        fake = FakeTransport(disconnect_at=1)         # dies on align's first wrap
        dev = AK820Pro(fake)
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(DeviceDisconnectedError):
                upload_frames_to_screen(dev, FRAMES, None, label="t", force=True)
        self.assertNotIn(b"\x04\x02", _cmds(fake))

    def test_shim_exposes_tempfail_exit_code(self):
        self.assertEqual(ak820_ctrl.DeviceDisconnectedError.exit_code, 75)


class TestAlignRetry(ResilienceTestCase):
    def test_align_recovers_on_second_attempt(self):  # m4 recovery path
        fake = FlakyOnceAlign()
        dev = AK820Pro(fake)
        with redirect_stdout(io.StringIO()):
            ok = upload_frames_to_screen(dev, FRAMES, None, label="t", force=True)
        self.assertTrue(ok)
        self.assertEqual(_img_cfg_count(fake), 3)     # deaf align + good align + stream
        self.assertIn(b"\x04\x02", _cmds(fake))

    def test_align_gives_up_after_one_retry(self):    # m4 abort path
        fake = AlwaysDeafAlign()
        dev = AK820Pro(fake)
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(ProtocolError):
                upload_frames_to_screen(dev, FRAMES, None, label="t", force=True)
        # Exactly two align probes, and the stream IMAGE_CFG (9 chunks) never ran.
        self.assertEqual(_img_cfg_count(fake), 2)

    def test_device_upload_frames_align_abort(self):
        fake = AlwaysDeafAlign()
        with self.assertRaises(ProtocolError):
            AK820Pro(fake).upload_frames(FRAMES, align=True)


class TestHandshakeObservability(unittest.TestCase):  # §3.6
    def test_handshake_failures_are_counted(self):
        # v0.12.0 (D9): §3.6 completed — the first failure is tolerated and
        # counted; the second consecutive failure means the control pipe is
        # dead, so handshake() raises instead of counting to 2 in silence.
        dev = AK820Pro(DeafControlTransport())
        self.assertIsNone(dev.handshake())
        self.assertEqual(dev.handshake_failures, 1)
        with self.assertRaises(DeviceDisconnectedError):
            dev.handshake()

    def test_healthy_handshake_does_not_count(self):
        dev = AK820Pro(FakeTransport())
        self.assertIsNotNone(dev.handshake())
        self.assertEqual(dev.handshake_failures, 0)


class TestErrnoTranslation(unittest.TestCase):
    def test_enodev_maps_to_tempfail_disconnect(self):
        exc = LinuxHidrawTransport._translate(OSError(errno.ENODEV, "gone"), "read")
        self.assertIsInstance(exc, DeviceDisconnectedError)
        self.assertEqual(exc.exit_code, 75)

    def test_eacces_maps_to_permission_denied(self):
        exc = LinuxHidrawTransport._translate(OSError(errno.EACCES, "denied"), "write")
        self.assertIsInstance(exc, PermissionDeniedError)

    def test_unrelated_errno_passes_through(self):
        raw = OSError(errno.EIO, "io error")
        self.assertIs(LinuxHidrawTransport._translate(raw, "x"), raw)

    def test_read_data_enodev_raises_instead_of_masking_as_timeout(self):
        t = LinuxHidrawTransport(ctrl_fd=None, data_fd=3)
        with mock.patch(
            "ak820.transport.select.select", side_effect=OSError(errno.ENODEV, "gone")
        ):
            with self.assertRaises(DeviceDisconnectedError):
                t.read_data(0.01)

    def test_read_data_transient_errno_is_a_plain_timeout(self):
        t = LinuxHidrawTransport(ctrl_fd=None, data_fd=3)
        with mock.patch(
            "ak820.transport.select.select", side_effect=OSError(errno.EAGAIN, "busy")
        ):
            self.assertIsNone(t.read_data(0.01))


class TestCliTempfailContract(unittest.TestCase):
    def test_main_exits_75_on_mid_op_disconnect(self):
        with mock.patch("ak820.cli._dispatch", side_effect=DeviceDisconnectedError("unplugged")):
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as ctx:
                    cli.main(["probe"])
        self.assertEqual(ctx.exception.code, 75)

    def test_watch_loop_propagates_disconnect_for_main_mapping(self):
        def boom():
            raise DeviceDisconnectedError("unplugged")

        with self.assertRaises(DeviceDisconnectedError):
            cli.run_watch_loop(boom, 0.001, "test")


if __name__ == "__main__":
    unittest.main()
