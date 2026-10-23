#!/usr/bin/env python3
"""
Regression tests for ak820_ctrl CLI argument parsing.

Covers Defect D1: `backlight set --mode <name>` used to crash with an
unhandled ValueError because `int(args.mode)` was eagerly evaluated as the
dict.get() default before the name lookup could win.
"""

import sys
import unittest
from pathlib import Path

# Ensure project root is in path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ak820_ctrl


class TestParseMode(unittest.TestCase):
    def test_all_named_modes_resolve(self):
        # Every advertised mode name must resolve to its firmware value.
        for name, expected in ak820_ctrl.LIGHT_MODES.items():
            self.assertEqual(ak820_ctrl.parse_mode(name), expected)

    def test_named_modes_case_insensitive(self):
        self.assertEqual(ak820_ctrl.parse_mode("BREATH"), 7)
        self.assertEqual(ak820_ctrl.parse_mode("Rainbow" if "rainbow" in ak820_ctrl.LIGHT_MODES else "Spectrum"), 8)
        self.assertEqual(ak820_ctrl.parse_mode("sTaTiC"), 1)
        self.assertEqual(ak820_ctrl.parse_mode("  Off  "), 0)

    def test_valid_numeric_modes(self):
        self.assertEqual(ak820_ctrl.parse_mode("0"), 0)
        self.assertEqual(ak820_ctrl.parse_mode("7"), 7)
        self.assertEqual(ak820_ctrl.parse_mode("19"), ak820_ctrl.MAX_LIGHT_MODE)

    def test_numeric_bounds(self):
        self.assertEqual(ak820_ctrl.MAX_LIGHT_MODE, 19)
        with self.assertRaises(ValueError):
            ak820_ctrl.parse_mode("20")
        with self.assertRaises(ValueError):
            ak820_ctrl.parse_mode("-1")
        with self.assertRaises(ValueError):
            ak820_ctrl.parse_mode("999")

    def test_invalid_inputs_raise_friendly_value_error(self):
        for bad in ("rainbow-party", "not-a-mode", "12.5", "abc", "", "   ", None):
            with self.assertRaises(ValueError) as ctx:
                ak820_ctrl.parse_mode(bad)
            # Message must guide the user toward valid input.
            self.assertIn("mode", str(ctx.exception).lower())

    def test_out_of_range_message_mentions_range(self):
        with self.assertRaises(ValueError) as ctx:
            ak820_ctrl.parse_mode("42")
        self.assertIn("0..19", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
