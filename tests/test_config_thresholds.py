#!/usr/bin/env python3
"""
Regression tests for configurable urgency thresholds (Defect D7).

`events.example.json` shipped `warn_days` / `urgent_days` but no code read
them — the thresholds were hardcoded 3/14/35 in `compute_upcoming_events`.
A shipped example that lies about configurability generates support issues.
`compute_upcoming_events` now honors `urgent_days`, `warn_days`, `soon_days`
from the config, falling back to the previous hardcodes on invalid values.
"""

import datetime
import sys
import unittest
from pathlib import Path

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import milestone_radar as mr

TODAY = datetime.date(2026, 6, 15)


def _theme(days_left, cfg_extra=None):
    date = (TODAY + datetime.timedelta(days=days_left)).strftime("%Y-%m-%d")
    cfg = {"events": [{"id": "e", "title": "E", "date": date}]}
    cfg.update(cfg_extra or {})
    return mr.compute_upcoming_events(cfg, today=TODAY)[0]["theme_key"]


class DefaultThresholdTests(unittest.TestCase):
    """Defaults must match the previous hardcodes exactly (3/14/35)."""

    def test_default_bands(self):
        self.assertEqual(_theme(0), "today")
        self.assertEqual(_theme(3), "urgent")
        self.assertEqual(_theme(4), "warn")
        self.assertEqual(_theme(14), "warn")
        self.assertEqual(_theme(15), "soon")
        self.assertEqual(_theme(35), "soon")
        self.assertEqual(_theme(36), "calm")


class CustomThresholdTests(unittest.TestCase):
    def test_custom_urgent_days(self):
        self.assertEqual(_theme(5, {"urgent_days": 7}), "urgent")
        self.assertEqual(_theme(5), "warn")  # unchanged without the key

    def test_custom_warn_days(self):
        self.assertEqual(_theme(10, {"warn_days": 7}), "soon")
        self.assertEqual(_theme(10), "warn")

    def test_custom_soon_days(self):
        self.assertEqual(_theme(25, {"soon_days": 20}), "calm")
        self.assertEqual(_theme(25), "soon")

    def test_invalid_thresholds_fall_back(self):
        self.assertEqual(_theme(5, {"urgent_days": "abc"}), "warn")   # not an int
        self.assertEqual(_theme(5, {"urgent_days": None}), "warn")    # wrong type
        self.assertEqual(_theme(5, {"urgent_days": -1}), "warn")      # negative
        self.assertEqual(_theme(5, {"urgent_days": "7"}), "urgent")   # numeric string honored


class ExampleConfigTests(unittest.TestCase):
    def test_example_json_thresholds_are_honored_keys(self):
        """Every *_days key shipped in events.example.json must be read by code."""
        import json
        example = Path(__file__).resolve().parent.parent / "events.example.json"
        cfg = json.loads(example.read_text())
        for key in ("urgent_days", "warn_days", "soon_days"):
            self.assertIn(key, cfg)
            self.assertIsInstance(mr._threshold(cfg, key, 0), int)


if __name__ == "__main__":
    unittest.main()
