"""HUD + Now-Playing card renderers for the 128x128 TFT (v0.25.0).

Split out of `widgets.py` under the §10 modularity budget. This module owns the
*pixel* half of the widget domain — the cyberpunk system HUD card and the media
player card. Both renderers are pure functions of the dicts produced by
`ak820.telemetry` (stats/media are injectable, so rendering is offline-testable
with zero sysfs or D-Bus access).

`widgets.py` re-exports both renderers unchanged (identity, not copy).
"""

from PIL import Image, ImageDraw

from .screen import (
    FONT_BOLD_PATHS,
    FONT_MONO_PATHS,
    FONT_REGULAR_PATHS,
    SCREEN_H,
    SCREEN_W,
    _load_font,
    ellipsize,
    fit_text,
    flatten_lines,
    text_width,
)

# m31b (v0.36.0) layout budgets. Each is derived from the element the text is
# actually drawn next to — never from a character count, the proxy m30 rejected
# (measured live: the old 16-char title cap drew 182 px of wide glyphs from x=8
# into a 128 px screen, amputated mid-glyph and silent).
MEDIA_TEXT_W = SCREEN_W - 1 - 8  # 119: outer border x=127 minus the text x of 8
HUD_UPTIME_W = SCREEN_W - 7 - 71  # 50: uptime card's right border x=121 minus text x=71
HUD_HOST_GAP = 4  # clean air kept between the hostname and the clock


def _greedy_words(draw, words: list[str], font, budget: float) -> list[str]:
    """The longest prefix of `words` that fits one measured line (m31b).

    The first word always lands, even when it alone exceeds the budget: the
    caller ellipsizes it, which MARKS the overflow. Dropping it is what the
    old char-count loop did — a title with no word boundary inside its first
    14 chars left line 1 empty and truncated line 2 mid-word, losing the
    tail silently.
    """
    line: list[str] = []
    for w in words:
        if line and text_width(draw, " ".join([*line, w]), font) > budget:
            break
        line.append(w)
    return line


def _wrap_media_title(draw, title: str, font, budget: float) -> list[str]:
    """Measured one/two-line layout for the media title (m31b, v0.36.0).

    One line when the (flattened) title fits; otherwise two greedily-filled
    measured lines. Content that cannot fit on either line is never dropped:
    the last drawn line carries `screen.ellipsize`'s mark instead. Titles
    that fitted before render byte-identically — the wrap decision moved
    from a character count to the pixels the box actually holds.
    """
    title = flatten_lines(title)
    if text_width(draw, title, font) <= budget:
        return [title]
    words = title.split()
    line1 = _greedy_words(draw, words, font, budget)
    rest = words[len(line1) :]
    line2 = _greedy_words(draw, rest, font, budget)
    dropped = rest[len(line2) :]
    first = ellipsize(draw, " ".join(line1), font, budget)
    if not rest:
        return [first]  # one over-long word: marked on its own line, not dropped
    return [first, ellipsize(draw, " ".join([*line2, *dropped]), font, budget)]


