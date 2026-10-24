#!/usr/bin/env python3
"""
Regression tests for v0.23.0 (REVIEW.md §4.4 / ITERATIONS v0.23): the optional
numpy RGB565 fast path in `ak820/screen.py`.

Hard invariants:
  1. Byte-identity — the numpy packer, the per-row fallback, and the original
     per-pixel reference loop must all produce the *same* 32768 bytes on the
     gradient + noise fixtures (numpy path skipped when numpy is absent).
  2. Import guard — numpy stays optional: `import numpy` sits in a guarded
     try/except and numpy-free installs keep the per-row path transparently.
  3. Dispatch — image_to_rgb565 routes through _rgb565_numpy only when the
     guarded import succeeded; the size guard fires on both paths.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from PIL import Image  # noqa: E402

import ak820_ctrl  # noqa: E402
from ak820 import screen  # noqa: E402
from test_screen import gradient_image, noise_image, reference_rgb565  # noqa: E402

HAS_NUMPY = screen._np is not None


@unittest.skipUnless(HAS_NUMPY, "numpy not installed: fast path unavailable")
class TestNumpyPathEquivalence(unittest.TestCase):
    def test_gradient_byte_identical_to_reference(self):
        img = gradient_image()
        self.assertEqual(screen._rgb565_numpy(img), reference_rgb565(img))

    def test_noise_byte_identical_to_reference(self):
        img = noise_image()
        self.assertEqual(screen._rgb565_numpy(img), reference_rgb565(img))

    def test_numpy_and_rows_paths_agree(self):
        for img in (gradient_image(), noise_image()):
            self.assertEqual(screen._rgb565_numpy(img), screen._rgb565_rows(img))

    def test_frame_length_and_endian(self):
        data = screen._rgb565_numpy(Image.new("RGB", (128, 128), (255, 0, 0)))
        self.assertEqual(len(data), screen.RGB565_FRAME_BYTES)
        self.assertEqual(data[:2], b"\x00\xF8")  # red 0xF800 little-endian


class TestFallbackPathEquivalence(unittest.TestCase):
    """The per-row path must stay byte-identical whether numpy is present or not."""

    def test_gradient_byte_identical_to_reference(self):
        img = gradient_image()
        with mock.patch.object(screen, "_np", None):
            self.assertEqual(screen.image_to_rgb565(img), reference_rgb565(img))

    def test_noise_byte_identical_to_reference(self):
        img = noise_image()
        with mock.patch.object(screen, "_np", None):
            self.assertEqual(screen.image_to_rgb565(img), reference_rgb565(img))

    def test_public_entry_matches_numpy_when_available(self):
        if not HAS_NUMPY:
            self.skipTest("numpy not installed")
        img = noise_image(seed=7)
        self.assertEqual(screen.image_to_rgb565(img), screen._rgb565_numpy(img))


class TestDispatch(unittest.TestCase):
    def test_numpy_present_routes_to_fast_path(self):
        img = Image.new("RGB", (128, 128), (1, 2, 3))
        sentinel = b"\xAB" * screen.RGB565_FRAME_BYTES
        with mock.patch.object(screen, "_np", object()), mock.patch.object(
            screen, "_rgb565_numpy", return_value=sentinel
        ) as fast:
            self.assertIs(screen.image_to_rgb565(img), sentinel)
        fast.assert_called_once()

    def test_numpy_absent_routes_to_row_path(self):
        img = Image.new("RGB", (128, 128), (1, 2, 3))
        sentinel = b"\xCD" * screen.RGB565_FRAME_BYTES
        with mock.patch.object(screen, "_np", None), mock.patch.object(
            screen, "_rgb565_rows", return_value=sentinel
        ) as slow:
            self.assertIs(screen.image_to_rgb565(img), sentinel)
        slow.assert_called_once()

    def test_size_guard_fires_on_both_paths(self):
        for np_state in (object(), None):
            with self.subTest(numpy=np_state is not None):
                with mock.patch.object(screen, "_np", np_state):
                    with self.assertRaises(ValueError):
                        screen.image_to_rgb565(Image.new("RGB", (64, 64)))


class TestImportGuard(unittest.TestCase):
    def test_numpy_import_is_guarded(self):
        src = (ROOT / "ak820" / "screen.py").read_text()
        self.assertRegex(src, r"try:[^\n]*\n\s*import numpy as _np\s*\nexcept ImportError")

    def test_numpy_not_a_hard_dependency(self):
        # pyproject must not list numpy; the guarded import is the contract.
        pyproject = (ROOT / "pyproject.toml").read_text()
        deps_block = pyproject.split("dependencies = [", 1)[1].split("]", 1)[0]
        self.assertNotIn("numpy", deps_block)

    def test_mypy_override_for_optional_numpy(self):
        # CI never installs numpy; without this override the gate would drift.
        pyproject = (ROOT / "pyproject.toml").read_text()
        self.assertIn('module = ["numpy", "numpy.*"]', pyproject)
        self.assertIn("ignore_missing_imports = true", pyproject)

    def test_shim_reexport_identity(self):
        self.assertIs(ak820_ctrl.image_to_rgb565, screen.image_to_rgb565)


if __name__ == "__main__":
    unittest.main()
