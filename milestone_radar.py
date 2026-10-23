#!/usr/bin/env python3
"""
Family Milestone Radar & AI-Ready Card Renderer for Ajazz AK820 Pro (128x128 TFT).

Reads recurring family milestones from ~/.config/ak820/events.json (or $AK820_EVENTS_PATH),
computes exact countdowns and milestone ages/years, and renders high-contrast
128x128 RGB frames (Hero Countdown Card, 3-Row Family Board, Hybrid Carousel,
and custom AI sticky-note cards).
"""

import datetime
import json
import os
from typing import Dict, List, Optional, Tuple
from PIL import Image, ImageDraw

# v0.8.0 render_common (m8): screen constants, font path catalogs, and the
# lru_cache'd font loader are shared with widgets.py via ak820.screen; payload
# dedup persistence moved to ak820.state (§2.1 layering inversion fix).
from ak820.screen import FONT_BOLD_PATHS, FONT_MONO_PATHS, FONT_NARROW_PATHS, SCREEN_H, SCREEN_W, _load_font
from ak820.state import (  # noqa: F401  (re-exported for backward compatibility)
    DEFAULT_STATE_PATH,
    check_and_record_payload_hash,
    save_payload_hash,
)

def get_default_events_path() -> str:
    """Return default path to ak820 events config with env/legacy/XDG fallback."""
    if "AK820_EVENTS_PATH" in os.environ:
        return os.path.expanduser(os.environ["AK820_EVENTS_PATH"])
    legacy = os.path.expanduser("~/personal-data/ak820_events.json")
    if os.path.exists(legacy):
        return legacy
    xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(xdg_config, "ak820", "events.json")


DEFAULT_EVENTS_PATH = get_default_events_path()

THEMES = {
    "today": {
        "bg": (12, 6, 20),
        "panel": (28, 12, 42),
        "border": (255, 42, 133),
        "header_bg": (255, 42, 133),
        "header_fg": (255, 255, 255),
        "accent": (255, 215, 0),
        "sub": (255, 200, 230),
        "tag": "TODAY!",
    },
    "urgent": {  # 1..3 days
        "bg": (16, 7, 10),
        "panel": (34, 14, 18),
        "border": (255, 59, 48),
        "header_bg": (220, 38, 38),
        "header_fg": (255, 255, 255),
        "accent": (255, 115, 95),
        "sub": (254, 202, 202),
        "tag": "URGENT",
    },
    "warn": {  # 4..14 days
        "bg": (14, 11, 6),
        "panel": (30, 22, 10),
        "border": (245, 158, 11),
        "header_bg": (217, 119, 6),
        "header_fg": (10, 10, 10),
        "accent": (251, 191, 36),
        "sub": (253, 230, 138),
        "tag": "PLAN GIFT",
    },
    "soon": {  # 15..35 days
        "bg": (6, 11, 18),
        "panel": (13, 24, 38),
        "border": (0, 229, 255),
        "header_bg": (0, 180, 216),
        "header_fg": (6, 12, 20),
        "accent": (56, 235, 255),
        "sub": (186, 230, 253),
        "tag": "UPCOMING",
    },
    "calm": {  # >35 days
        "bg": (7, 10, 18),
        "panel": (14, 20, 34),
        "border": (59, 130, 246),
        "header_bg": (37, 99, 235),
        "header_fg": (255, 255, 255),
        "accent": (147, 197, 253),
        "sub": (203, 213, 225),
        "tag": "MILESTONE",
    },
    "green": {
        "bg": (6, 14, 10),
        "panel": (12, 28, 20),
        "border": (16, 185, 129),
        "header_bg": (16, 185, 129),
        "header_fg": (6, 14, 10),
        "accent": (52, 211, 153),
        "sub": (167, 243, 208),
        "tag": "READY",
    },
}


