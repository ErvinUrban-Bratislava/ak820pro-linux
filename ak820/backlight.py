"""RGB backlight presets, idle-config persistence, `backlight` dispatch (v0.9.0).

Extracted from `ak820_ctrl.py` (§10.5). Depends only on {protocol, transport,
errors} + milestone_radar for the events-config persistence of the idle preset.
"""

import sys
import time
from typing import Any

from .protocol import CMD_DELAY, _cmd_packet, parse_hex_color, parse_mode

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


def set_backlight(
    dev,
    mode: int = 1,
    r: int = 255,
    g: int = 255,
    b: int = 255,
    rainbow: bool = False,
    brightness: int = 4,
    speed: int = 3,
    direction: int = 0,
    save: bool = False,
):
    """Send lighting mode configuration to Interface 3."""
    # 1. START (0x18)
    dev.send_feature(_cmd_packet(0x18, enable=True))
    time.sleep(CMD_DELAY)
    dev.handshake()

    # 2. START_MODE (0x13)
    dev.send_feature(_cmd_packet(0x13, enable=True))
    dev.handshake()

    # 3. MODE_DATA
    data = bytearray(64)
    data[0] = mode & 0xFF
    data[1] = r & 0xFF
    data[2] = g & 0xFF
    data[3] = b & 0xFF
    data[8] = 1 if rainbow else 0
    data[9] = max(0, min(5, brightness))
    data[10] = max(0, min(5, speed))
    data[11] = direction & 0x03
    data[14] = 0x55
    data[15] = 0xAA
    dev.send_feature(data)
    time.sleep(CMD_DELAY)
    dev.handshake()

    # 4. FINISH (0xF0) or SAVE (0x02)
    if save:
        dev.send_feature(_cmd_packet(0x02, enable=False))
    else:
        dev.send_feature(_cmd_packet(0xF0, enable=True))
    dev.handshake()


def get_idle_backlight_cfg() -> dict:
    import milestone_radar as mr

    cfg = mr.load_events_config()
    bl = cfg.get("backlight", {})
    return {
        "preset": bl.get("idle_preset", "warm-white"),
        "mode": bl.get("idle_mode", 1),
        "color": bl.get("idle_color", "#FFE0B2"),
        "rainbow": bl.get("idle_rainbow", False),
        "brightness": bl.get("idle_brightness", 4),
        "speed": bl.get("idle_speed", 3),
        "direction": bl.get("idle_direction", 0),
    }


def save_idle_backlight_cfg(
    idle_preset: str = "warm-white",
    mode: int = 1,
    color: str = "#FFE0B2",
    rainbow: bool = False,
    brightness: int = 4,
    speed: int = 3,
    direction: int = 0,
):
    import milestone_radar as mr

    cfg = mr.load_events_config()
    cfg["backlight"] = {
        "idle_preset": idle_preset,
        "idle_mode": mode,
        "idle_color": color,
        "idle_rainbow": rainbow,
        "idle_brightness": brightness,
        "idle_speed": speed,
        "idle_direction": direction,
    }
    mr.save_events_config(cfg)


def apply_idle_backlight(dev):
    cfg = get_idle_backlight_cfg()
    preset_key = cfg.get("preset")
    preset = next((p for p in SHOWCASE_PRESETS if p["key"] == preset_key), None)
    if preset:
        r, g, b = parse_hex_color(preset["color"])
        set_backlight(
            dev, preset["mode"], r, g, b, preset["rainbow"], preset["brightness"], preset["speed"]
        )
    else:
        r, g, b = parse_hex_color(cfg["color"])
        set_backlight(
            dev,
            cfg["mode"],
            r,
            g,
            b,
            cfg["rainbow"],
            cfg["brightness"],
            cfg["speed"],
            cfg["direction"],
        )


def list_backlight_presets():
    print(f"{'PRESET KEY':<18} {'NAME':<20} {'MODE':<6} {'COLOR':<10} {'DESCRIPTION'}")
    print("-" * 80)
    for p in SHOWCASE_PRESETS:
        print(f"{p['key']:<18} {p['name']:<20} {p['mode']!s:<6} {p['color']:<10} {p['desc']}")
    from .protocol import LIGHT_MODES

    print("\nSupported mode names:", ", ".join(LIGHT_MODES.keys()))


def dispatch_backlight(dev, args):
    """`backlight <demo|list|off|set>` subcommand routing (moved out of _main_impl)."""
    action = args.bl_action or "set"
    if action == "demo":
        from .status import run_backlight_demo

        run_backlight_demo(dev, dwell_seconds=args.dwell)
    elif action == "list":
        list_backlight_presets()
    elif action == "off":
        set_backlight(dev, mode=0, brightness=0, save=args.save)
        if args.save:
            save_idle_backlight_cfg(idle_preset="off", mode=0, brightness=0)
        print("Backlight turned OFF.")
    elif action == "set":
        if args.preset:
            p = next(x for x in SHOWCASE_PRESETS if x["key"] == args.preset)
            r, g, b = parse_hex_color(p["color"])
            set_backlight(
                dev,
                mode=p["mode"],
                r=r,
                g=g,
                b=b,
                rainbow=p["rainbow"],
                brightness=p["brightness"],
                speed=p["speed"],
                save=args.save,
            )
            if args.save:
                save_idle_backlight_cfg(
                    idle_preset=p["key"],
                    mode=p["mode"],
                    color=p["color"],
                    rainbow=p["rainbow"],
                    brightness=p["brightness"],
                    speed=p["speed"],
                )
            print(f"Applied preset '{p['name']}' (`{p['key']}`).")
        else:
            try:
                mode_val = parse_mode(args.mode) if args.mode else 1
            except ValueError as exc:
                # m16 (v0.21.0): a bad flag value is a usage error — documented
                # exit 2 (what argparse itself uses), not undocumented exit 1.
                print(f"Error: {exc}", file=sys.stderr)
                sys.exit(2)
            try:  # m10: `--color banana` must fail friendly, not with a traceback
                r, g, b = parse_hex_color(args.color)
            except ValueError as exc:
                print(f"Error: {exc}", file=sys.stderr)  # m16: exit 2, see above
                sys.exit(2)
            set_backlight(
                dev,
                mode=mode_val,
                r=r,
                g=g,
                b=b,
                rainbow=args.rainbow,
                brightness=args.brightness,
                speed=args.speed,
                direction=args.direction,
                save=args.save,
            )
            if args.save:
                save_idle_backlight_cfg(
                    idle_preset="custom",
                    mode=mode_val,
                    color=args.color,
                    rainbow=args.rainbow,
                    brightness=args.brightness,
                    speed=args.speed,
                    direction=args.direction,
                )
            print(
                f"Applied lighting (mode={mode_val}, color={args.color}, brightness={args.brightness})."
            )
