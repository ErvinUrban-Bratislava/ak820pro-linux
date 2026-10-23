#!/usr/bin/env python3
"""v0.17.0 timeout guards & bounded I/O — zero hardware attached (§16).

Covers the reliability contract added in v0.17.0:
  * drain_data_acks is bounded: a chattering (always-readable) data pipe can
    no longer spin the loop forever and hang the CLI / systemd unit,
  * stream_payload enforces a whole-transfer deadline (base + per-chunk
    budget): a device that ACKs just under the per-chunk timeout can never
    keep an upload alive indefinitely; exceeding it is an UploadIncompleteError
    (exit 5) raised *before* SAVE, so the digest stays unrecorded and the
    actions-level full retry applies,
  * LinuxHidrawTransport.write_data completes short writes (write-all loop)
    and raises ProtocolError on a stalled pipe instead of silently sending a
    partial chunk (which would desync the firmware ring counter like a drop),
  * FakeTransport `flood` fault models the chattering pipe.
"""

import errno
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from ak820.actions import upload_frames_to_screen
from ak820.device import AK820Pro, stream_payload
from ak820.errors import (
    DeviceDisconnectedError,
    PermissionDeniedError,
    ProtocolError,
    UploadIncompleteError,
)
from ak820.protocol import (
    CHUNK_ACK_TIMEOUT_MS,
    CHUNK_DEADLINE_FACTOR,
    DRAIN_MAX_READS,
    UPLOAD_BASE_DEADLINE_S,
    build_payload,
)
from ak820.transport import LinuxHidrawTransport
from fake_transport import FakeTransport

FRAMES = [b"\x00" * 32768]  # + 256B header -> 9 chunks


class FakeClock:
    """Monotonic clock that advances `step` seconds on every call."""

    def __init__(self, step):
        self.t = 0.0
        self.step = step

    def __call__(self):
        v = self.t
        self.t += self.step
        return v


