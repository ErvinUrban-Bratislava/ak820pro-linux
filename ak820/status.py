"""Workflow status lighting + live showcase demo (v0.9.0).

`status` / `run` lighting semantics: in-progress pulses amber; success/failed
breathe green/red and (unless --no-wait) wait for a real keypress on the
keyboard before restoring the ambient idle preset.
"""

import time

from .backlight import SHOWCASE_PRESETS, apply_idle_backlight, set_backlight
from .protocol import LIGHT_MODES
from .transport import wait_for_keypress


def set_status_backlight(dev, status_name: str, label: str | None = None, wait_key: bool = True):
    status_name = status_name.lower().strip()
    if status_name in ("in-progress", "busy", "progress", "work", "running"):
        tag = f" ({label})" if label else ""
        print(f"▶ Status: IN-PROGRESS{tag} -> Pulsing Amber backlight")
        set_backlight(
            dev, mode=LIGHT_MODES["breath"], r=255, g=150, b=0, rainbow=False, brightness=4, speed=3
        )
    elif status_name in ("success", "done", "ok", "passed"):
        tag = f" ({label})" if label else ""
        print(f"✔ Status: SUCCESS{tag} -> Emerald Green backlight")
        set_backlight(
            dev, mode=LIGHT_MODES["breath"], r=0, g=230, b=70, rainbow=False, brightness=5, speed=2
        )
        if wait_key:
            print("⏳ Awaiting your next keypress on the keyboard to restore ambient lighting...")
            wait_for_keypress()
            print("⌨ Keypress detected! Restoring ambient idle backlight...")
            apply_idle_backlight(dev)
    elif status_name in ("failed", "error", "fail", "err"):
        tag = f" ({label})" if label else ""
        print(f"✖ Status: FAILED{tag} -> Urgent Red backlight")
        set_backlight(
            dev, mode=LIGHT_MODES["breath"], r=255, g=20, b=20, rainbow=False, brightness=5, speed=4
        )
        if wait_key:
            print("⏳ Awaiting your next keypress on the keyboard to acknowledge error...")
            wait_for_keypress()
            print("⌨ Keypress detected! Restoring ambient idle backlight...")
            apply_idle_backlight(dev)
    elif status_name in ("idle", "normal", "reset", "clear"):
        print("Restoring ambient idle backlight...")
        apply_idle_backlight(dev)
    else:
        print(f"Unknown status '{status_name}'. Valid: in-progress, success, failed, idle")


def run_backlight_demo(dev, dwell_seconds: float = 6.0):
    from .protocol import parse_hex_color

    print("=" * 64)
    print("🌟 Ajazz AK820 Pro Ambient Backlight Showcase")
    print(f"Cycling through {len(SHOWCASE_PRESETS)} curated presets ({dwell_seconds}s each).")
    print("Watch the keyboard on your desk to pick your favorite!")
    print("Press Ctrl+C anytime to stop.")
    print("=" * 64)
    try:
        for idx, p in enumerate(SHOWCASE_PRESETS, start=1):
            r, g, b = parse_hex_color(p["color"])
            print(f"\n[{idx}/{len(SHOWCASE_PRESETS)}] {p['name'].upper()} (`{p['key']}`)")
            print(f"   Mode: {p['mode']} | Color: {p['color']} | Brightness: {p['brightness']}")
            print(f"   Vibe: {p['desc']}")
            set_backlight(
                dev,
                mode=p["mode"],
                r=r,
                g=g,
                b=b,
                rainbow=p["rainbow"],
                brightness=p["brightness"],
                speed=p["speed"],
            )
            end_t = time.time() + dwell_seconds
            while time.time() < end_t:
                time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nDemo interrupted by user.")
    print("\n" + "=" * 64)
    print("Done! To set and save your favorite preset as your default idle backlight:")
    print("   python3 ak820_ctrl.py backlight set --preset <key> --save")
    print("Example: python3 ak820_ctrl.py backlight set --preset warm-white --save")
    print("=" * 64)
