#!/usr/bin/env python3
"""
Family Milestone Radar & AI-Ready Card Renderer — COMPATIBILITY SHIM (v0.25.0).

Reads recurring family milestones from ~/.config/ak820/events.json (or $AK820_EVENTS_PATH),
computes exact countdowns and milestone ages/years, and renders high-contrast
128x128 RGB frames (Hero Countdown Card, 3-Row Family Board, Hybrid Carousel,
and custom AI sticky-note cards).

Since v0.25.0 the implementation lives in the `ak820/` package: the events-config
domain (path resolution, D4/D5 date validation, m7/D22 shape checks, countdown
rows) is `ak820.events`; the three card renderers are `ak820.cards`; the THEMES
urgency palette is `ak820.palette`; ConfigError joined the taxonomy in
`ak820.errors`. This module re-exports every historical name so existing
`milestone_radar.<name>` imports, user scripts, and systemd timers keep working
unchanged — identity, not copies (locked by tests/test_modularity_v025.py).
"""

from ak820.cards import (  # noqa: F401  (re-exported for backward compatibility)
    _draw_category_icon,
    render_board_card,
    render_custom_card,
    render_hero_card,
)
from ak820.errors import ConfigError  # noqa: F401  (re-exported: same class object)
from ak820.events import (  # noqa: F401  (re-exported for backward compatibility)
    _ordinal,
    _safe_date,
    _threshold,
    compute_upcoming_events,
    get_default_events_path,
    load_events_config,
    parse_event_date,
    save_events_config,
)
from ak820.palette import THEMES  # noqa: F401  (re-exported for backward compatibility)

# v0.8.0 render_common (m8): screen constants, font path catalogs, and the
# lru_cache'd font loader are shared with widgets.py via ak820.screen; payload
# dedup persistence moved to ak820.state (§2.1 layering inversion fix).
# v0.34.0 (m30): `fit_text`/`ellipsize` (the fit-then-mark containment rule) join
# them in `ak820.screen`, the shared render_common home. The old private
# `_fit_text_font` is GONE rather than aliased: it returned only a font, and a
# font alone cannot promise containment — aliasing the old name would have kept
# the lie available to callers (recorded in ITERATIONS.md, not silently dropped).
from ak820.screen import (  # noqa: F401  (re-exported for backward compatibility)
    FONT_BOLD_PATHS,
    FONT_MONO_PATHS,
    FONT_NARROW_PATHS,
    SCREEN_H,
    SCREEN_W,
    _load_font,
    ellipsize,
    fit_text,
)
from ak820.state import (  # noqa: F401  (re-exported for backward compatibility)
    DEFAULT_STATE_PATH,
    check_and_record_payload_hash,
    save_payload_hash,
)

# check_and_record_payload_hash / save_payload_hash moved to ak820.state
# (v0.8.0) and are re-exported above for backward compatibility.
