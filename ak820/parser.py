"""The argparse surface of the CLI — one module, one responsibility (v0.16.0).

Split out of `cli.py` under the §10 modularity budget: this file defines
*what the CLI accepts* (flags, subcommands, choices) and nothing else.
`build_parser()` reproduces the historical `ak820_ctrl` CLI flag-for-flag;
dispatch and exit-code mapping live in `cli.py`, device-bound handlers in
`handlers.py`.

Invariant: `cli.build_parser is parser.build_parser` (re-export, not a copy)
— locked by `tests/test_modularity_v016.py`.
"""

import argparse
import math

from .protocol import MAX_BRIGHTNESS, MAX_DIRECTION, MAX_LIGHT_MODE, MAX_SPEED


def finite_float(value: str) -> float:
    """argparse type (D24, v0.24.0): any float EXCEPT nan/inf.

    `type=float` happily accepts "nan"/"inf", which crashed downstream with a
    raw ValueError/OverflowError (`radar --seconds nan` -> round(NaN)) or an
    unbounded sleep (`hud --watch inf`). A non-finite numeric flag is a usage
    error -> argparse's friendly message + documented exit 2 (m16 precedent).
    """
    num = float(value)  # ValueError -> argparse turns it into an exit-2 usage error
    if not math.isfinite(num):
        raise argparse.ArgumentTypeError(f"{value!r} is not a finite number")
    return num


def non_negative_float(value: str) -> float:
    """argparse type (m26, v0.30.0): finite_float AND >= 0.

    Durations cannot be negative, and a negative one was silently wrong,
    never loud: `--dwell -5` put the demo's per-preset deadline in the past
    (presets cycled instantly), `--watch -5` failed the `> 0` gate (watch
    silently disabled), `radar --seconds -5` clamped to the 1-frame minimum.
    Reject at parse time — argparse's friendly usage error, documented exit 2
    (the D24/m16 class `finite_float` established; it stays the nan/inf home).
    """
    num = finite_float(value)
    if num < 0:
        raise argparse.ArgumentTypeError(f"{value!r} is negative; this value must be >= 0")
    return num


_CONFIG_HELP = (
    "Path to events JSON config (default: ~/.config/ak820/events.json or $AK820_EVENTS_PATH)"
)


