#!/usr/bin/env python3
"""
widgets.py — Standalone System HUD and Media Player card renderers for 128x128 TFT.
Zero external runtime dependencies beyond Pillow and Linux sysfs / D-Bus.

Since v0.25.0 the implementation lives in the `ak820/` package: the sysfs/proc
sampling and playerctl/MPRIS probes are `ak820.telemetry` (PIL-free), and the
two card renderers are `ak820.hud`. This module re-exports every historical
name so existing `widgets.<name>` imports keep working unchanged — identity,
not copies (locked by tests/test_modularity_v025.py).

Note for test authors: probe seams now live in `ak820.telemetry` — patch
`ak820.telemetry.shutil` / `ak820.telemetry.subprocess`, not this shim.
"""

from ak820.hud import (  # noqa: F401  (re-exported for backward compatibility)
    render_hud_card,
    render_media_card,
)

# v0.8.0 render_common (m8): screen constants, font path catalogs, and the
# lru_cache'd font loader (§4.2) are shared with milestone_radar.py.
from ak820.screen import (  # noqa: F401  (re-exported for backward compatibility)
    FONT_BOLD_PATHS,
    FONT_MONO_PATHS,
    FONT_REGULAR_PATHS,
    SCREEN_H,
    SCREEN_W,
    _load_font,
)
from ak820.telemetry import (  # noqa: F401  (re-exported for backward compatibility)
    get_media_status,
    get_system_stats,
)
