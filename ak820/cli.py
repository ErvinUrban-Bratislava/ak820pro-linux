"""argparse tree + dispatch table + exit-code main() (REVIEW.md §2.3/§3.4, v0.9.0).

`build_parser()` reproduces the historical `ak820_ctrl` CLI flag-for-flag.
`main(argv=None)` routes offline (device-free) commands first, then opens the
HID device and dispatches through `DEVICE_HANDLERS`. Exit-code contract (§3.4,
as shipped in `errors.py`): 0 success (incl. dedup skip), 2 argparse usage
error, 3 device not found, 4 permission denied, 5 upload incomplete,
6 invalid events config (D4/D5/D10), 7 protocol desync, 8 input file not
found (D11), 75 mid-op disconnect (EX_TEMPFAIL, systemd-recoverable).
"""

import argparse
import logging
import subprocess
import sys
import time

from .actions import describe_payload, sync_clock, upload_frames_to_screen, upload_to_screen
from .backlight import dispatch_backlight
from .config import cmd_events, cmd_hud_preview, cmd_media_preview, cmd_radar_preview
from .errors import AK820Error, DeviceNotFoundError
from .renderers import DRY_RENDERERS, render_card, render_hud, render_media, render_radar
from .status import set_status_backlight
from .transport import AK820ProDevice, find_hidraw_devices

log = logging.getLogger("ak820")

# Commands that render/upload the TFT data pipe (0xFF68) in addition to control.
TFT_COMMANDS = ("upload", "radar", "card", "hud", "stats", "media")


