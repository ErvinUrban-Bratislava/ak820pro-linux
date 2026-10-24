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
)


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
    draw.text((4, 3), stats["hostname"], fill=(0, 229, 255), font=font_bold_s)
    clock_str = stats.get("time", "")
    c_bbox = draw.textbbox((0, 0), clock_str, font=font_mono_s)
    draw.text(
        (SCREEN_W - 4 - (c_bbox[2] - c_bbox[0]), 4),
        clock_str,
        fill=(240, 246, 252),
        font=font_mono_s,
    )

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
    draw.text((71, 91), stats["uptime"], fill=(251, 191, 36), font=font_bold_s)

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
    font_reg_s = _load_font(FONT_REGULAR_PATHS, 9)

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

    # Title box (y: 26..70)
    title = media.get("title", "No Track")
    # Wrap title if long
    if len(title) > 16:
        # 2 lines
        words = title.split()
        l1: list[str] = []
        l2: list[str] = []
        for w in words:
            if len(" ".join([*l1, w])) <= 14:
                l1.append(w)
            else:
                l2.append(w)
        line1 = " ".join(l1)[:16]
        line2 = " ".join(l2)[:16]
        draw.text((8, 28), line1, fill=(255, 255, 255), font=font_bold_m)
        draw.text((8, 46), line2, fill=(255, 255, 255), font=font_bold_m)
    else:
        draw.text((8, 36), title, fill=(255, 255, 255), font=font_bold_m)

    # Artist line (y: 72..88)
    artist = media.get("artist", "")
    draw.text((8, 72), artist[:20], fill=(0, 229, 255), font=font_reg_s)

    # Album / Progress line (y: 90..106)
    album = media.get("album", "")
    if album:
        draw.text((8, 88), album[:22], fill=(148, 163, 184), font=font_mono_xs)
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
