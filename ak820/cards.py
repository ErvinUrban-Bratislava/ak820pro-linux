"""Radar card renderers: hero countdown, family board, custom AI cards (v0.25.0).

Split out of `milestone_radar.py` under the §10 modularity budget. This module
owns the *pixel* half of the radar domain — the THEMES urgency palette and the
three 128x128 card renderers. It consumes the plain `upcoming` row dicts that
`ak820.events.compute_upcoming_events` produces and never reads config or disk
itself, so rendering stays a pure function of its inputs (offline-testable).

`milestone_radar.py` re-exports every name here unchanged (identity, not copy).
"""

import datetime

from PIL import Image, ImageDraw

from .palette import THEMES
from .screen import (
    FONT_BOLD_PATHS,
    FONT_NARROW_PATHS,
    SCREEN_H,
    SCREEN_W,
    _load_font,
    fit_text,
)

# m30 (v0.34.0) layout budgets. Every dynamic string on these cards is drawn
# through `screen.fit_text` against one of them, so "does it fit?" is asked
# against the same box the pixels are drawn into — never against a character
# count, which is a proxy that lies for wide glyphs (measured: 16 wide chars at
# size 12 = 182 px into a 120 px box).
HERO_HEADER_W = 114  # header pill [4..123] minus padding
BOARD_META_W = 112  # row box right edge 123 minus the text x of 11


def _draw_category_icon(
    draw: ImageDraw.ImageDraw, x: int, y: int, category: str, color: tuple[int, int, int]
):
    """Draw a crisp 10x10 pixel-art icon for birthday cake or wedding rings."""
    if category == "birthday":
        # Cake base + candle + flame
        draw.rectangle([x + 1, y + 5, x + 9, y + 9], fill=color)
        draw.rectangle([x + 4, y + 2, x + 6, y + 4], fill=(255, 255, 255))
        draw.point((x + 5, y + 1), fill=(255, 215, 0))
    elif category == "anniversary":
        # Heart / rings
        draw.polygon(
            [
                (x + 5, y + 9),
                (x + 1, y + 4),
                (x + 3, y + 2),
                (x + 5, y + 4),
                (x + 7, y + 2),
                (x + 9, y + 4),
            ],
            fill=color,
        )
    else:
        # Star / diamond
        draw.polygon([(x + 5, y + 1), (x + 9, y + 5), (x + 5, y + 9), (x + 1, y + 5)], fill=color)


