#!/usr/bin/env python3
"""
Regression tests for v0.8.0 `ak820/state.py` (REVIEW.md §10.4 item 3):
payload dedup moved out of milestone_radar (layering inversion) with an
`AK820_STATE_PATH` env override so the round-trip is testable offline.
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ak820 import state


class DedupEnvCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "nested", "state.json")
        self._old = os.environ.get("AK820_STATE_PATH")
        os.environ["AK820_STATE_PATH"] = self.path

    def tearDown(self):
        if self._old is None:
            os.environ.pop("AK820_STATE_PATH", None)
        else:
            os.environ["AK820_STATE_PATH"] = self._old
        self.tmp.cleanup()


class TestStatePath(DedupEnvCase):
    def test_env_override(self):
        self.assertEqual(state.get_state_path(), self.path)

    def test_default_without_env(self):
        os.environ.pop("AK820_STATE_PATH", None)
        self.assertEqual(state.get_state_path(), state.DEFAULT_STATE_PATH)

    def test_tilde_expanded(self):
        os.environ["AK820_STATE_PATH"] = "~/ak820_test_state.json"
        self.assertFalse(state.get_state_path().startswith("~"))


class TestDedupRoundTrip(DedupEnvCase):
    PAYLOAD = b"\x01\x5A" + bytes(range(256)) * 40

    def test_first_upload_always_proceeds(self):
        should, digest = state.check_and_record_payload_hash(self.PAYLOAD, "radar")
        self.assertTrue(should)
        self.assertEqual(digest, hashlib.sha256(self.PAYLOAD).hexdigest())

    def test_identical_payload_skipped_after_save(self):
        _, digest = state.check_and_record_payload_hash(self.PAYLOAD, "radar")
        state.save_payload_hash(digest, "radar")
        should, digest2 = state.check_and_record_payload_hash(self.PAYLOAD, "radar")
        self.assertFalse(should)
        self.assertEqual(digest, digest2)

    def test_force_overrides_dedup(self):
        _, digest = state.check_and_record_payload_hash(self.PAYLOAD, "radar")
        state.save_payload_hash(digest, "radar")
        should, _ = state.check_and_record_payload_hash(self.PAYLOAD, "radar", force=True)
        self.assertTrue(should)

    def test_changed_payload_reuploads(self):
        _, digest = state.check_and_record_payload_hash(self.PAYLOAD, "radar")
        state.save_payload_hash(digest, "radar")
        should, _ = state.check_and_record_payload_hash(self.PAYLOAD + b"\x00", "radar")
        self.assertTrue(should)

    def test_saved_json_shape(self):
        _, digest = state.check_and_record_payload_hash(self.PAYLOAD, "hud")
        state.save_payload_hash(digest, "hud")
        with open(self.path, encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual(doc["sha256"], digest)
        self.assertEqual(doc["mode"], "hud")
        self.assertIn("updated_at", doc)

    def test_corrupt_state_file_fails_open(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        Path(self.path).write_text("{not json", encoding="utf-8")
        should, _ = state.check_and_record_payload_hash(self.PAYLOAD, "radar")
        self.assertTrue(should)

    def test_memoryview_and_bytearray_inputs(self):
        # §4.5: hashing accepts any buffer protocol object, digest unchanged.
        _, d_bytes = state.check_and_record_payload_hash(self.PAYLOAD, "x")
        _, d_ba = state.check_and_record_payload_hash(bytearray(self.PAYLOAD), "x")
        _, d_mv = state.check_and_record_payload_hash(memoryview(self.PAYLOAD), "x")
        self.assertEqual(d_bytes, d_ba)
        self.assertEqual(d_bytes, d_mv)


class TestBackcompatReexports(unittest.TestCase):
    def test_milestone_radar_reexports_state_fns(self):
        import milestone_radar as mr

        self.assertIs(mr.check_and_record_payload_hash, state.check_and_record_payload_hash)
        self.assertIs(mr.save_payload_hash, state.save_payload_hash)

    def test_ctrl_reexports_state_fns(self):
        import ak820_ctrl

        self.assertIs(ak820_ctrl.check_and_record_payload_hash, state.check_and_record_payload_hash)
        self.assertIs(ak820_ctrl.save_payload_hash, state.save_payload_hash)


if __name__ == "__main__":
    unittest.main()