class CountingTransport(FakeTransport):
    """Counts every read_data call so the drain bound is observable."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.reads = 0

    def read_data(self, timeout):
        self.reads += 1
        return super().read_data(timeout)


class DrainBoundTests(unittest.TestCase):
    def test_drain_stops_at_cap_on_flooded_pipe(self):
        # Pre-v0.17.0 this loop spun forever on an always-readable pipe.
        fake = CountingTransport(flood=True)
        dev = AK820Pro(fake)
        drained = dev.drain_data_acks()
        self.assertEqual(fake.reads, DRAIN_MAX_READS)
        self.assertEqual(drained, DRAIN_MAX_READS)

    def test_drain_cap_is_overridable(self):
        fake = CountingTransport(flood=True)
        dev = AK820Pro(fake)
        dev.drain_data_acks(max_reads=5)
        self.assertEqual(fake.reads, 5)

    def test_drain_normal_pipe_empties_queue_and_returns_count(self):
        fake = FakeTransport()
        dev = AK820Pro(fake)
        fake.rx_queue.extend([b"\x01\x5A\x02"] * 3)
        self.assertEqual(dev.drain_data_acks(), 3)
        self.assertEqual(fake.rx_queue, [])

    def test_align_terminates_on_flooded_pipe(self):
        # align_data_pipe drains twice; both drains are now bounded, so a
        # chattering pipe completes alignment instead of hanging the CLI.
        fake = FakeTransport(flood=True)
        dev = AK820Pro(fake)
        (coarse, fine), acked = dev.align_data_pipe()
        self.assertEqual(coarse, 1)  # first burst "answered" instantly
        self.assertTrue(acked)


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.payload, _, self.chunks = build_payload(FRAMES)
        self.assertEqual(self.chunks, 9)

    def test_healthy_transfer_meets_default_deadline(self):
        dev = AK820Pro(FakeTransport())
        stream_payload(dev, self.payload, self.chunks, clock=FakeClock(0.001))
        # SAVE (04 02) reached: every chunk ACKed inside the deadline.
        self.assertEqual(dev.transport.feature_log[-1][:2], b"\x04\x02")

    def test_deadline_exceeded_raises_before_save(self):
        fake = FakeTransport()
        dev = AK820Pro(fake)
        # clock: start=0.0; checks at 0.6 (pass), 1.2 (> 1.0 deadline) at idx 1
        with self.assertRaises(UploadIncompleteError) as cm:
            stream_payload(dev, self.payload, self.chunks,
                           deadline_s=1.0, clock=FakeClock(0.6))
        self.assertEqual(cm.exception.acks, 1)
        self.assertEqual(cm.exception.total, self.chunks)
        self.assertEqual(cm.exception.exit_code, 5)
        # aborted *before* SAVE: last feature report is IMAGE_CFG, never 04 02
        self.assertNotEqual(fake.feature_log[-1][:2], b"\x04\x02")

    def test_default_deadline_formula_pins_per_chunk_budget(self):
        # default = base + chunks * timeout * factor = 30 + 9*1*2 = 48 s;
        # clock steps 10 s: checks 10,20,30,40 pass, 50 breaches at idx 4.
        expected = UPLOAD_BASE_DEADLINE_S + self.chunks * (
            CHUNK_ACK_TIMEOUT_MS / 1000.0) * CHUNK_DEADLINE_FACTOR
        self.assertEqual(expected, 48.0)
        dev = AK820Pro(FakeTransport())
        with self.assertRaises(UploadIncompleteError) as cm:
            stream_payload(dev, self.payload, self.chunks, clock=FakeClock(10.0))
        self.assertEqual(cm.exception.acks, 4)

    def test_negative_deadline_aborts_before_first_chunk(self):
        fake = FakeTransport()
        dev = AK820Pro(fake)
        with self.assertRaises(UploadIncompleteError) as cm:
            stream_payload(dev, self.payload, self.chunks,
                           deadline_s=-1.0, clock=FakeClock(0.0))
        self.assertEqual(cm.exception.acks, 0)
        self.assertEqual(fake.chunks_seen, 0)  # no chunk ever left the host


class WriteAllTests(unittest.TestCase):
    """LinuxHidrawTransport.write_data short-write / stall behavior (fd mocked)."""

    def _write(self, side_effect=None, return_value=None):
        kw = {"side_effect": side_effect} if side_effect is not None else {"return_value": return_value}
        with mock.patch("ak820.transport.os.write", **kw) as w:
            LinuxHidrawTransport(data_fd=7).write_data(b"x" * 64)
        return w

    def test_full_write_single_call_fast_path(self):
        w = self._write(return_value=65)  # 0x00 prefix + 64-byte payload
        self.assertEqual(w.call_count, 1)
        self.assertEqual(bytes(w.call_args[0][1]), b"\x00" + b"x" * 64)

    def test_short_write_is_completed_in_loop(self):
        w = self._write(side_effect=[10, 30, 25])  # 65 bytes in three slices
        self.assertEqual(w.call_count, 3)
        # each call re-offered only the remaining slice: 65 -> 55 -> 25
        self.assertEqual([len(c.args[1]) for c in w.call_args_list], [65, 55, 25])

    def test_stalled_write_raises_protocol_error(self):
        with self.assertRaises(ProtocolError):
            self._write(return_value=0)  # pipe accepts nothing, ever

    def test_enodev_during_write_maps_to_disconnect(self):
        with self.assertRaises(DeviceDisconnectedError):
            self._write(side_effect=OSError(errno.ENODEV, "gone"))

    def test_eacces_during_write_maps_to_permission_denied(self):
        with self.assertRaises(PermissionDeniedError):
            self._write(side_effect=OSError(errno.EACCES, "no udev rule"))


class ActionsIntegrationTests(unittest.TestCase):
    """The guards through the real upload pipeline (dedup state isolated)."""

    def setUp(self):
        self._state_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._state_dir.cleanup)
        env = mock.patch.dict(
            os.environ,
            {"AK820_STATE_PATH": os.path.join(self._state_dir.name, "state.json")},
        )
        env.start()
        self.addCleanup(env.stop)

    def test_upload_happy_path_under_default_deadline(self):
        dev = AK820Pro(FakeTransport())
        self.assertTrue(upload_frames_to_screen(dev, FRAMES, label="t"))

    def test_upload_completes_on_flooded_pipe(self):
        # A chattering pipe answers every ACK read: bounded drains keep
        # align + stream terminating, and the transfer still completes.
        dev = AK820Pro(FakeTransport(flood=True))
        self.assertTrue(upload_frames_to_screen(dev, FRAMES, label="t"))


if __name__ == "__main__":
    unittest.main()