def render_hero_card(upcoming: list[dict], today: datetime.date | None = None) -> Image.Image:
    """Render Slide 1: High-contrast Hero Card for the #1 upcoming family milestone + #2 ticker."""
    if today is None:
        today = datetime.date.today()

    if not upcoming:
        return render_custom_card(
            "MILESTONES", "NONE", "Add events in JSON", today.strftime("%b %d"), "calm"
        )

    ev = upcoming[0]
    theme = THEMES[ev["theme_key"]]

    img = Image.new("RGB", (SCREEN_W, SCREEN_H), theme["bg"])
    draw = ImageDraw.Draw(img)

    # Outer frame border
    draw.rounded_rectangle([1, 1, 126, 126], radius=6, outline=theme["border"], width=1)

    # Top header pill (y = 4..18)
    draw.rounded_rectangle([4, 4, 123, 18], radius=3, fill=theme["header_bg"])
    hdr_text = f"{theme['tag']} • {ev['date_label'].upper()}"
    f_hdr, hdr_text = fit_text(draw, hdr_text, FONT_BOLD_PATHS, 10, 10, HERO_HEADER_W)
    bbox = draw.textbbox((0, 0), hdr_text, font=f_hdr)
    draw.text(
        ((SCREEN_W - (bbox[2] - bbox[0])) // 2, 5), hdr_text, font=f_hdr, fill=theme["header_fg"]
    )

    # Event short title (y = 21..39)
    title_str = ev["short_title"].upper()
    f_title, title_str = fit_text(
        draw, title_str, FONT_NARROW_PATHS, max_size=16, min_size=11, max_width=116
    )
    tb = draw.textbbox((0, 0), title_str, font=f_title)
    draw.text(
        ((SCREEN_W - (tb[2] - tb[0])) // 2, 21), title_str, font=f_title, fill=(255, 255, 255)
    )

    # Center countdown box (y = 41..85)
    draw.rounded_rectangle(
        [6, 41, 121, 85], radius=5, fill=theme["panel"], outline=theme["border"], width=1
    )

    days = ev["days_left"]
    if days == 0:
        big_str = "TODAY!"
        f_big, big_str = fit_text(draw, big_str, FONT_BOLD_PATHS, 24, 16, 108)
        bb = draw.textbbox((0, 0), big_str, font=f_big)
        draw.text(
            ((SCREEN_W - (bb[2] - bb[0])) // 2, 51), big_str, font=f_big, fill=theme["accent"]
        )
    elif days == 1:
        big_str = "TOMORROW"
        f_big, big_str = fit_text(draw, big_str, FONT_NARROW_PATHS, 20, 14, 108)
        bb = draw.textbbox((0, 0), big_str, font=f_big)
        draw.text(
            ((SCREEN_W - (bb[2] - bb[0])) // 2, 53), big_str, font=f_big, fill=theme["accent"]
        )
    else:
        # The countdown numerics are the one documented exemption from fit_text:
        # `days_left` is bounded by construction (the next occurrence is always
        # within a year, so at most 3 digits) and the pair is already sized by
        # length. Every user-authored string on this card goes through the fitter.
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
    f_sub, sub_str = fit_text(draw, sub_str, FONT_BOLD_PATHS, 11, 9, 116)
    sb = draw.textbbox((0, 0), sub_str, font=f_sub)
    draw.text(((SCREEN_W - (sb[2] - sb[0])) // 2, 89), sub_str, font=f_sub, fill=theme["sub"])

    # Divider line
    draw.line([8, 105, 119, 105], fill=(35, 48, 70), width=1)

    # Bottom ticker: 2nd upcoming milestone (y = 108..122)
    if len(upcoming) > 1:
        ev2 = upcoming[1]
        t2 = THEMES[ev2["theme_key"]]
        next_label = f"NEXT: {ev2['short_title']} ({ev2['days_left']}d)"
        f_next, next_label = fit_text(draw, next_label, FONT_BOLD_PATHS, 10, 8, 116)
        nb2 = draw.textbbox((0, 0), next_label, font=f_next)
        draw.text(
            ((SCREEN_W - (nb2[2] - nb2[0])) // 2, 109), next_label, font=f_next, fill=t2["accent"]
        )
    else:
        f_next = _load_font(FONT_BOLD_PATHS, 10)
        draw.text((24, 109), today.strftime("TODAY: %b %d"), font=f_next, fill=(148, 163, 184))

    return img


def render_board_card(upcoming: list[dict], today: datetime.date | None = None) -> Image.Image:
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
    label_s = "FAMILY RADAR"
    draw.text((7, 6), label_s, font=f_hdr, fill=(56, 235, 255))
    lb = draw.textbbox((0, 0), label_s, font=f_hdr)
    # The date's budget is whatever the label leaves, derived from the measured
    # label rather than hardcoded — a locale with a longer %b ellipsizes instead
    # of sliding under the header label (m30's containment rule, applied here too).
    date_s = today.strftime("%b %d").upper()
    f_hdr_date, date_s = fit_text(
        draw, date_s, FONT_BOLD_PATHS, 10, 10, 121 - (7 + (lb[2] - lb[0]))
    )
    db = draw.textbbox((0, 0), date_s, font=f_hdr_date)
    draw.text((121 - (db[2] - db[0]), 6), date_s, font=f_hdr_date, fill=(148, 163, 184))

    # Up to 3 rows (y = 22..54, 57..89, 92..124)
    f_days = _load_font(FONT_BOLD_PATHS, 12)

    for idx, ev in enumerate(upcoming[:3]):
        y0 = 22 + idx * 35
        y1 = y0 + 31
        theme = THEMES[ev["theme_key"]]

        # Row background & left accent bar
        row_bg = (15, 23, 38) if idx == 0 else (10, 15, 26)
        row_border = theme["border"] if idx == 0 else (28, 40, 62)
        draw.rounded_rectangle([4, y0, 123, y1], radius=4, fill=row_bg, outline=row_border, width=1)
        draw.rounded_rectangle([4, y0, 7, y1], radius=2, fill=theme["border"])

        # The days pill is the row's other documented fit_text exemption: it is
        # numeric and bounded by construction (days_left <= 366), and its own
        # measured width is what the name line's budget is derived from.
        days_s = "TODAY!" if ev["days_left"] == 0 else f"{ev['days_left']}d"
        dbx = draw.textbbox((0, 0), days_s, font=f_days)
        dw = dbx[2] - dbx[0]
        draw.text((119 - dw, y0 + 2), days_s, font=f_days, fill=theme["accent"])

        # Top row line: Short name auto-fitted to available width before days pill
        name_s = ev["short_title"]
        f_name, name_s = fit_text(draw, name_s, FONT_NARROW_PATHS, 12, 9, 102 - dw)
        draw.text((11, y0 + 2), name_s, font=f_name, fill=(255, 255, 255))

        # Bottom row line: Date + age/years. m30 (v0.34.0): this was the one
        # dynamic string on the card drawn at a FIXED size with no fitting at all
        # — a 66-char note measured 288 px from x=11 into a 128 px screen, cut off
        # mid-word and silent. Size stays 9 (so every note that already fitted
        # renders byte-identically); what changed is that overflow is now marked
        # with an ellipsis inside the row box instead of amputated by the screen.
        meta_s = f"{ev['date_label']} • {ev['milestone_str']}"
        f_meta, meta_s = fit_text(draw, meta_s, FONT_BOLD_PATHS, 9, 9, BOARD_META_W)
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

    hdr_s = title.upper()
    f_hdr, hdr_s = fit_text(draw, hdr_s, FONT_BOLD_PATHS, 11, 9, 114)
    tb = draw.textbbox((0, 0), hdr_s, font=f_hdr)
    draw.text(((SCREEN_W - (tb[2] - tb[0])) // 2, 6), hdr_s, font=f_hdr, fill=theme["header_fg"])

    draw.rounded_rectangle(
        [6, 25, 121, 78], radius=5, fill=theme["panel"], outline=theme["border"], width=1
    )
    f_val, value_s = fit_text(draw, value, FONT_BOLD_PATHS, 24, 13, 108)
    vb = draw.textbbox((0, 0), value_s, font=f_val)
    vh = vb[3] - vb[1]
    draw.text(
        ((SCREEN_W - (vb[2] - vb[0])) // 2, 25 + (53 - vh) // 2 - 3),
        value_s,
        font=f_val,
        fill=theme["accent"],
    )

    if sub:
        f_sub, sub_s = fit_text(draw, sub, FONT_BOLD_PATHS, 12, 9, 114)
        sb = draw.textbbox((0, 0), sub_s, font=f_sub)
        draw.text(((SCREEN_W - (sb[2] - sb[0])) // 2, 85), sub_s, font=f_sub, fill=(255, 255, 255))

    if footer:
        draw.line([8, 105, 119, 105], fill=(35, 48, 70), width=1)
        f_foot, footer_s = fit_text(draw, footer, FONT_BOLD_PATHS, 10, 8, 114)
        fb = draw.textbbox((0, 0), footer_s, font=f_foot)
        draw.text(
            ((SCREEN_W - (fb[2] - fb[0])) // 2, 109), footer_s, font=f_foot, fill=theme["sub"]
        )

    return img
