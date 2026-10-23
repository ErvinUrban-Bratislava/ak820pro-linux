#!/usr/bin/env python3
"""
Regression tests for milestone_radar event date handling.

Covers Defect D4: malformed event dates (`date="not-a-date"`) crashed
`radar` / `events list` with a raw ValueError traceback, and `events add
--date` performed no validation at all.

Covers Defect D5: the Feb-29 fallback in compute_upcoming_events assumed
Feb-29-on-non-leap was the only possible ValueError and itself crashed on
any other invalid component (e.g. `date="2020-13-45"` -> date(y, 13, 28)
-> "month must be in 1..12").
"""

import datetime
import sys
import unittest
from pathlib import Path

# Ensure project root is in path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import milestone_radar as mr


class TestParseEventDate(unittest.TestCase):
    def test_full_iso_date(self):
        self.assertEqual(mr.parse_event_date("2024-11-06"), (2024, 11, 6))

    def test_full_iso_date_single_digits_accepted(self):
        self.assertEqual(mr.parse_event_date("2024-1-5"), (2024, 1, 5))

    def test_recurring_mm_dd_has_no_year(self):
        self.assertEqual(mr.parse_event_date("11-06"), (None, 11, 6))

    def test_feb29_mm_dd_accepted_via_leap_year_probe(self):
        # 02-29 is a legitimate recurring date; probed against leap year 2024.
        self.assertEqual(mr.parse_event_date("02-29"), (None, 2, 29))

    def test_feb29_full_date_valid_on_leap_year(self):
        self.assertEqual(mr.parse_event_date("2024-02-29"), (2024, 2, 29))

    def test_feb29_full_date_rejected_on_non_leap_year(self):
        with self.assertRaises(mr.ConfigError):
            mr.parse_event_date("2023-02-29")

    def test_garbage_string_raises_config_error(self):
        # D4 reproduction: used to be an unhandled ValueError.
        with self.assertRaises(mr.ConfigError):
            mr.parse_event_date("not-a-date")

    def test_invalid_month_day_raises_config_error(self):
        # D5 reproduction: used to slip past parsing and crash the fallback.
        with self.assertRaises(mr.ConfigError):
            mr.parse_event_date("2020-13-45")

    def test_invalid_day_for_month_raises_config_error(self):
        with self.assertRaises(mr.ConfigError):
            mr.parse_event_date("2020-02-30")

    def test_empty_and_wrong_shape_raise_config_error(self):
        for raw in ("", "2024", "06", "2024-11-06-00", "1-2-3-4"):
            with self.assertRaises(mr.ConfigError):
                mr.parse_event_date(raw)

    def test_non_string_raises_config_error(self):
        with self.assertRaises(mr.ConfigError):
            mr.parse_event_date(None)

    def test_error_message_is_informative(self):
        with self.assertRaises(mr.ConfigError) as ctx:
            mr.parse_event_date("not-a-date")
        self.assertIn("not-a-date", str(ctx.exception))
        self.assertIn("YYYY-MM-DD", str(ctx.exception))


class TestSafeDate(unittest.TestCase):
    def test_valid_date_passthrough(self):
        self.assertEqual(mr._safe_date(2024, 11, 6), datetime.date(2024, 11, 6))

    def test_feb29_leap_year_passthrough(self):
        self.assertEqual(mr._safe_date(2024, 2, 29), datetime.date(2024, 2, 29))

    def test_feb29_non_leap_clamps_to_feb28(self):
        self.assertEqual(mr._safe_date(2023, 2, 29), datetime.date(2023, 2, 28))

    def test_invalid_month_raises_config_error_not_valueerror(self):
        # D5: the old fallback tried date(y, 13, 28) and died with ValueError.
        with self.assertRaises(mr.ConfigError):
            mr._safe_date(2023, 13, 45)

    def test_invalid_day_for_month_raises_config_error(self):
        with self.assertRaises(mr.ConfigError):
            mr._safe_date(2023, 4, 31)


class TestComputeUpcomingEvents(unittest.TestCase):
    def _cfg(self, *events):
        return {"events": list(events)}

    def test_malformed_date_raises_config_error(self):
        # D4 reproduction: compute used to blow up with a raw ValueError.
        cfg = self._cfg({"title": "X", "date": "not-a-date"})
        with self.assertRaises(mr.ConfigError):
            mr.compute_upcoming_events(cfg, today=datetime.date(2026, 10, 23))

    def test_invalid_components_raise_config_error(self):
        # D5 reproduction: "2020-13-45" used to crash the Feb-28 fallback.
        cfg = self._cfg({"title": "X", "date": "2020-13-45"})
        with self.assertRaises(mr.ConfigError):
            mr.compute_upcoming_events(cfg, today=datetime.date(2026, 10, 23))

    def test_feb29_event_on_non_leap_year_clamps(self):
        cfg = self._cfg({"title": "Leap Kid", "date": "02-29", "category": "birthday"})
        upcoming = mr.compute_upcoming_events(cfg, today=datetime.date(2023, 1, 1))
        self.assertEqual(len(upcoming), 1)
        self.assertEqual(upcoming[0]["next_date"], datetime.date(2023, 2, 28))
        self.assertEqual(upcoming[0]["days_left"], 58)

    def test_feb29_event_on_leap_year_keeps_feb29(self):
        cfg = self._cfg({"title": "Leap Kid", "date": "02-29", "category": "birthday"})
        upcoming = mr.compute_upcoming_events(cfg, today=datetime.date(2024, 1, 1))
        self.assertEqual(upcoming[0]["next_date"], datetime.date(2024, 2, 29))

    def test_mm_dd_past_date_rolls_to_next_year(self):
        # Negative offset: Jan 10 seen from Oct 23 must roll to next year,
        # so days_left is never negative.
        cfg = self._cfg({"title": "New Year", "date": "01-10"})
        upcoming = mr.compute_upcoming_events(cfg, today=datetime.date(2026, 10, 23))
        self.assertEqual(upcoming[0]["next_date"], datetime.date(2027, 1, 10))
        self.assertGreater(upcoming[0]["days_left"], 0)

    def test_event_today_is_zero_days(self):
        cfg = self._cfg({"title": "Today", "date": "10-23", "category": "birthday"})
        upcoming = mr.compute_upcoming_events(cfg, today=datetime.date(2026, 10, 23))
        self.assertEqual(upcoming[0]["days_left"], 0)
        self.assertEqual(upcoming[0]["theme_key"], "today")

    def test_sorted_by_days_left(self):
        cfg = self._cfg(
            {"title": "Far", "date": "06-01"},
            {"title": "Near", "date": "11-01"},
        )
        upcoming = mr.compute_upcoming_events(cfg, today=datetime.date(2026, 10, 23))
        self.assertEqual([u["title"] for u in upcoming], ["Near", "Far"])
        self.assertLess(upcoming[0]["days_left"], upcoming[1]["days_left"])

    def test_full_date_year_age_computation(self):
        cfg = self._cfg({"title": "Born", "date": "2020-11-06", "category": "birthday"})
        upcoming = mr.compute_upcoming_events(cfg, today=datetime.date(2026, 10, 23))
        self.assertEqual(upcoming[0]["years"], 6)
        self.assertEqual(upcoming[0]["milestone_str"], "Turns 6")


if __name__ == "__main__":
    unittest.main()
