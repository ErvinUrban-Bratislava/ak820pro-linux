#!/usr/bin/env python3
"""
Regression tests for v0.8.0 (REVIEW.md §10.4): the RGB565 pipeline moved to
`ak820/screen.py` with an optimized per-row packer.

Hard invariant (§10.4 item 2): the optimized packer must be **byte-identical**
to the old per-pixel `getpixel` reference loop on fixed fixtures — gradient
covering the full RGB565 truncation boundaries and deterministic noise.
Also covers: fit_image sizes for all three modes, the m9 alpha-mask fix,
GIF round-trip through load_frames_from_file, and lru_cache font identity.
"""

import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image

import ak820_ctrl
from ak820 import screen
from ak820.protocol import SCREEN_HEIGHT, SCREEN_WIDTH


def reference_rgb565(img: Image.Image) -> bytes:
    """The pre-v0.8.0 per-pixel implementation, verbatim (equivalence oracle)."""
    img_rgb = img.convert("RGB")
    pixels = img_rgb.load()
    data = bytearray(SCREEN_WIDTH * SCREEN_HEIGHT * 2)
    for y in range(SCREEN_HEIGHT):
        for x in range(SCREEN_WIDTH):
            r, g, b = pixels[x, y]
            rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            offset = (y * SCREEN_WIDTH + x) * 2
            data[offset] = rgb565 & 0xFF
            data[offset + 1] = (rgb565 >> 8) & 0xFF
    return bytes(data)


def gradient_image() -> Image.Image:
    """Full-range gradient hitting every RGB565 truncation boundary."""
    img = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT))
    px = img.load()
    for y in range(SCREEN_HEIGHT):
        for x in range(SCREEN_WIDTH):
            px[x, y] = ((x * 2) % 256, (y * 2) % 256, (x * 251 + y * 97) % 256)
    return img


