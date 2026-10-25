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
import re
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
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
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
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1: load_default takes no size
        return ImageFont.load_default()


def _load_font(paths, size: int):
    """First existing TTF from `paths` at `size`, cached via lru_cache (§4.2):
    `fit_text` probes up to 6 sizes per text element and --watch re-renders
    every refresh, so re-parsing the same TTF was pure waste."""
    return _load_font_cached(tuple(paths), size)


# m30 (v0.34.0): the mark that says "there was more". Every font in the catalogs
# above carries the glyph (locked by tests/test_minors_v034.py), and it is a
# single character, so the mark costs less width than "..." at every size.
ELLIPSIS = "…"

# m31 (v0.35.0): a single-line box must stay single-line. m30 gave `fit_text`
# horizontal containment but measured width across the whole string without
# asking how many lines it draws. PIL breaks on `\n`, so one embedded newline
# splits a fitted line in two; a *centred* box then centres on the total height
# and the custom card's value drew its first line ten px ABOVE the panel top
# (live-measured), while top-anchored boxes (board meta, hero subtitle) spilled
# below their border — silent, exit 0, the m30 class one axis deeper. The fix is
# at the single home of containment so every caller inherits it: line-breakers
# (every boundary `str.splitlines()` knows) flatten to a space before measuring.
# Newline-free text is returned byte-for-byte identical, so no prior render moves.
_LINE_BREAK_RE = re.compile("[\\n\\r\\v\\f\\x1c\\x1d\\x1e\\x85\\u2028\\u2029]+")
_WS_RUN_RE = re.compile(r"\s+")


def flatten_lines(text: str) -> str:
    """Collapse line-breaking characters to single spaces (m31, v0.35.0).

    Returns `text` unchanged when it holds no line break, so the fix is a strict
    no-op for every string that already rendered on one line.
    """
    if not text or not _LINE_BREAK_RE.search(text):
        return text
    return _WS_RUN_RE.sub(" ", _LINE_BREAK_RE.sub(" ", text)).strip()


def text_width(draw, text: str, font) -> float:
    """Drawn width of `text` in pixels (bbox, matching every renderer's own math).

    float because that is what PIL's `textbbox` reports; every renderer already
    compared those floats against its box budget.
    """
    if not text:
        return 0.0
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def ellipsize(draw, text: str, font, max_width: float) -> str:
    """Shorten `text` with a trailing `…` until it fits `max_width` at `font`.

    m30 (v0.34.0), the second half of the containment rule: shrinking a font has
    a floor, and below that floor the old behaviour was to draw the full string
    anyway and let the screen cut it off — a 66-char note measured 288 px drawn
    from x=11 into a 128 px panel, amputated mid-word and silent. An ellipsis is
    the difference between *truncated* and *silently lost*: the reader sees that
    text existed.

    Text that already fits comes back unchanged (byte-for-byte identical renders
    for every string that always fitted). Binary search on the joined candidate,
    so the cost is O(log n) measurements even for a pathological note.

    m31 (v0.35.0): the text is flattened to a single line first, so a box that
    fits one line can never be handed a two-line string by this primitive either.
    """
    text = flatten_lines(text)
    if not text or text_width(draw, text, font) <= max_width:
        return text
    if text_width(draw, ELLIPSIS, font) > max_width:
        return ""  # the box cannot even hold the mark; draw nothing rather than overflow
    lo, hi = 0, len(text) - 1  # the full string is known NOT to fit; "" + mark does
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if text_width(draw, text[:mid] + ELLIPSIS, font) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + ELLIPSIS


def fit_text(draw, text: str, paths: list[str], max_size: int, min_size: int, max_width: float):
    """Fit `text` into `max_width`: shrink the font, then ellipsize. Returns (font, text).

    The single home of the "never draw outside the box" rule for every 128x128
    renderer (m30, v0.34.0; lives in `screen` because it is a render_common
    primitive shared by `cards` and `hud`, like the font catalogs and `_load_font`).

    `fit_text_font` (the pre-v0.34 helper) shrank the font and stopped: at
    `min_size` it returned the font whether or not the text fitted, so every
    "fitted" call site could still overflow — measured live, the hero subtitle
    overflowed by 199 px and the custom card's value by 182 px at their minimum
    sizes. Containment is a property of the *pair* (font, text), so the pair is
    what this returns: a caller cannot apply the font and forget the text.

    Strings that fit at some size in [min_size, max_size] are returned verbatim
    with the same font the old helper picked, so existing renders stay
    byte-identical; only text that used to be clipped changes.

    m31 (v0.35.0): containment is on BOTH axes. The text is flattened to a single
    line first (see `flatten_lines`), so a single-line box can never be handed a
    multi-line string that escapes it vertically — the custom card's centred value
    drew its first line ten px above the panel before this. Newline-free text is
    untouched, so the byte-identical guarantee above still holds.
    """
    text = flatten_lines(text)
    for sz in range(max_size, min_size - 1, -1):
        font = _load_font(paths, sz)
        if text_width(draw, text, font) <= max_width:
            return font, text
    font = _load_font(paths, min_size)
    return font, ellipsize(draw, text, font, max_width)


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