def _fit_text_font(draw: ImageDraw.ImageDraw, text: str, paths: List[str], max_size: int, min_size: int, max_width: int):
    for sz in range(max_size, min_size - 1, -1):
        font = _load_font(paths, sz)
        bbox = draw.textbbox((0, 0), text, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            return font
    return _load_font(paths, min_size)


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class ConfigError(Exception):
    """Raised when the events config contains invalid data (Defects D4/D5).

    Mapped to exit code 6 in `ak820_ctrl.main()` so a single typo in
    events.json fails the daily systemd timer with a friendly message
    instead of a raw ValueError traceback.
    """

    exit_code = 6


def _safe_date(year: int, month: int, day: int) -> datetime.date:
    """Build a date, clamping Feb 29 -> Feb 28 ONLY on non-leap years (D5).

    The previous blanket `except ValueError` fallback assumed Feb-29-on-non-leap
    was the only possible failure and itself crashed on any other invalid
    component (e.g. month 13 -> `date(y, 13, 28)` -> ValueError). Every other
    invalid combination now raises ConfigError with the offending components.
    """
    try:
        return datetime.date(year, month, day)
    except ValueError:
        if (month, day) == (2, 29):  # the ONLY expected failure mode
            return datetime.date(year, 2, 28)
        raise ConfigError(
            f"Invalid event date components: {year:04d}-{month:02d}-{day:02d}"
        )


def parse_event_date(raw: str) -> Tuple[Optional[int], int, int]:
    """Validate an event date at input time (D4): 'YYYY-MM-DD' or 'MM-DD'.

    Returns (year, month, day); year is None for recurring MM-DD dates.
    Raises ConfigError with an informative message on anything else, so
    `events add` fails fast and bad data never reaches render time.
    """
    if not isinstance(raw, str):
        raise ConfigError(f"Invalid date {raw!r}: expected YYYY-MM-DD or MM-DD")
    parts = raw.split("-")
    try:
        if len(parts) == 3:
            y, m, d = (int(p) for p in parts)
            datetime.date(y, m, d)
            return y, m, d
        if len(parts) == 2:
            m, d = (int(p) for p in parts)
            datetime.date(2024, m, d)  # 2024 is a leap year: validates Feb 29
            return None, m, d
    except ValueError:
        pass
    raise ConfigError(f"Invalid date {raw!r}: expected YYYY-MM-DD or MM-DD")


def load_events_config(config_path: Optional[str] = None) -> Dict:
    path = config_path or get_default_events_path()
    if not os.path.exists(path):
        return {"default_mode": "radar", "default_preset": "bongo", "events": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_events_config(cfg: Dict, config_path: Optional[str] = None):
    path = config_path or get_default_events_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def _threshold(cfg: Dict, key: str, default: int) -> int:
    """Read an urgency threshold from config (D7), falling back on invalid values."""
    try:
        value = int(cfg.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def compute_upcoming_events(cfg: Dict, today: Optional[datetime.date] = None) -> List[Dict]:
    if today is None:
        today = datetime.date.today()

    # D7: honor configurable urgency thresholds (defaults match previous hardcodes)
    urgent_days = _threshold(cfg, "urgent_days", 3)
    warn_days = _threshold(cfg, "warn_days", 14)
    soon_days = _threshold(cfg, "soon_days", 35)

    upcoming = []
    for ev in cfg.get("events", []):
        raw_date = ev.get("date", "")
        orig_year, month, day = parse_event_date(raw_date)  # D4: friendly error, no traceback
        if orig_year is None:
            orig_year = today.year

        next_date = _safe_date(today.year, month, day)  # D5: narrow Feb-29 clamp only
        if next_date < today:
            next_date = _safe_date(today.year + 1, month, day)

        days_left = (next_date - today).days
        years = next_date.year - orig_year
        category = ev.get("category", "milestone")

        if category == "birthday":
            milestone_str = f"Turns {years}" if years > 0 else "Birthday"
            short_age = _ordinal(years) if years > 0 else "B-Day"
        elif category == "anniversary":
            milestone_str = f"{years} Years" if years > 0 else "Anniversary"
            short_age = f"{years} yrs" if years > 0 else "Anniv"
        else:
            note_tpl = ev.get("note", "")
            milestone_str = note_tpl.format(years=years) if "{years}" in note_tpl else (note_tpl or "Milestone")
            short_age = milestone_str

        if days_left == 0:
            theme_key = "today"
        elif days_left <= urgent_days:
            theme_key = "urgent"
        elif days_left <= warn_days:
            theme_key = "warn"
        elif days_left <= soon_days:
            theme_key = "soon"
        else:
            theme_key = "calm"

        upcoming.append({
            "id": ev.get("id", ev.get("title", "event").lower().replace(" ", "-")),
            "title": ev.get("title", "Milestone"),
            "short_title": ev.get("short_title", ev.get("title", "EVENT").upper()[:11]),
            "orig_date": raw_date,
            "next_date": next_date,
            "date_label": next_date.strftime("%b %d"),
            "days_left": days_left,
            "years": years,
            "category": category,
            "milestone_str": milestone_str,
            "short_age": short_age,
            "theme_key": theme_key,
        })

    upcoming.sort(key=lambda x: (x["days_left"], x["title"]))
    return upcoming


def _draw_category_icon(draw: ImageDraw.ImageDraw, x: int, y: int, category: str, color: Tuple[int, int, int]):
    """Draw a crisp 10x10 pixel-art icon for birthday cake or wedding rings."""
    if category == "birthday":
        # Cake base + candle + flame
        draw.rectangle([x + 1, y + 5, x + 9, y + 9], fill=color)
        draw.rectangle([x + 4, y + 2, x + 6, y + 4], fill=(255, 255, 255))
        draw.point((x + 5, y + 1), fill=(255, 215, 0))
    elif category == "anniversary":
        # Heart / rings
        draw.polygon([(x + 5, y + 9), (x + 1, y + 4), (x + 3, y + 2), (x + 5, y + 4), (x + 7, y + 2), (x + 9, y + 4)], fill=color)
    else:
        # Star / diamond
        draw.polygon([(x + 5, y + 1), (x + 9, y + 5), (x + 5, y + 9), (x + 1, y + 5)], fill=color)


def render_hero_card(upcoming: List[Dict], today: Optional[datetime.date] = None) -> Image.Image:
    """Render Slide 1: High-contrast Hero Card for the #1 upcoming family milestone + #2 ticker."""
    if today is None:
        today = datetime.date.today()

    if not upcoming:
        return render_custom_card("MILESTONES", "NONE", "Add events in JSON", today.strftime("%b %d"), "calm")

    ev = upcoming[0]
    theme = THEMES[ev["theme_key"]]

    img = Image.new("RGB", (SCREEN_W, SCREEN_H), theme["bg"])
    draw = ImageDraw.Draw(img)

    # Outer frame border
    draw.rounded_rectangle([1, 1, 126, 126], radius=6, outline=theme["border"], width=1)

    # Top header pill (y = 4..18)
    draw.rounded_rectangle([4, 4, 123, 18], radius=3, fill=theme["header_bg"])
    f_hdr = _load_font(FONT_BOLD_PATHS, 10)
    hdr_text = f"{theme['tag']} • {ev['date_label'].upper()}"
    bbox = draw.textbbox((0, 0), hdr_text, font=f_hdr)
    draw.text(((SCREEN_W - (bbox[2] - bbox[0])) // 2, 5), hdr_text, font=f_hdr, fill=theme["header_fg"])

    # Event short title (y = 21..39)
    title_str = ev["short_title"].upper()
    f_title = _fit_text_font(draw, title_str, FONT_NARROW_PATHS, max_size=16, min_size=11, max_width=116)
    tb = draw.textbbox((0, 0), title_str, font=f_title)
    draw.text(((SCREEN_W - (tb[2] - tb[0])) // 2, 21), title_str, font=f_title, fill=(255, 255, 255))

    # Center countdown box (y = 41..85)
    draw.rounded_rectangle([6, 41, 121, 85], radius=5, fill=theme["panel"], outline=theme["border"], width=1)

    days = ev["days_left"]
    if days == 0:
        big_str = "TODAY!"
        f_big = _fit_text_font(draw, big_str, FONT_BOLD_PATHS, 24, 16, 108)
        bb = draw.textbbox((0, 0), big_str, font=f_big)
        draw.text(((SCREEN_W - (bb[2] - bb[0])) // 2, 51), big_str, font=f_big, fill=theme["accent"])
    elif days == 1:
        big_str = "TOMORROW"
        f_big = _fit_text_font(draw, big_str, FONT_NARROW_PATHS, 20, 14, 108)
        bb = draw.textbbox((0, 0), big_str, font=f_big)
        draw.text(((SCREEN_W - (bb[2] - bb[0])) // 2, 53), big_str, font=f_big, fill=theme["accent"])
    else:
        num_str = str(days)
        unit_str = "DAYS"
        f_num = _load_font(FONT_BOLD_PATHS, 28 if len(num_str) <= 2 else 23)
        f_unit = _load_font(FONT_NARROW_PATHS, 14)
        nb = draw.textbbox((0, 0), num_str, font=f_num)
        ub = draw.textbbox((0, 0), unit_str, font=f_unit)
        num_w = nb[2] - nb[0]
        unit_w = ub[2] - ub[0]
        total_w = num_w + 5 + unit_w
        start_x = (SCREEN_W - total_w) // 2
        draw.text((start_x, 45), num_str, font=f_num, fill=theme["accent"])
        draw.text((start_x + num_w + 5, 56), unit_str, font=f_unit, fill=(255, 255, 255))

    # Subtitle line: e.g. "Nov 06 • Turns 1" (y = 89..103)
    sub_str = f"{ev['date_label']} • {ev['milestone_str']}"
    f_sub = _fit_text_font(draw, sub_str, FONT_BOLD_PATHS, 11, 9, 116)
    sb = draw.textbbox((0, 0), sub_str, font=f_sub)
    draw.text(((SCREEN_W - (sb[2] - sb[0])) // 2, 89), sub_str, font=f_sub, fill=theme["sub"])

    # Divider line
    draw.line([8, 105, 119, 105], fill=(35, 48, 70), width=1)

    # Bottom ticker: 2nd upcoming milestone (y = 108..122)
    if len(upcoming) > 1:
        ev2 = upcoming[1]
        t2 = THEMES[ev2["theme_key"]]
        next_label = f"NEXT: {ev2['short_title']} ({ev2['days_left']}d)"
        f_next = _fit_text_font(draw, next_label, FONT_BOLD_PATHS, 10, 8, 116)
        nb2 = draw.textbbox((0, 0), next_label, font=f_next)
        draw.text(((SCREEN_W - (nb2[2] - nb2[0])) // 2, 109), next_label, font=f_next, fill=t2["accent"])
    else:
        f_next = _load_font(FONT_BOLD_PATHS, 10)
        draw.text((24, 109), today.strftime("TODAY: %b %d"), font=f_next, fill=(148, 163, 184))

    return img


def render_board_card(upcoming: List[Dict], today: Optional[datetime.date] = None) -> Image.Image:
    """Render Slide 2: All-in-one 3-Row Family Milestone Radar Board."""
    if today is None:
        today = datetime.date.today()

    img = Image.new("RGB", (SCREEN_W, SCREEN_H), (6, 9, 16))
    draw = ImageDraw.Draw(img)

    # Outer border
    draw.rounded_rectangle([1, 1, 126, 126], radius=6, outline=(38, 56, 84), width=1)

    # Top Header (y = 4..19)
    draw.rounded_rectangle([4, 4, 123, 19], radius=3, fill=(16, 26, 44))
    f_hdr = _load_font(FONT_BOLD_PATHS, 10)
    draw.text((7, 6), "FAMILY RADAR", font=f_hdr, fill=(56, 235, 255))
    date_s = today.strftime("%b %d").upper()
    db = draw.textbbox((0, 0), date_s, font=f_hdr)
    draw.text((121 - (db[2] - db[0]), 6), date_s, font=f_hdr, fill=(148, 163, 184))

    # Up to 3 rows (y = 22..54, 57..89, 92..124)
    f_days = _load_font(FONT_BOLD_PATHS, 12)
    f_meta = _load_font(FONT_BOLD_PATHS, 9)

    for idx, ev in enumerate(upcoming[:3]):
        y0 = 22 + idx * 35
        y1 = y0 + 31
        theme = THEMES[ev["theme_key"]]

        # Row background & left accent bar
        row_bg = (15, 23, 38) if idx == 0 else (10, 15, 26)
        row_border = theme["border"] if idx == 0 else (28, 40, 62)
        draw.rounded_rectangle([4, y0, 123, y1], radius=4, fill=row_bg, outline=row_border, width=1)
        draw.rounded_rectangle([4, y0, 7, y1], radius=2, fill=theme["border"])

        days_s = "TODAY!" if ev["days_left"] == 0 else f"{ev['days_left']}d"
        dbx = draw.textbbox((0, 0), days_s, font=f_days)
        dw = dbx[2] - dbx[0]
        draw.text((119 - dw, y0 + 2), days_s, font=f_days, fill=theme["accent"])

        # Top row line: Short name auto-fitted to available width before days pill
        name_s = ev["short_title"]
        f_name = _fit_text_font(draw, name_s, FONT_NARROW_PATHS, 12, 9, 102 - dw)
        draw.text((11, y0 + 2), name_s, font=f_name, fill=(255, 255, 255))

        # Bottom row line: Date + age/years
        meta_s = f"{ev['date_label']} • {ev['milestone_str']}"
        draw.text((11, y0 + 17), meta_s, font=f_meta, fill=theme["sub"])

    return img


def render_custom_card(
    title: str,
    value: str,
    sub: str = "",
    footer: str = "",
    theme_name: str = "soon",
) -> Image.Image:
    """Render an AI-ready custom 128x128 status/notification card."""
    theme = THEMES.get(theme_name, THEMES["soon"])
    img = Image.new("RGB", (SCREEN_W, SCREEN_H), theme["bg"])
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle([1, 1, 126, 126], radius=6, outline=theme["border"], width=1)
    draw.rounded_rectangle([4, 4, 123, 19], radius=3, fill=theme["header_bg"])

    f_hdr = _fit_text_font(draw, title.upper(), FONT_BOLD_PATHS, 11, 9, 114)
    tb = draw.textbbox((0, 0), title.upper(), font=f_hdr)
    draw.text(((SCREEN_W - (tb[2] - tb[0])) // 2, 6), title.upper(), font=f_hdr, fill=theme["header_fg"])

    draw.rounded_rectangle([6, 25, 121, 78], radius=5, fill=theme["panel"], outline=theme["border"], width=1)
    f_val = _fit_text_font(draw, value, FONT_BOLD_PATHS, 24, 13, 108)
    vb = draw.textbbox((0, 0), value, font=f_val)
    vh = vb[3] - vb[1]
    draw.text(((SCREEN_W - (vb[2] - vb[0])) // 2, 25 + (53 - vh) // 2 - 3), value, font=f_val, fill=theme["accent"])

    if sub:
        f_sub = _fit_text_font(draw, sub, FONT_BOLD_PATHS, 12, 9, 114)
        sb = draw.textbbox((0, 0), sub, font=f_sub)
        draw.text(((SCREEN_W - (sb[2] - sb[0])) // 2, 85), sub, font=f_sub, fill=(255, 255, 255))

    if footer:
        draw.line([8, 105, 119, 105], fill=(35, 48, 70), width=1)
        f_foot = _fit_text_font(draw, footer, FONT_BOLD_PATHS, 10, 8, 114)
        fb = draw.textbbox((0, 0), footer, font=f_foot)
        draw.text(((SCREEN_W - (fb[2] - fb[0])) // 2, 109), footer, font=f_foot, fill=theme["sub"])

    return img


# check_and_record_payload_hash / save_payload_hash moved to ak820.state
# (v0.8.0) and are re-exported above for backward compatibility.
