"""Curated ambient lighting preset catalog (v0.27.0).

Pure data module split out of `backlight.py` under the §10 line budget when the
idle-config `--config` threading (v0.27.0) landed: this file owns *what the
showcase presets are*, `backlight.py` owns *how they are sent and persisted*.
`backlight.SHOWCASE_PRESETS` re-exports this exact object (identity, not copy)
so `ak820.backlight.*`, `ak820.*`, and the `ak820_ctrl.py` shim are unchanged
— locked by `tests/test_device_config_parity_v027.py`.
"""

from typing import Any

# Heterogeneous preset records (str/int/bool values) — `Any` keeps the dict
# values usable at call sites (status.py demo) without lying about types.
SHOWCASE_PRESETS: list[dict[str, Any]] = [
    {
        "key": "warm-white",
        "name": "Warm White",
        "mode": 1,
        "color": "#FFE0B2",
        "rainbow": False,
        "brightness": 4,
        "speed": 3,
        "desc": "Subtle incandescent glow, easy on the eyes for coding & reading",
    },
    {
        "key": "amber-retro",
        "name": "Amber Terminal",
        "mode": 1,
        "color": "#FF8C00",
        "rainbow": False,
        "brightness": 4,
        "speed": 3,
        "desc": "Warm retro amber terminal glow",
    },
    {
        "key": "cyber-cyan",
        "name": "Cyber Cyan",
        "mode": 1,
        "color": "#00E5FF",
        "rainbow": False,
        "brightness": 4,
        "speed": 3,
        "desc": "Crisp modern cyberpunk cyan highlight",
    },
    {
        "key": "spectrum",
        "name": "Spectrum Wave",
        "mode": 8,
        "color": "#000000",
        "rainbow": True,
        "brightness": 4,
        "speed": 3,
        "desc": "Continuous smooth full-spectrum rainbow color cycle",
    },
    {
        "key": "single-on",
        "name": "Reactive Typing",
        "mode": 2,
        "color": "#00E5FF",
        "rainbow": False,
        "brightness": 5,
        "speed": 3,
        "desc": "Stealth ambient: keys illuminate under your fingers as you type",
    },
    {
        "key": "breath-lavender",
        "name": "Breathing Lavender",
        "mode": 7,
        "color": "#BA68C8",
        "rainbow": False,
        "brightness": 4,
        "speed": 2,
        "desc": "Slow, relaxing lavender breathing pulsation",
    },
    {
        "key": "flowing",
        "name": "Flowing Stream",
        "mode": 16,
        "color": "#000000",
        "rainbow": True,
        "brightness": 4,
        "speed": 3,
        "desc": "Dynamic gradient flowing sideways across the keyboard",
    },
    {
        "key": "off",
        "name": "Stealth (Off)",
        "mode": 0,
        "color": "#000000",
        "rainbow": False,
        "brightness": 0,
        "speed": 0,
        "desc": "Zero LEDs, completely dark",
    },
]