def noise_image(seed: int = 42) -> Image.Image:
    # Deterministic LCG — no RNG module needed, byte-stable across runs.
    out = bytearray()
    v = seed
    for _ in range(SCREEN_WIDTH * SCREEN_HEIGHT * 3):
        v = (v * 1103515245 + 12345) & 0x7FFFFFFF
        out.append(v & 0xFF)
    return Image.frombytes("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), bytes(out))


class TestRgb565ByteExactness(unittest.TestCase):
    def test_gradient_byte_identical_to_reference(self):
        img = gradient_image()
        self.assertEqual(screen.image_to_rgb565(img), reference_rgb565(img))

    def test_noise_byte_identical_to_reference(self):
        img = noise_image()
        self.assertEqual(screen.image_to_rgb565(img), reference_rgb565(img))

    def test_frame_length_and_endian(self):
        img = Image.new("RGB", (128, 128), (0xFF, 0xFF, 0xFF))
        data = screen.image_to_rgb565(img)
        self.assertEqual(len(data), screen.RGB565_FRAME_BYTES)
        self.assertEqual(len(data), SCREEN_WIDTH * SCREEN_HEIGHT * 2)
        # white -> 0xFFFF little-endian = FF FF
        self.assertEqual(data[:2], b"\xFF\xFF")
        # pure red -> 0xF800 -> little-endian 00 F8
        red = screen.image_to_rgb565(Image.new("RGB", (128, 128), (255, 0, 0)))
        self.assertEqual(red[:2], b"\x00\xF8")

    def test_non_rgb_mode_converted(self):
        rgba = Image.new("RGBA", (128, 128), (10, 20, 30, 128))
        self.assertEqual(screen.image_to_rgb565(rgba), screen.image_to_rgb565(rgba.convert("RGB")))

    def test_wrong_size_rejected(self):
        with self.assertRaises(ValueError):
            screen.image_to_rgb565(Image.new("RGB", (64, 64)))

    def test_shim_reexports_same_function(self):
        self.assertIs(ak820_ctrl.image_to_rgb565, screen.image_to_rgb565)
        self.assertIs(ak820_ctrl.fit_image, screen.fit_image)
        self.assertIs(ak820_ctrl.load_frames_from_file, screen.load_frames_from_file)


class TestFitImage(unittest.TestCase):
    def test_all_modes_return_128x128(self):
        src = Image.new("RGB", (200, 100), (200, 30, 30))
        for mode in ("fill", "contain", "stretch"):
            out = screen.fit_image(src, mode)
            self.assertEqual(out.size, (128, 128), f"mode={mode}")
            self.assertEqual(out.mode, "RGB")

    def test_contain_letterboxes_wide_image(self):
        src = Image.new("RGB", (256, 64), (255, 255, 255))
        out = screen.fit_image(src, "contain")
        # 256x64 -> thumbnail 128x32 centered: top/bottom bands stay black.
        self.assertEqual(out.getpixel((64, 2)), (0, 0, 0))
        self.assertEqual(out.getpixel((64, 125)), (0, 0, 0))
        self.assertEqual(out.getpixel((64, 64)), (255, 255, 255))

    def test_m9_contain_uses_alpha_mask(self):
        """m9: fully transparent RGBA must composite as black background,
        not paste its raw RGB values over it."""
        src = Image.new("RGBA", (64, 64), (255, 0, 0, 0))  # invisible red
        out = screen.fit_image(src, "contain")
        self.assertEqual(out.getpixel((64, 64)), (0, 0, 0))

    def test_m9_semi_transparent_blends(self):
        src = Image.new("RGBA", (64, 64), (255, 0, 0, 128))  # ~50% red on black
        out = screen.fit_image(src, "contain")
        r, g, b = out.getpixel((64, 64))
        self.assertTrue(90 < r < 165, f"expected ~128 red blend, got {(r, g, b)}")
        self.assertEqual((g, b), (0, 0))


class TestGifRoundTrip(unittest.TestCase):
    def _write_gif(self, path: Path, n: int = 3, duration: int = 100):
        frames = [Image.new("RGB", (128, 128), (i * 60 % 256, 20, 40)) for i in range(n)]
        frames[0].save(
            path, save_all=True, append_images=frames[1:], duration=duration, loop=0
        )

    def test_three_frame_gif_round_trip(self):
        with self.subTest("tmpdir"):
            import tempfile

            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / "anim.gif"
                self._write_gif(p, n=3, duration=100)
                frames, delays = screen.load_frames_from_file(str(p))
                self.assertEqual(len(frames), 3)
                self.assertTrue(all(len(f) == screen.RGB565_FRAME_BYTES for f in frames))
                self.assertEqual(len(delays), 3)
                self.assertTrue(all(d >= 20 for d in delays))

    def test_static_image_single_frame_no_delays(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "still.png"
            Image.new("RGB", (300, 200), (9, 99, 199)).save(p)
            frames, delays = screen.load_frames_from_file(str(p))
            self.assertEqual(len(frames), 1)
            self.assertIsNone(delays)
            self.assertEqual(len(frames[0]), screen.RGB565_FRAME_BYTES)

    def test_max_frames_cap(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "anim.gif"
            self._write_gif(p, n=5)
            frames, delays = screen.load_frames_from_file(str(p), max_frames=2)
            self.assertEqual(len(frames), 2)
            self.assertEqual(len(delays), 2)


class TestFontCache(unittest.TestCase):
    def test_lru_cache_returns_same_object(self):
        f1 = screen._load_font(screen.FONT_BOLD_PATHS, 10)
        f2 = screen._load_font(screen.FONT_BOLD_PATHS, 10)
        self.assertIs(f1, f2)  # §4.2: cached, not re-parsed

    def test_accepts_tuple_or_list(self):
        a = screen._load_font(list(screen.FONT_MONO_PATHS), 8)
        b = screen._load_font(tuple(screen.FONT_MONO_PATHS), 8)
        self.assertIs(a, b)

    def test_renderers_share_render_common(self):
        import milestone_radar
        import widgets

        self.assertIs(widgets._load_font, screen._load_font)
        self.assertIs(milestone_radar._load_font, screen._load_font)
        self.assertIs(widgets.SCREEN_W, screen.SCREEN_W)
        self.assertIs(milestone_radar.FONT_BOLD_PATHS, screen.FONT_BOLD_PATHS)


class TestLayering(unittest.TestCase):
    def test_ctrl_no_longer_imports_milestone_radar_for_dedup(self):
        text = (ROOT / "ak820_ctrl.py").read_text()
        # upload_frames_to_screen must not reach into milestone_radar anymore.
        self.assertNotIn("mr.check_and_record_payload_hash", text)
        self.assertNotIn("mr.save_payload_hash", text)

    def test_screen_module_size(self):
        lines = (ROOT / "ak820" / "screen.py").read_text().splitlines()
        self.assertLess(len(lines), 300)
        lines = (ROOT / "ak820" / "state.py").read_text().splitlines()
        self.assertLess(len(lines), 300)


if __name__ == "__main__":
    unittest.main()