def render_hud_card(stats: dict | None = None) -> Image.Image:
    """Render a 128x128 cyberpunk PC status HUD card."""
    if stats is None:
        from . import telemetry

        stats = telemetry.get_system_stats()

    img = Image.new("RGB", (SCREEN_W, SCREEN_H), (10, 14, 22))
    draw = ImageDraw.Draw(img)

    # Fonts
    font_mono_s = _load_font(FONT_MONO_PATHS, 9)
    font_mono_xs = _load_font(FONT_MONO_PATHS, 8)
    font_bold_s = _load_font(FONT_BOLD_PATHS, 10)
    font_bold_m = _load_font(FONT_BOLD_PATHS, 13)

    # 1. Header Bar: Hostname + Clock
    draw.rectangle([0, 0, SCREEN_W - 1, 17], fill=(16, 24, 38))
    draw.line([0, 17, SCREEN_W - 1, 17], fill=(0, 229, 255))
    # m31b (v0.36.0): the hostname was drawn unbounded — a long node name slid
    # under (and past) the clock, live-measured cyan paint at x=125 vs a clock
    # starting at x=98. Its budget is derived from the clock's measured start
    # x, the element it is actually drawn next to. clock_str itself stays
    # exempt: telemetry formats it "%H:%M", bounded by construction like the
    # board card's days pill (cards.py documents the same exemption class).
    clock_str = stats.get("time", "")
    c_bbox = draw.textbbox((0, 0), clock_str, font=font_mono_s)
    clock_x = SCREEN_W - 4 - (c_bbox[2] - c_bbox[0])
    f_host, host_s = fit_text(
        draw, stats["hostname"], FONT_BOLD_PATHS, 10, 8, clock_x - 4 - HUD_HOST_GAP
    )
    draw.text((4, 3), host_s, fill=(0, 229, 255), font=f_host)
    draw.text((clock_x, 4), clock_str, fill=(240, 246, 252), font=font_mono_s)

    # 2. CPU Metric & Gauge (y: 22..46)
    cpu = stats["cpu_pct"]
    draw.text((6, 22), "CPU", fill=(148, 163, 184), font=font_mono_xs)
    cpu_str = f"{cpu:.0f}%"
    val_box = draw.textbbox((0, 0), cpu_str, font=font_bold_s)
    draw.text(
        (SCREEN_W - 6 - (val_box[2] - val_box[0]), 21),
        cpu_str,
        fill=(56, 189, 248),
        font=font_bold_s,
    )

    # CPU bar
    draw.rectangle([6, 35, SCREEN_W - 7, 40], fill=(22, 32, 48), outline=(30, 48, 72))
    fill_w = int((SCREEN_W - 14) * (min(cpu, 100.0) / 100.0))
    if fill_w > 0:
        bar_color = (239, 68, 68) if cpu >= 85 else (245, 158, 11) if cpu >= 60 else (56, 189, 248)
        draw.rectangle([7, 36, 6 + fill_w, 39], fill=bar_color)

    # 3. RAM Metric & Gauge (y: 47..72)
    ram = stats["ram_pct"]
    draw.text((6, 47), "RAM", fill=(148, 163, 184), font=font_mono_xs)
    ram_str = f"{stats['ram_used_gb']:.0f}/{stats['ram_total_gb']:.0f}G ({ram:.0f}%)"
    r_box = draw.textbbox((0, 0), ram_str, font=font_mono_xs)
    draw.text(
        (SCREEN_W - 6 - (r_box[2] - r_box[0]), 47), ram_str, fill=(192, 132, 252), font=font_mono_xs
    )

    # RAM bar
    draw.rectangle([6, 59, SCREEN_W - 7, 64], fill=(22, 32, 48), outline=(30, 48, 72))
    r_fill_w = int((SCREEN_W - 14) * (min(ram, 100.0) / 100.0))
    if r_fill_w > 0:
        r_bar_color = (239, 68, 68) if ram >= 90 else (192, 132, 252)
        draw.rectangle([7, 60, 6 + r_fill_w, 63], fill=r_bar_color)

    # 4. Split Cards: TEMP (left) & UPTIME (right) (y: 71..112)
    # Left Card: Temp
    draw.rectangle([6, 71, 60, 111], fill=(16, 24, 38), outline=(30, 48, 72))
    draw.text((10, 75), "TEMP", fill=(148, 163, 184), font=font_mono_xs)
    if stats["temp_c"] is not None:
        t_val = stats["temp_c"]
        t_color = (
            (239, 68, 68) if t_val >= 80 else (245, 158, 11) if t_val >= 65 else (52, 211, 153)
        )
        t_str = f"{t_val:.0f}°C"
        draw.text((10, 89), t_str, fill=t_color, font=font_bold_m)
    else:
        draw.text((10, 89), "N/A", fill=(148, 163, 184), font=font_bold_m)

    # Right Card: Uptime
    draw.rectangle([66, 71, SCREEN_W - 7, 111], fill=(16, 24, 38), outline=(30, 48, 72))
    draw.text((71, 75), "UPTIME", fill=(148, 163, 184), font=font_mono_xs)
    # m31b (v0.36.0): a 5-digit-day uptime (54 px measured) crossed this card's
    # right border at x=121 — painted to x=123, live. The string now fits the
    # card itself: shrink first, mark only when shrinking cannot save it.
    f_up, up_s = fit_text(draw, stats["uptime"], FONT_BOLD_PATHS, 10, 8, HUD_UPTIME_W)
    draw.text((71, 91), up_s, fill=(251, 191, 36), font=f_up)

    # 5. Bottom Frame Border
    draw.rectangle([0, 0, SCREEN_W - 1, SCREEN_H - 1], outline=(30, 48, 72))
    draw.rectangle([0, SCREEN_H - 14, SCREEN_W - 1, SCREEN_H - 1], fill=(14, 20, 32))
    draw.text((6, SCREEN_H - 11), "AJAZZ AK820 PRO", fill=(71, 85, 105), font=font_mono_xs)

    return img


