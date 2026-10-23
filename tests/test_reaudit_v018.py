#!/usr/bin/env python3
"""v0.18.0 comprehensive re-audit regressions (REVIEW.md §17).

D12: `upload <non-image|directory|corrupt>` leaked raw PIL/OSError tracebacks
     -> typed InvalidFileError (exit 9), live and --dry-run paths identical.
D13: `events remove <no-match>` printed "Removed 0 event(s)." and exited 0,
     rewriting the config for nothing -> ConfigError (exit 6), file untouched.
D14: `--save-preview` into a missing parent dir leaked FileNotFoundError; a
     target colliding with an existing file/dir leaked FileExistsError /
     IsADirectoryError -> parents auto-created, unusable targets exit 9.
Version drift: ak820.__version__ (0.9.0) vs pyproject (1.0.0) vs tags -> locked
     equal by test.

All offline, zero hardware.
"""

import argparse
import io
import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ak820
import ak820_ctrl
import milestone_radar as mr
from ak820 import cli
from ak820.actions import load_frames_guarded, upload_to_screen
from ak820.config import cmd_events, cmd_hud_preview, cmd_media_preview
from ak820.errors import InvalidFileError


def _make_png(path: str, size=(64, 64)) -> str:
    from PIL import Image

    Image.new("RGB", size, (10, 20, 30)).save(path)
    return path


class TestD12InvalidUploadFile(unittest.TestCase):
    """Non-image / directory / corrupt input becomes InvalidFileError (exit 9)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.txt = os.path.join(self.tmp, "notes.txt")
        with open(self.txt, "w") as f:
            f.write("not an image")

    def test_exit_code_is_nine(self):
        self.assertEqual(InvalidFileError.exit_code, 9)

    def test_non_image_raises_typed_error(self):
        with self.assertRaises(InvalidFileError):
            load_frames_guarded(self.txt)

    def test_directory_raises_typed_error(self):
        with self.assertRaises(InvalidFileError):
            load_frames_guarded(self.tmp)

    def test_corrupt_image_raises_typed_error(self):
        bad = os.path.join(self.tmp, "bad.png")
        with open(bad, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"garbage" * 40)  # PNG magic, junk body
        with self.assertRaises(InvalidFileError):
            load_frames_guarded(bad)

    def test_upload_to_screen_fails_before_touching_device(self):
        # dev is never used: the load guard fires first, so None proves it.
        with self.assertRaises(InvalidFileError):
            upload_to_screen(None, self.txt)

    def test_cli_dry_run_upload_non_image_exits_9(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["--dry-run", "upload", self.txt])
        self.assertEqual(ctx.exception.code, 9)

    def test_cli_dry_run_upload_directory_exits_9(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["--dry-run", "upload", self.tmp])
        self.assertEqual(ctx.exception.code, 9)

    def test_valid_png_still_loads(self):
        png = _make_png(os.path.join(self.tmp, "ok.png"))
        frames, delays = load_frames_guarded(png)
        self.assertEqual(len(frames), 1)
        self.assertEqual(len(frames[0]), 128 * 128 * 2)
        self.assertIsNone(delays)

    def test_shim_and_package_reexport_identity(self):
        self.assertIs(ak820_ctrl.InvalidFileError, InvalidFileError)
        self.assertIs(ak820.InvalidFileError, InvalidFileError)


class TestD13EventsRemoveNoMatch(unittest.TestCase):
    """A no-match remove is a friendly exit 6 and never rewrites the config."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg_path = os.path.join(self.tmp, "events.json")
        self.cfg = {
            "events": [
                {"id": "daughter", "title": "Daughter Birthday", "date": "06-01"},
            ]
        }
        with open(self.cfg_path, "w") as f:
            json.dump(self.cfg, f)
        self.env = mock.patch.dict(os.environ, {"AK820_EVENTS_PATH": self.cfg_path})
        self.env.start()
        self.addCleanup(self.env.stop)

    def _remove(self, target):
        return argparse.Namespace(config=None, events_action="remove", target=target)

    def test_no_match_raises_config_error(self):
        with self.assertRaises(mr.ConfigError):
            cmd_events(self._remove("ghost"))

    def test_no_match_cli_exits_6(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["events", "remove", "ghost"])
        self.assertEqual(ctx.exception.code, 6)

    def test_no_match_leaves_file_untouched(self):
        before = Path(self.cfg_path).read_bytes()
        with self.assertRaises(mr.ConfigError):
            cmd_events(self._remove("ghost"))
        self.assertEqual(Path(self.cfg_path).read_bytes(), before)

    def test_remove_by_id_still_works(self):
        with redirect_stdout(io.StringIO()):
            rc = cmd_events(self._remove("daughter"))
        self.assertEqual(rc, 0)
        cfg = json.loads(Path(self.cfg_path).read_text())
        self.assertEqual(cfg["events"], [])

    def test_remove_by_title_case_insensitive(self):
        with redirect_stdout(io.StringIO()):
            rc = cmd_events(self._remove("DAUGHTER BIRTHDAY"))
        self.assertEqual(rc, 0)
        cfg = json.loads(Path(self.cfg_path).read_text())
        self.assertEqual(cfg["events"], [])


class TestD14PreviewTargetPaths(unittest.TestCase):
    """Preview parents are auto-created; unusable targets exit 9 friendly."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"AK820_EVENTS_PATH": os.path.join(self.tmp, "ev.json")})
        self.env.start()
        with open(os.environ["AK820_EVENTS_PATH"], "w") as f:
            json.dump({"events": [{"id": "x", "title": "X", "date": "06-01"}]}, f)
        self.addCleanup(self.env.stop)

    def test_hud_preview_creates_missing_parents(self):
        out = os.path.join(self.tmp, "deep", "nested", "hud.png")
        with redirect_stdout(io.StringIO()):
            rc = cmd_hud_preview(argparse.Namespace(save_preview=out))
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(out))

    def test_media_preview_creates_missing_parents(self):
        out = os.path.join(self.tmp, "a", "b", "media.png")
        with redirect_stdout(io.StringIO()):
            rc = cmd_media_preview(argparse.Namespace(save_preview=out))
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(out))

    def test_hud_preview_directory_target_exits_9(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["hud", "--save-preview", self.tmp])
        self.assertEqual(ctx.exception.code, 9)

    def test_radar_preview_onto_existing_file_exits_9(self):
        clash = os.path.join(self.tmp, "clash")
        with open(clash, "w") as f:
            f.write("occupied")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["radar", "--save-preview", clash])
        self.assertEqual(ctx.exception.code, 9)

    def test_radar_preview_nested_dir_still_works(self):
        out = os.path.join(self.tmp, "prev", "dir")
        with redirect_stdout(io.StringIO()):
            rc = cli.main(["radar", "--save-preview", out])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(os.path.join(out, "radar_hero.png")))
        self.assertTrue(os.path.exists(os.path.join(out, "radar_board.png")))


class TestVersionSync(unittest.TestCase):
    """ak820.__version__ and pyproject [project].version must never drift."""

    def test_package_version_matches_pyproject(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
        self.assertIsNotNone(m, "pyproject lost its [project] version")
        self.assertEqual(
            ak820.__version__, m.group(1),
            f"ak820.__version__={ak820.__version__} != pyproject {m.group(1)}",
        )


if __name__ == "__main__":
    unittest.main()
