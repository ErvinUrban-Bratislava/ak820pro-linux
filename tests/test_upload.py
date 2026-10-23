#!/usr/bin/env python3
"""
Regression tests for Defect D2: `upload_frames_to_screen` used to print
"Upload and flash save complete!" and return True even when the device
dropped chunk ACKs, and the SAVE (04 02) flash command was sent regardless.

Invariant: success + SAVE + digest recording happen only when
acks == total_chunks. Any dropped ACK must raise UploadIncompleteError
(exit code 5) *before* SAVE, and must never record the payload digest.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Ensure project root is in path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ak820_ctrl
from ak820 import state


class FakeTransport:
    """Duck-typed AK820ProDevice with injectable per-chunk ACK drops."""

    def __init__(self, drop_chunk_idxs=()):
        self.drop_chunk_idxs = set(drop_chunk_idxs)
        self.sent_chunks = 0
        self.feature_packets = []  # every 64-byte feature report sent

    def align_data_pipe(self):
        # v0.11.0: a full retry re-aligns before re-streaming, and chunk
        # numbering restarts from 0 — the drop set applies per attempt.
        self.sent_chunks = 0
        return (0, True)

    def send_feature(self, data):
        self.feature_packets.append(bytes(data))

    def handshake(self, post_delay=0):
        pass

    def drain_data_acks(self):
        pass

    def send_data_chunk(self, chunk, timeout_ms=0):
        idx = self.sent_chunks
        self.sent_chunks += 1
        if idx in self.drop_chunk_idxs:
            return None  # simulate a dropped ACK (timeout)
        return b"\x01\x5a\x02" + b"\x00" * 61


def _cmd_bytes(dev):
    """First two bytes of each feature report (e.g. b'\\x04\\x02' == SAVE)."""
    return [p[:2] for p in dev.feature_packets]


class TestUploadIncomplete(unittest.TestCase):
    FRAMES = [b"\xAB" * 100, b"\xCD" * 100]  # header + 200B -> 1 chunk

    def setUp(self):
        # v0.13.0: upload_frames_to_screen persists the §3.5 pipe flag via
        # state.mark_pipe — isolate the state file so tests never touch ~/.cache.
        self._state_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._state_dir.cleanup)
        env = mock.patch.dict(
            os.environ,
            {"AK820_STATE_PATH": os.path.join(self._state_dir.name, "state.json")},
        )
        env.start()
        self.addCleanup(env.stop)

    def _upload(self, drop_idxs=()):
        dev = FakeTransport(drop_chunk_idxs=drop_idxs)
        with mock.patch.object(state, "save_payload_hash") as save:
            res = ak820_ctrl.upload_frames_to_screen(
                dev, self.FRAMES, None, label="test", force=True
            )
        return dev, res, save

    def test_all_acks_reports_success(self):
        dev, res, save = self._upload()
        self.assertTrue(res)
        save.assert_called_once()

    def test_dropped_ack_raises_upload_incomplete(self):
        dev = FakeTransport(drop_chunk_idxs={0})
        with mock.patch.object(state, "save_payload_hash") as save:
            with self.assertRaises(ak820_ctrl.UploadIncompleteError) as ctx:
                ak820_ctrl.upload_frames_to_screen(
                    dev, self.FRAMES, None, label="test", force=True
                )
        self.assertIn("0/1", str(ctx.exception))
        save.assert_not_called()

    def test_partial_acks_still_raise(self):
        # 3 frames of 3000B -> header + 9000B -> 3 chunks; drop the middle one.
        dev = FakeTransport(drop_chunk_idxs={1})
        with mock.patch.object(state, "save_payload_hash") as save:
            with self.assertRaises(ak820_ctrl.UploadIncompleteError) as ctx:
                ak820_ctrl.upload_frames_to_screen(
                    dev, [b"\x01" * 3000] * 3, None, label="test", force=True
                )
        self.assertIn("2/3", str(ctx.exception))
        save.assert_not_called()

    def test_no_save_command_sent_on_failure(self):
        dev = FakeTransport(drop_chunk_idxs={0})
        with mock.patch.object(state, "save_payload_hash"):
            with self.assertRaises(ak820_ctrl.UploadIncompleteError):
                ak820_ctrl.upload_frames_to_screen(
                    dev, self.FRAMES, None, label="test", force=True
                )
        # SAVE (04 02) must never reach the device when ACKs are missing.
        self.assertNotIn(b"\x04\x02", _cmd_bytes(dev))
        # ...but it *is* sent on the fully-ACKed success path.
        dev_ok, res, _ = self._upload()
        self.assertIn(b"\x04\x02", _cmd_bytes(dev_ok))

    def test_exit_code_is_5(self):
        self.assertEqual(ak820_ctrl.UploadIncompleteError.exit_code, 5)

    def test_main_maps_error_to_exit_code_5(self):
        # v0.9.0: main() lives in ak820.cli (ak820_ctrl.main is the same object).
        from ak820 import cli

        boom = ak820_ctrl.UploadIncompleteError("boom")
        with mock.patch.object(cli, "_dispatch", side_effect=boom):
            with self.assertRaises(SystemExit) as ctx:
                ak820_ctrl.main(["probe"])
        self.assertEqual(ctx.exception.code, 5)


if __name__ == "__main__":
    unittest.main()