def _configure_logging(verbose: bool) -> None:
    """B6: all diagnostics land on stderr; stdout stays reserved for
    machine-readable data (`events list --json` must parse with stderr
    discarded). `--verbose/-v` raises the level from INFO to DEBUG."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        stream=sys.stderr,
        format="%(levelname).1s %(name)s: %(message)s",
        force=True,
    )


def build_parser():
    """Identical argparse tree to the pre-0.9.0 ak820_ctrl CLI (flag-for-flag)."""
    from .backlight import SHOWCASE_PRESETS

    parser = argparse.ArgumentParser(description="Ajazz AK820 Pro Linux Screen, Clock & Milestone Radar Controller")
    # Global flags (B6/B7) precede the subcommand: `ak820 --dry-run radar ...`
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose diagnostic logging on stderr (stdout stays machine-readable)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Render + report payload digest & chunk count only; send nothing over USB")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("upload", help="Upload image or animated GIF to TFT display")
    p.add_argument("file", help="Path to PNG, JPG, BMP, or GIF file")
    p.add_argument("--fit", choices=["fill", "contain", "stretch"], default="fill", help="Image fit mode")
    p.add_argument("--force", action="store_true", help="Upload even if identical payload is already active")

    p = sub.add_parser("radar", help="Render and upload Family Milestone Radar to the screen")
    p.add_argument("--view", choices=["cycle", "hero", "board", "hybrid"], default="cycle",
                   help="cycle=Hero+Board loop (default), hero=closest event only, board=3-row list, hybrid=Bongo Cat+Radar")
    p.add_argument("--seconds", type=float, default=5.0, help="Dwell time in seconds per slide in cycle/hybrid mode (default: 5.0)")
    p.add_argument("--date", help="Override today's date (YYYY-MM-DD) for testing/preview")
    p.add_argument("--save-preview", help="Save rendered PNG preview(s) to directory instead of uploading")
    p.add_argument("--config", help="Path to events JSON config (default: ~/.config/ak820/events.json or $AK820_EVENTS_PATH)")
    p.add_argument("--sync-clock", action="store_true", help="Also synchronize the keyboard RTC clock")
    p.add_argument("--force", action="store_true", help="Upload even if identical payload is already on screen")

    p = sub.add_parser("card", help="Render and upload a custom AI/status card to the screen")
    p.add_argument("--title", required=True, help="Top header banner text (e.g. REMINDER, KODI, AI)")
    p.add_argument("--value", required=True, help="Big center headline text (e.g. 27 DAYS, READY, DONE)")
    p.add_argument("--sub", default="", help="Subtitle line below center box")
    p.add_argument("--footer", default="", help="Bottom footer ticker line")
    p.add_argument("--theme", choices=["soon", "warn", "urgent", "today", "calm", "green"], default="soon",
                   help="Color theme (soon=cyan, warn=gold, urgent=red, today=magenta/gold, calm=blue, green=emerald)")
    p.add_argument("--force", action="store_true", help="Upload even if identical payload is already on screen")

    p = sub.add_parser("hud", aliases=["stats"], help="Render and upload live PC system stats HUD (CPU, RAM, Temp, Uptime)")
    p.add_argument("--save-preview", help="Save rendered PNG preview to file instead of uploading")
    p.add_argument("--force", action="store_true", help="Upload even if identical payload is already on screen")
    p.add_argument("--watch", type=float, default=0, help="Continuously refresh every N seconds (e.g. --watch 15)")

    p = sub.add_parser("media", help="Render and upload Now Playing music/media card (Spotify, browser, VLC)")
    p.add_argument("--save-preview", help="Save rendered PNG preview to file instead of uploading")
    p.add_argument("--force", action="store_true", help="Upload even if identical payload is already on screen")
    p.add_argument("--watch", type=float, default=0, help="Continuously refresh every N seconds (e.g. --watch 10)")

    p = sub.add_parser("events", help="List, add, or remove family milestones")
    p.add_argument("--config", help="Path to events JSON config (default: ~/.config/ak820/events.json or $AK820_EVENTS_PATH)")
    ev = p.add_subparsers(dest="events_action", required=False)
    ev_l = ev.add_parser("list", help="List upcoming milestones sorted by days left")
    ev_l.add_argument("--json", action="store_true", help="Output JSON for AI tools")
    ev_a = ev.add_parser("add", help="Add or update a milestone in ak820_events.json")
    ev_a.add_argument("--title", required=True, help="Full event title (e.g. \"Daughter's Birthday\")")
    ev_a.add_argument("--short", required=True, help="Short label for 128x128 screen (max ~10 chars, e.g. DAUGHTER)")
    ev_a.add_argument("--date", required=True, help="Date in YYYY-MM-DD or MM-DD format")
    ev_a.add_argument("--category", choices=["birthday", "anniversary", "milestone"], default="milestone")
    ev_r = ev.add_parser("remove", help="Remove a milestone by id or title")
    ev_r.add_argument("target", help="Event id or title to remove")

    sub.add_parser("sync-clock", help="Sync system date & time to keyboard RTC")
    sub.add_parser("probe", help="Detect keyboard HID interfaces")

    bl = sub.add_parser("backlight", help="Control RGB keyboard backlighting (modes, colors, demo)")
    bls = bl.add_subparsers(dest="bl_action", required=False)
    d = bls.add_parser("demo", help="Showcase curated ambient lighting presets live on your keyboard")
    d.add_argument("--dwell", type=float, default=6.0, help="Seconds to display each preset (default: 6.0)")
    bls.add_parser("list", help="List all lighting modes and showcase presets")
    s = bls.add_parser("set", help="Set lighting mode, color, brightness or preset")
    s.add_argument("--preset", choices=[x["key"] for x in SHOWCASE_PRESETS], help="Apply a named showcase preset")
    s.add_argument("--mode", help="Lighting mode name or integer 0..19 (e.g. static, breath, spectrum)")
    s.add_argument("--color", default="#FFE0B2", help="Hex color (e.g. #FF8C00, #00E5FF, #FFFFFF)")
    s.add_argument("--rainbow", action="store_true", help="Enable multi-color rainbow mode")
    s.add_argument("--brightness", type=int, default=4, choices=range(0, 6), help="Brightness level 0..5 (default: 4)")
    s.add_argument("--speed", type=int, default=3, choices=range(0, 6), help="Animation speed 0..5 (default: 3)")
    s.add_argument("--direction", type=int, default=0, choices=[0, 1, 2, 3], help="Animation direction (0=Left, 1=Up, 2=Down, 3=Right)")
    s.add_argument("--save", action="store_true", help="Save as default idle ambient preset in events config & device flash")
    o = bls.add_parser("off", help="Turn keyboard backlighting off")
    o.add_argument("--save", action="store_true", help="Save off state as default in device flash")

    st = sub.add_parser("status", help="Set workflow status lighting (in-progress, success, failed, idle)")
    st.add_argument("state", choices=["in-progress", "success", "failed", "idle"], help="Target workflow status state")
    st.add_argument("--label", default=None, help="Optional text description of task")
    st.add_argument("--no-wait", action="store_true", help="Do not wait for keypress to restore idle (exit immediately)")

    rn = sub.add_parser("run", help="Wrap a shell command with live status lighting (in-progress -> success/failed -> keypress wait)")
    rn.add_argument("--label", default=None, help="Optional text label for command")
    rn.add_argument("--no-wait", action="store_true", help="Do not wait for keypress on completion")
    rn.add_argument("cmd", nargs=argparse.REMAINDER, help="Command to execute (e.g. -- make, -- ./build.sh)")
    return parser


def run_watch_loop(push_fn, interval, label=""):
    """One shared --watch loop (replaces the two copy-pasted while-True blocks).

    A mid-loop DeviceDisconnectedError propagates to main()'s §3.4 mapping,
    so "keyboard unplugged" exits with a friendly line instead of a traceback.
    """
    print(f"Watching {label} (refreshing every {interval}s, Ctrl+C to stop)...")
    try:
        while True:
            time.sleep(interval)
            push_fn()
    except KeyboardInterrupt:
        print("\nStopped.")


# --- device-bound handlers (args, dev) ---------------------------------------
def cmd_upload(args, dev):
    upload_to_screen(dev, args.file, args.fit, force=args.force)


def cmd_sync(args, dev):
    sync_clock(dev)


def cmd_radar(args, dev):
    if args.sync_clock:
        sync_clock(dev)
    frames, delays, label = render_radar(args)
    upload_frames_to_screen(dev, frames, delays, label=label, force=args.force)


def cmd_card(args, dev):
    frames, delays, label = render_card(args)
    upload_frames_to_screen(dev, frames, delays, label=label, force=args.force)


def cmd_hud(args, dev):
    def push():
        frames, delays, label = render_hud(args)
        upload_frames_to_screen(dev, frames, delays, label=label, force=args.force)

    push()
    if getattr(args, "watch", 0) > 0:
        run_watch_loop(push, args.watch, "system HUD")


def cmd_media(args, dev):
    def push():
        frames, delays, label = render_media(args)
        upload_frames_to_screen(dev, frames, delays, label=label, force=args.force)

    push()
    if getattr(args, "watch", 0) > 0:
        run_watch_loop(push, args.watch, "media player")


def cmd_backlight(args, dev):
    dispatch_backlight(dev, args)


def cmd_status(args, dev):
    set_status_backlight(dev, args.state, label=args.label, wait_key=not args.no_wait)


def cmd_run(args, dev):
    cmd = args.cmd
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        sys.exit("Error: No command specified to run.")
    set_status_backlight(dev, "in-progress", label=args.label)
    res = subprocess.run(cmd)
    if res.returncode == 0:
        set_status_backlight(dev, "success", label=args.label, wait_key=not args.no_wait)
    else:
        set_status_backlight(dev, "failed", label=args.label, wait_key=not args.no_wait)
        sys.exit(res.returncode)


DEVICE_HANDLERS = {
    "upload": cmd_upload,
    "radar": cmd_radar,
    "card": cmd_card,
    "hud": cmd_hud,
    "stats": cmd_hud,
    "media": cmd_media,
    "sync-clock": cmd_sync,
    "backlight": cmd_backlight,
    "status": cmd_status,
    "run": cmd_run,
}


def _dispatch(args):
    """Offline-first routing, then device discovery + HANDLERS dispatch (§2.3)."""
    if args.command == "events":
        return cmd_events(args)
    if args.command == "radar" and args.save_preview:
        return cmd_radar_preview(args)
    if args.command in ("hud", "stats") and getattr(args, "save_preview", None):
        return cmd_hud_preview(args)
    if args.command == "media" and getattr(args, "save_preview", None):
        return cmd_media_preview(args)

    # B7: --dry-run renders the exact payload and reports digest + chunk count
    # without ever opening a hidraw node — zero USB traffic, works headless.
    if args.dry_run:
        renderer = DRY_RENDERERS.get(args.command)
        if renderer is None:
            print(f"[dry-run] '{args.command}' sends no rendered payload — nothing to preview, nothing sent.")
            return 0
        log.debug("dry-run: rendering '%s' with no USB traffic", args.command)
        frames, delays, label = renderer(args)
        return describe_payload(frames, delays, label)

    tft, ctrl = find_hidraw_devices()
    log.debug("hidraw discovery: tft=%s ctrl=%s", tft, ctrl)
    if args.command == "probe":
        print(f"TFT Data Interface (0xFF68):    {tft or 'NOT FOUND'}")
        print(f"Control/RTC Interface (0xFF13): {ctrl or 'NOT FOUND'}")
        return 0
    if not ctrl or (args.command in TFT_COMMANDS and not tft):
        raise DeviceNotFoundError(
            "AK820 Pro USB HID interfaces not found. Ensure the keyboard is connected via USB cable."
        )
    with AK820ProDevice(tft, ctrl) as dev:
        return DEVICE_HANDLERS[args.command](args, dev)


def main(argv=None) -> int:
    """Console entry point (ak820 / ak820-ctrl scripts, ak820_ctrl shim).

    Exit-code contract (§3.4 + D4/D5/D8/D10/D11): UploadIncompleteError -> 5,
    ConfigError (invalid event date or --date override) -> 6,
    DeviceNotFoundError -> 3, PermissionDeniedError -> 4, ProtocolError -> 7,
    FileMissingError -> 8, DeviceDisconnectedError -> 75, argparse usage -> 2.
    Returns 0 on success (dedup skip included).
    """
    import milestone_radar as mr

    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    try:
        result = _dispatch(args)
    except AK820Error as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(exc.exit_code)
    except mr.ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(mr.ConfigError.exit_code)
    return result or 0


if __name__ == "__main__":
    sys.exit(main())
