#!/usr/bin/env python3
"""
Regression tests for Defect D3: the Sonix firmware GIF header stores the
frame count in a single byte, so uploads of >255 frames silently wrapped
(& 0xFF) and the device animated garbage while the tool reported success.

Invariants:
  - `upload_frames_to_screen` raises ValueError on >MAX_FRAMES frames and
    sends *zero* USB packets (no align, START, IMAGE_CFG, or data chunks).
  - Exactly MAX_FRAMES frames still upload normally.
  - `build_radar_frames` (hybrid) clamps its total frame count to MAX_FRAMES.
"""

import datetime
import sys
import unittest
from pathlib import Path
from unittest import mock

# Ensure project root and tests dir are in path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ak820_ctrl
import milestone_radar as mr
from test_upload import FakeTransport


class TestFrameLimit(unittest.TestCase):
    def test_max_frames_constant(self):
        self.assertEqual(ak820_ctrl.MAX_FRAMES, 255)

    def test_256_frames_rejected_before_any_usb_packet(self):
        dev = FakeTransport()
        frames = [b"\xAB" * 100] * 256
        with mock.patch.object(mr, "save_payload_hash") as save:
            with self.assertRaises(ValueError) as ctx:
                ak820_ctrl.upload_frames_to_screen(
                    dev, frames, None, label="toolong", force=True
                )
        self.assertIn("256", str(ctx.exception))
        self.assertIn("255", str(ctx.exception))
        # Nothing must have touched the device: no feature reports, no chunks.
        self.assertEqual(dev.feature_packets, [])
        self.assertEqual(dev.sent_chunks, 0)
        save.assert_not_called()

    def test_255_frames_upload_valid(self):
        dev = FakeTransport()
        frames = [b"\xAB" * 100] * 255
        with mock.patch.object(mr, "save_payload_hash") as save:
            res = ak820_ctrl.upload_frames_to_screen(
                dev, frames, None, label="edge", force=True
            )
        self.assertTrue(res)
        save.assert_called_once()
        self.assertGreater(dev.sent_chunks, 0)

    def test_hybrid_radar_clamped_to_max_frames(self):
        today = datetime.date(2026, 10, 23)
        upcoming = mr.compute_upcoming_events(
            {
                "events": [
                    {
                        "id": "t",
                        "title": "Test Birthday",
                        "short_title": "TEST",
                        "date": "2020-10-25",
                        "category": "birthday",
                    }
                ]
            },
            today=today,
        )
        # Force the raw hybrid total well past 255 (4 Bongo loops x 60 frames
        # + 2 slides x 60 frames = 360) and assert it is clamped, not wrapped.
        big_gif = ([b"\x00" * 100] * 60, [125] * 60)
        with mock.patch.object(
            ak820_ctrl, "load_frames_from_file", return_value=big_gif
        ):
            frames, delays = ak820_ctrl.build_radar_frames(
                "hybrid", upcoming, today, slide_seconds=60.0
            )
        self.assertEqual(len(frames), ak820_ctrl.MAX_FRAMES)
        self.assertEqual(len(frames), len(delays))

    def test_cycle_radar_within_limit(self):
        today = datetime.date(2026, 10, 23)
        frames, delays = ak820_ctrl.build_radar_frames(
            "cycle", [], today, slide_seconds=60.0
        )
        self.assertLessEqual(len(frames), ak820_ctrl.MAX_FRAMES)


if __name__ == "__main__":
    unittest.main()