def render_media_card(media: dict | None = None) -> Image.Image:
    """Render a 128x128 Now Playing music/media card."""
    if media is None:
        from . import telemetry

        media = telemetry.get_media_status()

    img = Image.new("RGB", (SCREEN_W, SCREEN_H), (12, 10, 18))
    draw = ImageDraw.Draw(img)

    font_bold_m = _load_font(FONT_BOLD_PATHS, 12)
    font_bold_s = _load_font(FONT_BOLD_PATHS, 10)
    font_mono_xs = _load_font(FONT_MONO_PATHS, 8)
    # m31b: the artist line's size-9 regular font is now loaded by fit_text —
    # the renderer no longer draws that string with a font it picked itself.

    is_playing = media.get("status") == "Playing"
    is_paused = media.get("status") == "Paused"

    # Header Bar
    if is_playing:
        header_bg = (16, 185, 129)
        header_fg = (6, 24, 16)
        tag = "▶ PLAYING"
    elif is_paused:
        header_bg = (245, 158, 11)
        header_fg = (24, 16, 6)
        tag = "⏸ PAUSED"
    else:
        header_bg = (30, 41, 59)
        header_fg = (148, 163, 184)
        tag = "♫ MEDIA"

    draw.rectangle([0, 0, SCREEN_W - 1, 18], fill=header_bg)
    draw.text((6, 3), tag, fill=header_fg, font=font_bold_s)

    # Title box (y: 26..70). m31b (v0.36.0): the old caps were character counts
    # — a 16-char title at size 12 measured 182 px from x=8 into a 128 px
    # screen, and the wrap loop dropped line 1 entirely for a title with no
    # word boundary in its first 14 chars. Every title is now measured: one
    # fitted line, or two greedy lines whose tail is marked instead of amputated.
    title_lines = _wrap_media_title(draw, media.get("title", "No Track"), font_bold_m, MEDIA_TEXT_W)
    if len(title_lines) == 1:
        draw.text((8, 36), title_lines[0], fill=(255, 255, 255), font=font_bold_m)
    else:
        draw.text((8, 28), title_lines[0], fill=(255, 255, 255), font=font_bold_m)
        draw.text((8, 46), title_lines[1], fill=(255, 255, 255), font=font_bold_m)

    # Artist line (y: 72..88). m31b: the old 20-char cap was the same
    # char-count proxy; the size stays 9 so every artist that already fitted
    # renders byte-identically, and overflow is now marked inside the card.
    f_artist, artist_s = fit_text(
        draw, media.get("artist", ""), FONT_REGULAR_PATHS, 9, 9, MEDIA_TEXT_W
    )
    draw.text((8, 72), artist_s, fill=(0, 229, 255), font=f_artist)

    # Album / Progress line (y: 90..106)
    album = media.get("album", "")
    if album:
        f_album, album_s = fit_text(draw, album, FONT_MONO_PATHS, 8, 8, MEDIA_TEXT_W)
        draw.text((8, 88), album_s, fill=(148, 163, 184), font=f_album)
    else:
        draw.text((8, 88), "AK820 Pro Display", fill=(71, 85, 105), font=font_mono_xs)

    # Bottom status bar
    draw.rectangle([0, SCREEN_H - 14, SCREEN_W - 1, SCREEN_H - 1], fill=(18, 14, 28))
    draw.line([0, SCREEN_H - 14, SCREEN_W - 1, SCREEN_H - 14], fill=(45, 30, 60))
    state_label = "NOW PLAYING" if is_playing else "PAUSED" if is_paused else "IDLE"
    draw.text((8, SCREEN_H - 11), state_label, fill=(148, 163, 184), font=font_mono_xs)

    # Outer border
    draw.rectangle([0, 0, SCREEN_W - 1, SCREEN_H - 1], outline=(45, 30, 60))

    return img
