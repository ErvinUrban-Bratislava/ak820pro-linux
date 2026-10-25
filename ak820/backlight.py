"""RGB backlight presets, idle-config persistence, `backlight` dispatch (v0.9.0).

Extracted from `ak820_ctrl.py` (§10.5). Depends only on {protocol, transport,
errors, events} — the events-config domain (not the rendering shim) owns the
idle-preset persistence since the v0.25.0 split.
"""

import sys
import time

from . import events, tables
from .errors import ConfigError
from .presets import SHOWCASE_PRESETS  # re-export identity (v0.27.0 §10 data split)
from .protocol import CMD_DELAY, LIGHT_MODES, _cmd_packet, parse_hex_color, parse_mode


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


def get_idle_backlight_cfg(config_path: str | None = None) -> dict:
    """Idle ambient preset (v0.27.0: `config_path` honors the same `--config`
    override every other events-config consumer accepts; None = env/default)."""
    cfg = events.load_events_config(config_path)
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
    config_path: str | None = None,
):
    """Persist the idle ambient preset (v0.27.0: `config_path` = `--config`)."""
    cfg = events.load_events_config(config_path)
    cfg["backlight"] = {
        "idle_preset": idle_preset,
        "idle_mode": mode,
        "idle_color": color,
        "idle_rainbow": rainbow,
        "idle_brightness": brightness,
        "idle_speed": speed,
        "idle_direction": direction,
    }
    events.save_events_config(cfg, config_path)


def apply_idle_backlight(dev, config_path: str | None = None):
    cfg = get_idle_backlight_cfg(config_path)
    preset_key = cfg.get("preset")
    preset = next((p for p in SHOWCASE_PRESETS if p["key"] == preset_key), None)
    if preset:
        r, g, b = parse_hex_color(preset["color"])
        set_backlight(
            dev, preset["mode"], r, g, b, preset["rainbow"], preset["brightness"], preset["speed"]
        )
    else:
        try:
            r, g, b = parse_hex_color(cfg["color"])
        except ValueError as exc:
            # m19 (v0.24.0, §21): hand-edited idle_color -> ConfigError (6), never a raw traceback.
            raise ConfigError(f"Invalid idle backlight color in events config: {exc}") from exc
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
    # m33 (v0.39.0): pad to DISPLAY width, never char count. m34 (v0.40.0):
    # the geometry has ONE home in `tables.format_preset_table` (pure — the
    # catalog data is never touched, only displayed).
    for line in tables.format_preset_table(SHOWCASE_PRESETS):
        print(line)
    print("\nSupported mode names:", ", ".join(LIGHT_MODES.keys()))


def dispatch_backlight(dev, args):
    """`backlight <demo|list|off|set>` subcommand routing (moved out of _main_impl)."""
    action = args.bl_action or "set"
    # v0.27.0 --config parity: the --save paths persist the idle preset into the
    # same override every events-config consumer honors (parent or sub-action
    # position; demo/list never touch the config, the flag just parses uniformly).
    cfg_path = getattr(args, "config", None)
    if action == "demo":
        from .status import run_backlight_demo

        run_backlight_demo(dev, dwell_seconds=args.dwell)
    elif action == "list":
        list_backlight_presets()
    elif action == "off":
        set_backlight(dev, mode=0, brightness=0, save=args.save)
        if args.save:
            save_idle_backlight_cfg(idle_preset="off", mode=0, brightness=0, config_path=cfg_path)
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
                    config_path=cfg_path,
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
                    config_path=cfg_path,
                )
            print(
                f"Applied lighting (mode={mode_val}, color={args.color}, brightness={args.brightness})."
            )
