#!/usr/bin/env python3
"""
Unit tests for Ajazz AK820 Pro screen controller and milestone radar.
"""

import datetime
import os
import sys
import unittest
from pathlib import Path
from PIL import Image

# Ensure project root is in path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ak820_ctrl
import milestone_radar as mr


class TestAK820Protocol(unittest.TestCase):
    def test_cmd_packet_structure(self):
        pkt = ak820_ctrl._cmd_packet(0x18, enable=False)
        self.assertEqual(len(pkt), 64)
        self.assertEqual(pkt[0], 0x04)
        self.assertEqual(pkt[1], 0x18)
        self.assertEqual(pkt[8], 0x00)

        pkt_on = ak820_ctrl._cmd_packet(0x02, enable=True)
        self.assertEqual(pkt_on[0], 0x04)
        self.assertEqual(pkt_on[1], 0x02)
        self.assertEqual(pkt_on[8], 0x01)

    def test_image_to_rgb565(self):
        # 128x128 solid red image
        img = Image.new("RGB", (128, 128), (255, 0, 0))
        data = ak820_ctrl.image_to_rgb565(img)
        self.assertEqual(len(data), 128 * 128 * 2)
        # Red in RGB565 = 0xF800, little endian = 0x00, 0xF8
        self.assertEqual(data[0], 0x00)
        self.assertEqual(data[1], 0xF8)

        # 128x128 solid green image
        img_green = Image.new("RGB", (128, 128), (0, 255, 0))
        data_green = ak820_ctrl.image_to_rgb565(img_green)
        # Green in RGB565 = 0x07E0, little endian = 0xE0, 0x07
        self.assertEqual(data_green[0], 0xE0)
        self.assertEqual(data_green[1], 0x07)

        # 128x128 solid blue image
        img_blue = Image.new("RGB", (128, 128), (0, 0, 255))
        data_blue = ak820_ctrl.image_to_rgb565(img_blue)
        # Blue in RGB565 = 0x001F, little endian = 0x1F, 0x00
        self.assertEqual(data_blue[0], 0x1F)
        self.assertEqual(data_blue[1], 0x00)


class TestMilestoneRadar(unittest.TestCase):
    def test_upcoming_events_calculation(self):
        cfg = {
            "events": [
                {
                    "id": "test-bday",
                    "title": "Test Birthday",
                    "short_title": "TEST",
                    "date": "2020-10-25",
                    "category": "birthday"
                },
                {
                    "id": "test-anniv",
                    "title": "Test Anniversary",
                    "short_title": "ANNIV",
                    "date": "2018-10-15",
                    "category": "anniversary"
                }
            ]
        }
        ref_today = datetime.date(2026, 10, 20)
        upcoming = mr.compute_upcoming_events(cfg, today=ref_today)
        self.assertEqual(len(upcoming), 2)

        # First upcoming is Test Birthday in 5 days (2026-10-25) -> turns 6
        self.assertEqual(upcoming[0]["id"], "test-bday")
        self.assertEqual(upcoming[0]["days_left"], 5)
        self.assertEqual(upcoming[0]["milestone_str"], "Turns 6")

        # Second upcoming is Test Anniversary (2027-10-15) -> 9th year
        self.assertEqual(upcoming[1]["id"], "test-anniv")
        self.assertEqual(upcoming[1]["days_left"], 360)
        self.assertEqual(upcoming[1]["milestone_str"], "9 Years")

    def test_custom_card_rendering(self):
        themes = ["soon", "warn", "urgent", "today", "calm", "green"]
        for theme in themes:
            img = mr.render_custom_card(
                title="TEST",
                value="ALL GOOD",
                sub="Subtitle line",
                footer="Footer line",
                theme_name=theme
            )
            self.assertEqual(img.size, (128, 128))
            self.assertEqual(img.mode, "RGB")

    def test_hero_and_board_rendering(self):
        cfg = {
            "events": [
                {
                    "id": "launch",
                    "title": "Open Source Launch",
                    "short_title": "RELEASE",
                    "date": "2026-10-25",
                    "category": "milestone"
                }
            ]
        }
        ref_today = datetime.date(2026, 10, 20)
        upcoming = mr.compute_upcoming_events(cfg, today=ref_today)

        hero_img = mr.render_hero_card(upcoming, today=ref_today)
        self.assertEqual(hero_img.size, (128, 128))

        board_img = mr.render_board_card(upcoming, today=ref_today)
        self.assertEqual(board_img.size, (128, 128))

    def test_default_events_path_precedence(self):
        # 1. Environment variable override
        orig_env = os.environ.get("AK820_EVENTS_PATH")
        try:
            os.environ["AK820_EVENTS_PATH"] = "/custom/path/events.json"
            self.assertEqual(mr.get_default_events_path(), "/custom/path/events.json")
        finally:
            if orig_env is None:
                os.environ.pop("AK820_EVENTS_PATH", None)
            else:
                os.environ["AK820_EVENTS_PATH"] = orig_env


if __name__ == "__main__":
    unittest.main()
