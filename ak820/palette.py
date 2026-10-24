"""Urgency theme palette for the radar cards (v0.25.0).

Extracted from `ak820.cards` under the §10 modularity budget: one entry per
urgency band — the `theme_key` strings `ak820.events.compute_upcoming_events`
assigns (today/urgent/warn/soon/calm) plus the standalone `green` — mapping to
the full RGB colorway a card renderer paints with. Pure data: no drawing, no
config reads, so both the renderers and any future surface share one palette.
`milestone_radar.py` re-exports THEMES unchanged (identity, not copy).
"""

from typing import Any

# Heterogeneous palette records (RGB tuples + str tags) — `Any` keeps the
# values usable at PIL call sites without lying about types (v0.22 idiom,
# same as backlight.SHOWCASE_PRESETS).
THEMES: dict[str, dict[str, Any]] = {
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
