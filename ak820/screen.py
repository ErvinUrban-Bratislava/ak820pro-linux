"""Screen imaging pipeline (v0.8.0, REVIEW.md §10.4): RGB565 packing, fit/crop,
GIF frame loading, and the shared render_common symbols (m8) that widgets.py
and milestone_radar.py used to duplicate verbatim.

Dependency rule (§10.2): screen -> {protocol, errors} only — imaging never
touches device/transport/state.

RGB565 packing is the optimized per-row form (§10.4 item 2): `img.tobytes()`
sliced per row + `struct` little-endian packing instead of the old per-pixel
`pixels[x, y]` double loop. Output is byte-identical to the reference loop
(proven by tests/test_screen.py on gradient + noise fixtures).

v0.23.0 (§4.4): when numpy is importable, a vectorized packer replaces the
per-row loop behind the same `image_to_rgb565` signature. numpy stays an
*optional* accelerator — the dependency-light positioning is a feature — so
the import is guarded and numpy-free installs silently keep the per-row path.
Both paths are byte-identical to the reference loop (tests/test_rgb565_numpy_v023.py).
"""

import functools
import os
import struct

from PIL import Image, ImageFont, ImageOps

from .protocol import MAX_FRAMES, SCREEN_HEIGHT, SCREEN_WIDTH

try:  # optional accelerator (§4.4): never a hard dependency
    import numpy as _np
except ImportError:  # pragma: no cover — numpy-free installs take the row path
    _np = None

# render_common aliases (m8): one set of screen constants for all renderers.
SCREEN_W = SCREEN_WIDTH
SCREEN_H = SCREEN_HEIGHT

FONT_BOLD_PATHS = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
FONT_NARROW_PATHS = [
    "/usr/share/fonts/truetype/liberation/LiberationSansNarrow-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
FONT_MONO_PATHS = [
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
]
FONT_REGULAR_PATHS = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

_U16 = struct.Struct("<H")

RGB565_FRAME_BYTES = SCREEN_WIDTH * SCREEN_HEIGHT * 2


@functools.lru_cache(maxsize=128)
def _load_font_cached(paths: tuple, size: int):
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _load_font(paths, size: int):
    """First existing TTF from `paths` at `size`, cached via lru_cache (§4.2):
    _fit_text_font probes up to 6 sizes per text element and --watch re-renders
    every refresh, so re-parsing the same TTF was pure waste."""
    return _load_font_cached(tuple(paths), size)


def _rgb565_rows(rgb) -> bytes:
    """Per-row struct packer (v0.8.0, §10.4 item 2) — the numpy-free fallback."""
    raw = rgb.tobytes()
    row_in, row_out = SCREEN_WIDTH * 3, SCREEN_WIDTH * 2
    pack = _U16.pack
    out = bytearray(row_out * SCREEN_HEIGHT)
    for y in range(SCREEN_HEIGHT):
        row = raw[y * row_in : (y + 1) * row_in]
        out[y * row_out : (y + 1) * row_out] = b"".join(
            pack(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3))
            for r, g, b in zip(row[0::3], row[1::3], row[2::3])
        )
    return bytes(out)


def _rgb565_numpy(rgb) -> bytes:
    """Vectorized packer (v0.23.0, §4.4), byte-identical to _rgb565_rows:
    (r & 0xF8) << 8 == (r >> 3) << 11, (g & 0xFC) << 3 == (g >> 2) << 5,
    b >> 3 unchanged. uint16 intermediates (uint8 << 11 would overflow);
    astype("<u2") emits the same little-endian byte order as _U16.pack."""
    a = (
        _np.frombuffer(rgb.tobytes(), dtype=_np.uint8)
        .reshape(SCREEN_HEIGHT, SCREEN_WIDTH, 3)
        .astype(_np.uint16)
    )
    packed = ((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)
    return packed.astype("<u2").tobytes()


def image_to_rgb565(img) -> bytes:
    """Convert a 128x128 PIL Image to RGB565 little-endian bytes (32768 bytes).

    Byte-identical to the reference per-pixel loop: rgb565 =
    ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3), low byte first.
    Takes the numpy fast path when available (§4.4), else the per-row loop.
    """
    rgb = img.convert("RGB")
    if rgb.size != (SCREEN_WIDTH, SCREEN_HEIGHT):
        raise ValueError(
            f"image_to_rgb565 expects a {SCREEN_WIDTH}x{SCREEN_HEIGHT} image, "
            f"got {rgb.size[0]}x{rgb.size[1]} (fit it first with fit_image())."
        )
    if _np is not None:
        return _rgb565_numpy(rgb)
    return _rgb565_rows(rgb)


def fit_image(img, mode: str = "fill"):
    """Resize/crop PIL image to 128x128 according to fit mode."""
    if mode == "stretch":
        return img.resize((SCREEN_WIDTH, SCREEN_HEIGHT), Image.Resampling.LANCZOS)
    elif mode == "contain":
        copy = img.copy()
        copy.thumbnail((SCREEN_WIDTH, SCREEN_HEIGHT), Image.Resampling.LANCZOS)
        out = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), (0, 0, 0))
        ox = (SCREEN_WIDTH - copy.width) // 2
        oy = (SCREEN_HEIGHT - copy.height) // 2
        # m9: pass the image itself as the alpha mask so semi-transparent
        # pixels composite over the black background instead of overwriting
        # it. Plain RGB sources have no alpha band to use as a mask.
        mask = copy if copy.mode in ("RGBA", "LA", "PA") else None
        out.paste(copy, (ox, oy), mask)
        return out
    else:  # fill / center-crop
        return ImageOps.fit(img, (SCREEN_WIDTH, SCREEN_HEIGHT), method=Image.Resampling.LANCZOS)


def load_frames_from_file(path: str, fit_mode: str = "fill", max_frames: int = MAX_FRAMES):
    """Load static image or multi-frame GIF into RGB565 frames and delays (ms)."""
    img = Image.open(path)
    n_frames = getattr(img, "n_frames", 1)

    if n_frames <= 1:
        fitted = fit_image(img.convert("RGB"), fit_mode)
        return [image_to_rgb565(fitted)], None

    frames: list = []
    delays: list = []
    canvas = Image.new("RGBA", img.size, (0, 0, 0, 255))

    try:
        while len(frames) < max_frames:
            previous_canvas = canvas.copy()
            frame_rgba = img.copy().convert("RGBA")
            canvas.paste(frame_rgba, (0, 0), frame_rgba)

            output = fit_image(canvas.convert("RGB"), fit_mode)
            frames.append(image_to_rgb565(output))

            delay = img.info.get("duration", 100) or 100
            delays.append(max(int(delay), 20))

            disposal = getattr(img, "disposal_method", 0)
            if disposal == 2:
                canvas = Image.new("RGBA", img.size, (0, 0, 0, 255))
            elif disposal == 3:
                canvas = previous_canvas

            img.seek(img.tell() + 1)
    except EOFError:
        pass

    return frames, delays