def build_parser():
    """Identical argparse tree to the pre-0.9.0 ak820_ctrl CLI (flag-for-flag)."""
    from .presets import SHOWCASE_PRESETS

    parser = argparse.ArgumentParser(
        description="Ajazz AK820 Pro Linux Screen, Clock & Milestone Radar Controller"
    )
    # Global flags (B6/B7) precede the subcommand: `ak820 --dry-run radar ...`
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose diagnostic logging on stderr (stdout stays machine-readable)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render + report payload digest & chunk count only; send nothing over USB",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("upload", help="Upload image or animated GIF to TFT display")
    p.add_argument("file", help="Path to PNG, JPG, BMP, or GIF file")
    p.add_argument(
        "--fit", choices=["fill", "contain", "stretch"], default="fill", help="Image fit mode"
    )
    p.add_argument(
        "--force", action="store_true", help="Upload even if identical payload is already active"
    )

    p = sub.add_parser("radar", help="Render and upload Family Milestone Radar to the screen")
    p.add_argument(
        "--view",
        choices=["cycle", "hero", "board", "hybrid"],
        default="cycle",
        help="cycle=Hero+Board loop (default), hero=closest event only, board=3-row list, hybrid=Bongo Cat+Radar",
    )
    p.add_argument(
        "--seconds",
        type=non_negative_float,
        default=5.0,
        help="Dwell time in seconds per slide in cycle/hybrid mode (default: 5.0)",
    )
    p.add_argument("--date", help="Override today's date (YYYY-MM-DD) for testing/preview")
    p.add_argument(
        "--save-preview", help="Save rendered PNG preview(s) to directory instead of uploading"
    )
    p.add_argument("--config", help=_CONFIG_HELP)
    p.add_argument(
        "--sync-clock", action="store_true", help="Also synchronize the keyboard RTC clock"
    )
    p.add_argument(
        "--force", action="store_true", help="Upload even if identical payload is already on screen"
    )

    p = sub.add_parser("card", help="Render and upload a custom AI/status card to the screen")
    p.add_argument(
        "--title", required=True, help="Top header banner text (e.g. REMINDER, KODI, AI)"
    )
    p.add_argument(
        "--value", required=True, help="Big center headline text (e.g. 27 DAYS, READY, DONE)"
    )
    p.add_argument("--sub", default="", help="Subtitle line below center box")
    p.add_argument("--footer", default="", help="Bottom footer ticker line")
    p.add_argument(
        "--theme",
        choices=["soon", "warn", "urgent", "today", "calm", "green"],
        default="soon",
        help="Color theme (soon=cyan, warn=gold, urgent=red, today=magenta/gold, calm=blue, green=emerald)",
    )
    p.add_argument(
        "--force", action="store_true", help="Upload even if identical payload is already on screen"
    )

    p = sub.add_parser(
        "hud",
        aliases=["stats"],
        help="Render and upload live PC system stats HUD (CPU, RAM, Temp, Uptime)",
    )
    p.add_argument("--save-preview", help="Save rendered PNG preview to file instead of uploading")
    p.add_argument(
        "--force", action="store_true", help="Upload even if identical payload is already on screen"
    )
    p.add_argument(
        "--watch",
        type=non_negative_float,
        default=0,
        help="Continuously refresh every N seconds (e.g. --watch 15)",
    )

    p = sub.add_parser(
        "media", help="Render and upload Now Playing music/media card (Spotify, browser, VLC)"
    )
    p.add_argument("--save-preview", help="Save rendered PNG preview to file instead of uploading")
    p.add_argument(
        "--force", action="store_true", help="Upload even if identical payload is already on screen"
    )
    p.add_argument(
        "--watch",
        type=non_negative_float,
        default=0,
        help="Continuously refresh every N seconds (e.g. --watch 10)",
    )

    p = sub.add_parser("events", help="List, add, or remove family milestones")
    p.add_argument("--config", help=_CONFIG_HELP)
    ev = p.add_subparsers(dest="events_action", required=False)
    # v0.26.0 CLI surface parity: every events-config consumer accepts --config
    # wherever `radar` does — before AND after the sub-action (`events list
    # --config F` used to be rejected as unrecognized, exit 2). SUPPRESS keeps
    # the parent's value when the flag precedes the sub-action: argparse parses
    # the sub-action into a fresh namespace and copies it over the parent's, so
    # a None default here would silently clobber `events --config F list`.
    ev_l = ev.add_parser("list", help="List upcoming milestones sorted by days left")
    ev_l.add_argument("--json", action="store_true", help="Output JSON for AI tools")
    ev_l.add_argument("--config", default=argparse.SUPPRESS, help=_CONFIG_HELP)
    ev_a = ev.add_parser("add", help="Add or update a milestone in ak820_events.json")
    ev_a.add_argument("--config", default=argparse.SUPPRESS, help=_CONFIG_HELP)
    ev_a.add_argument(
        "--title", required=True, help='Full event title (e.g. "Daughter\'s Birthday")'
    )
    ev_a.add_argument(
        "--short",
        required=True,
        help="Short label for 128x128 screen (max ~10 chars, e.g. DAUGHTER)",
    )
    ev_a.add_argument("--date", required=True, help="Date in YYYY-MM-DD or MM-DD format")
    ev_a.add_argument(
        "--category", choices=["birthday", "anniversary", "milestone"], default="milestone"
    )
    # v0.27.0: `note` was the one event field that required hand-editing JSON.
    # Validated by the same {years}-only rule as the config loader (configvalues).
    ev_a.add_argument(
        "--note",
        default=None,
        help="Note template; only {years} may be interpolated (e.g. 'Shipped {years} yrs "
        "after V1'). Omit to keep an existing entry's note when re-adding.",
    )
    ev_r = ev.add_parser("remove", help="Remove a milestone by id or title")
    ev_r.add_argument("target", help="Event id or title to remove")
    ev_r.add_argument("--config", default=argparse.SUPPRESS, help=_CONFIG_HELP)

    sub.add_parser("sync-clock", help="Sync system date & time to keyboard RTC")
    sub.add_parser("probe", help="Detect keyboard HID interfaces")

    bl = sub.add_parser("backlight", help="Control RGB keyboard backlighting (modes, colors, demo)")
    # v0.27.0 --config parity: the flag parses wherever `radar`/`events` accept
    # it — parent here, SUPPRESS on the sub-actions (argparse copies the
    # sub-action namespace over the parent's, so a None default would clobber
    # `backlight --config F set`; sub-action position wins on conflict). Only
    # the --save paths write the config; demo/list parse-and-ignore the value.
    bl.add_argument("--config", help=_CONFIG_HELP)
    # D21 (v0.24.0, §21): bare `ak820 backlight` used to crash with an
    # AttributeError traceback — the "set" fallback reads attributes (preset,
    # color, ...) that only exist once a sub-action parsed them. Requiring the
    # action makes argparse emit its friendly usage error (exit 2, m16 class).
    bls = bl.add_subparsers(dest="bl_action", required=True)
    d = bls.add_parser(
        "demo", help="Showcase curated ambient lighting presets live on your keyboard"
    )
    d.add_argument(
        "--dwell",
        type=non_negative_float,
        default=6.0,
        help="Seconds to display each preset (default: 6.0)",
    )
    d.add_argument("--config", default=argparse.SUPPRESS, help=_CONFIG_HELP)
    ls = bls.add_parser("list", help="List all lighting modes and showcase presets")
    ls.add_argument("--config", default=argparse.SUPPRESS, help=_CONFIG_HELP)
    s = bls.add_parser("set", help="Set lighting mode, color, brightness or preset")
    s.add_argument(
        "--preset",
        choices=[x["key"] for x in SHOWCASE_PRESETS],
        help="Apply a named showcase preset",
    )
    s.add_argument(
        "--mode",
        help=f"Lighting mode name or integer 0..{MAX_LIGHT_MODE} (e.g. static, breath, spectrum)",
    )
    s.add_argument("--color", default="#FFE0B2", help="Hex color (e.g. #FF8C00, #00E5FF, #FFFFFF)")
    s.add_argument("--rainbow", action="store_true", help="Enable multi-color rainbow mode")
    s.add_argument(
        "--brightness",
        type=int,
        default=4,
        choices=range(MAX_BRIGHTNESS + 1),
        help=f"Brightness level 0..{MAX_BRIGHTNESS} (default: 4)",
    )
    s.add_argument(
        "--speed",
        type=int,
        default=3,
        choices=range(MAX_SPEED + 1),
        help=f"Animation speed 0..{MAX_SPEED} (default: 3)",
    )
    s.add_argument(
        "--direction",
        type=int,
        default=0,
        choices=list(range(MAX_DIRECTION + 1)),
        help="Animation direction (0=Left, 1=Up, 2=Down, 3=Right)",
    )
    s.add_argument(
        "--save",
        action="store_true",
        help="Save as default idle ambient preset in events config & device flash",
    )
    s.add_argument("--config", default=argparse.SUPPRESS, help=_CONFIG_HELP)
    o = bls.add_parser("off", help="Turn keyboard backlighting off")
    o.add_argument("--save", action="store_true", help="Save off state as default in device flash")
    o.add_argument("--config", default=argparse.SUPPRESS, help=_CONFIG_HELP)

    st = sub.add_parser(
        "status", help="Set workflow status lighting (in-progress, success, failed, idle)"
    )
    st.add_argument(
        "state",
        choices=["in-progress", "success", "failed", "idle"],
        help="Target workflow status state",
    )
    st.add_argument("--label", default=None, help="Optional text description of task")
    st.add_argument(
        "--no-wait",
        action="store_true",
        help="Do not wait for keypress to restore idle (exit immediately)",
    )
    # v0.27.0 --config parity: the keypress-restore idle step reads this config.
    st.add_argument("--config", help=_CONFIG_HELP)

    rn = sub.add_parser(
        "run",
        help="Wrap a shell command with live status lighting (in-progress -> success/failed -> keypress wait)",
    )
    rn.add_argument("--label", default=None, help="Optional text label for command")
    rn.add_argument("--no-wait", action="store_true", help="Do not wait for keypress on completion")
    # v0.27.0 --config parity: the completion idle restore reads this config.
    rn.add_argument("--config", help=_CONFIG_HELP)
    rn.add_argument(
        "cmd", nargs=argparse.REMAINDER, help="Command to execute (e.g. -- make, -- ./build.sh)"
    )
    return parser
