#!/usr/bin/env python3
"""v0.13.0 regression suite: alignment-skip on a known-clean pipe (§3.5/§4.1).

Invariant: the ~1.6-2 s `align_data_pipe` is skipped *only* when the state file
records a previous fully-ACKed + SAVE'd upload (sub_packet_idx provably 0,
FINDINGS.md §4). The flag flips to dirty pessimistically before streaming, so
any timeout/crash re-aligns on the next run. `FakeTransport` starts dirty
(nonzero ring index) so the skip is proven state-driven, never unconditional.
All offline — zero hardware.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ak820 import state
from ak820.actions import upload_frames_to_screen
from ak820.device import AK820Pro
from ak820.errors import UploadIncompleteError
from fake_transport import DIRTY_START_IDX, FakeTransport

FRAMES = [b"\x00" * 32768]  # 1 frame + 256B header -> 9 chunks


class StateEnvCase(unittest.TestCase):
    """Isolates the state file so tests never touch ~/.cache."""

    def setUp(self):
        self._state_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._state_dir.cleanup)
        self.state_path = os.path.join(self._state_dir.name, "state.json")
        env = mock.patch.dict(os.environ, {"AK820_STATE_PATH": self.state_path})
        env.start()
        self.addCleanup(env.stop)

    def _doc(self):
        with open(self.state_path, encoding="utf-8") as f:
            return json.load(f)


class CountingAK820(AK820Pro):
    """AK820Pro that records how many times align_data_pipe actually ran."""

    def __init__(self, transport):
        super().__init__(transport)
        self.align_calls = 0

    def align_data_pipe(self):
        self.align_calls += 1
        return super().align_data_pipe()


class TestPipeFlagPersistence(StateEnvCase):
    def test_absent_state_is_dirty(self):
        self.assertFalse(state.pipe_known_clean())

    def test_corrupt_state_is_dirty(self):
        Path(self.state_path).write_text("{not json", encoding="utf-8")
        self.assertFalse(state.pipe_known_clean())

    def test_mark_pipe_round_trip(self):
        state.mark_pipe(True)
        self.assertTrue(state.pipe_known_clean())
        state.mark_pipe(False)
        self.assertFalse(state.pipe_known_clean())

    def test_flag_is_strict_boolean(self):
        # A truthy-but-not-True value must NOT unlock the skip (fail dirty).
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        Path(self.state_path).write_text(json.dumps({"pipe_clean": "yes"}), encoding="utf-8")
        self.assertFalse(state.pipe_known_clean())

    def test_mark_pipe_preserves_dedup_fields(self):
        state.save_payload_hash("abc123", "radar")
        state.mark_pipe(True)
        doc = self._doc()
        self.assertEqual(doc["sha256"], "abc123")
        self.assertEqual(doc["mode"], "radar")
        self.assertTrue(doc["pipe_clean"])

    def test_save_payload_hash_preserves_pipe_flag(self):
        state.mark_pipe(True)
        state.save_payload_hash("abc123", "hud")
        self.assertTrue(self._doc()["pipe_clean"])
        self.assertTrue(state.pipe_known_clean())

    def test_atomic_write_leaves_no_temp_files(self):
        state.mark_pipe(True)
        leftovers = [p for p in os.listdir(os.path.dirname(self.state_path)) if p != "state.json"]
        self.assertEqual(leftovers, [])


class TestFakeTransportStartsDirty(unittest.TestCase):
    def test_default_ring_index_is_nonzero(self):
        fake = FakeTransport()
        self.assertEqual(fake.sub_packet_idx, DIRTY_START_IDX)
        self.assertNotEqual(fake.sub_packet_idx, 0)

    def test_align_locks_dirty_ring_to_zero(self):
        fake = FakeTransport()
        _, acked = AK820Pro(fake).align_data_pipe()
        self.assertTrue(acked)
        self.assertEqual(fake.sub_packet_idx, 0)


class TestUploadAlignSkip(StateEnvCase):
    def _upload(self, dev):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = upload_frames_to_screen(dev, FRAMES, None, label="t", force=True)
        return ok, buf.getvalue()

    def test_first_upload_aligns_then_marks_clean(self):
        dev = CountingAK820(FakeTransport())
        ok, out = self._upload(dev)
        self.assertTrue(ok)
        self.assertGreaterEqual(dev.align_calls, 1)   # dirty state -> align ran
        self.assertIn("Aligning USB data pipe", out)
        self.assertTrue(state.pipe_known_clean())

    def test_repeat_upload_skips_alignment(self):
        with contextlib.redirect_stdout(io.StringIO()):
            upload_frames_to_screen(CountingAK820(FakeTransport()), FRAMES, None, label="t", force=True)
        dev = CountingAK820(FakeTransport())          # ring starts dirty again
        ok, out = self._upload(dev)
        self.assertTrue(ok)
        self.assertEqual(dev.align_calls, 0)          # state-driven skip
        self.assertIn("skipping alignment", out)
        self.assertTrue(state.pipe_known_clean())

    def test_failed_upload_flips_state_dirty(self):
        # Sticky drop at wrap #5: lands on stream chunk 2 in BOTH attempts
        # (IMAGE_CFG resets chunks_seen), so the upload aborts for good.
        dev = CountingAK820(FakeTransport(drop_ack_at=5))
        with self.assertRaises(UploadIncompleteError):
            self._upload(dev)
        self.assertFalse(state.pipe_known_clean())
        doc = self._doc()
        self.assertFalse(doc["pipe_clean"])
        self.assertNotIn("sha256", doc)               # digest never recorded

    def test_recovery_realigns_after_failure(self):
        with self.assertRaises(UploadIncompleteError):
            self._upload(CountingAK820(FakeTransport(drop_ack_at=5)))
        dev = CountingAK820(FakeTransport())          # healthy device, dirty state
        ok, out = self._upload(dev)
        self.assertTrue(ok)
        self.assertGreaterEqual(dev.align_calls, 1)   # dirty -> re-aligned
        self.assertTrue(state.pipe_known_clean())

    def test_dedup_skip_leaves_pipe_flag_untouched(self):
        with contextlib.redirect_stdout(io.StringIO()):
            upload_frames_to_screen(CountingAK820(FakeTransport()), FRAMES, None, label="t", force=True)
        dev = CountingAK820(FakeTransport())
        with contextlib.redirect_stdout(io.StringIO()):
            ok = upload_frames_to_screen(dev, FRAMES, None, label="t", force=False)
        self.assertFalse(ok)                          # dedup skip, no USB traffic
        self.assertEqual(dev.align_calls, 0)
        self.assertTrue(state.pipe_known_clean())

    def test_wall_time_saving_on_repeat_upload(self):
        """§4.1 measurement: the skipped run must be materially faster."""
        t0 = time.monotonic()
        with contextlib.redirect_stdout(io.StringIO()):
            upload_frames_to_screen(CountingAK820(FakeTransport()), FRAMES, None, label="t", force=True)
        aligned_s = time.monotonic() - t0

        t0 = time.monotonic()
        dev = CountingAK820(FakeTransport())
        with contextlib.redirect_stdout(io.StringIO()):
            upload_frames_to_screen(dev, FRAMES, None, label="t", force=True)
        skipped_s = time.monotonic() - t0

        self.assertEqual(dev.align_calls, 0)
        print(f"\n  [§4.1 measurement] aligned upload: {aligned_s:.2f}s | "
              f"align-skipped upload: {skipped_s:.2f}s | saved: {aligned_s - skipped_s:.2f}s")
        # The align sleeps (~0.75s against FakeTransport; ~1.6-2s on real
        # hardware) dominate the delta — a 25% margin absorbs CI jitter.
        self.assertLess(skipped_s, aligned_s * 0.75)


if __name__ == "__main__":
    unittest.main()
