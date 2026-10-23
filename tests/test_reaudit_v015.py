#!/usr/bin/env python3
"""v0.15.0 comprehensive re-audit regressions (REVIEW.md §14): D10 + D11.

D10: `radar --date <invalid>` leaked a raw ValueError traceback from
     `date.fromisoformat` — D4 validated `events add --date` but never the
     `--date` override path (live/preview/dry-run). Now ConfigError -> exit 6.
D11: `upload <missing>` and `--dry-run upload <missing>` leaked a raw
     FileNotFoundError traceback. Now FileMissingError -> exit 8.

All offline, zero hardware, zero ~/.cache writes (state path untouched by
preview/dry-run paths by construction).
"""

import datetime
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import milestone_radar as mr
from ak820 import cli
from ak820.actions import upload_to_screen
from ak820.config import today_from_args
from ak820.errors import FileMissingError


class _Args:
    """Minimal argparse.Namespace stand-in for direct helper calls."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class TestDateOverrideValidation(unittest.TestCase):
    """D10: `--date` must fail as ConfigError (exit 6), never a traceback."""

    def test_valid_date_parses(self):
        self.assertEqual(today_from_args(_Args(date="2028-02-29")), datetime.date(2028, 2, 29))

    def test_non_leap_feb29_rejected(self):
        # 2026 is not a leap year — fromisoformat must reject Feb 29 (exit 6).
        with self.assertRaises(mr.ConfigError):
            today_from_args(_Args(date="2026-02-29"))

    def test_absent_date_is_real_today(self):
        self.assertEqual(today_from_args(_Args(date=None)), datetime.date.today())

    def test_garbage_date_raises_config_error(self):
        with self.assertRaises(mr.ConfigError) as ctx:
            today_from_args(_Args(date="banana"))
        self.assertIn("YYYY-MM-DD", str(ctx.exception))

    def test_impossible_calendar_date_rejected(self):
        # fromisoformat enforces real calendar days: Feb 30 must not slip through.
        with self.assertRaises(mr.ConfigError):
            today_from_args(_Args(date="2026-02-30"))

    def test_wrong_format_rejected(self):
        with self.assertRaises(mr.ConfigError):
            today_from_args(_Args(date="24-10-2026"))

    def test_cli_radar_preview_bad_date_exits_6(self):
        with tempfile.TemporaryDirectory() as td:
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as ctx:
                    cli.main(["radar", "--date", "banana", "--save-preview", td])
        self.assertEqual(ctx.exception.code, 6)
        self.assertIn("Invalid --date", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())

    def test_cli_dry_run_radar_bad_date_exits_6(self):
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["--dry-run", "radar", "--date", "2026-13-01"])
        self.assertEqual(ctx.exception.code, 6)
        self.assertIn("Invalid --date", err.getvalue())


class TestMissingFileError(unittest.TestCase):
    """D11: a bad upload path is a typed exit-8 error, never a traceback."""

    def test_exit_code_is_8(self):
        self.assertEqual(FileMissingError.exit_code, 8)

    def test_is_ak820_error_subclass(self):
        from ak820.errors import AK820Error

        self.assertTrue(issubclass(FileMissingError, AK820Error))

    def test_upload_to_screen_raises_typed_error_before_touching_device(self):
        # dev=None proves the existence check happens before any device use.
        with self.assertRaises(FileMissingError) as ctx:
            upload_to_screen(None, "/nonexistent/dir/definitely-not-here.png")
        self.assertIn("File not found", str(ctx.exception))

    def test_cli_dry_run_upload_missing_exits_8(self):
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["--dry-run", "upload", "/nonexistent/dir/x.png"])
        self.assertEqual(ctx.exception.code, 8)
        self.assertIn("File not found", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())

    def test_main_maps_file_missing_to_exit_8(self):
        with mock.patch.object(cli, "_dispatch", side_effect=FileMissingError("boom")), \
             redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["probe"])
        self.assertEqual(ctx.exception.code, 8)

    def test_shim_reexports_file_missing_error(self):
        import ak820_ctrl

        self.assertIs(ak820_ctrl.FileMissingError, FileMissingError)


if __name__ == "__main__":
    unittest.main()
