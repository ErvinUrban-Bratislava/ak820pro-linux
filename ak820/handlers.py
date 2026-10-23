"""Device-bound command handlers + dispatch table (REVIEW.md §2.3, v0.16.0).

Split out of `cli.py` under the §10 modularity budget: every function here
takes `(args, dev)` where `dev` is an opened `AK820ProDevice`, and performs
the actual work for one subcommand. `cli.py` owns offline routing, device
discovery, and the §3.4 exit-code mapping; this module owns the work itself.

Invariant: `cli.DEVICE_HANDLERS is handlers.DEVICE_HANDLERS` (re-export,
not a copy) — locked by `tests/test_modularity_v016.py`.
"""

import subprocess
import sys
import time

from .actions import sync_clock, upload_frames_to_screen, upload_to_screen
from .backlight import dispatch_backlight
from .renderers import render_card, render_hud, render_media, render_radar
from .status import set_status_backlight

# Commands that render/upload the TFT data pipe (0xFF68) in addition to control.
TFT_COMMANDS = ("upload", "radar", "card", "hud", "stats", "media")


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
