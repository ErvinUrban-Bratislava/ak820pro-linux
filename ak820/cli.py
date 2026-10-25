"""Exit-code `main()` + offline-first dispatch (REVIEW.md §2.3/§3.4, v0.9.0).

v0.16.0 modularity split: the argparse tree lives in `parser.py` and the
device-bound `(args, dev)` handlers in `handlers.py`; this module keeps the
routing (offline commands, `--dry-run`, device discovery) and the §3.4
exit-code contract, and re-exports both split modules' public names so
`ak820.cli.*`, `ak820.*`, and the `ak820_ctrl.py` shim are unchanged.

Exit-code contract (§3.4, as shipped in `errors.py`): 0 success (incl. dedup
skip), 2 argparse usage error, 3 device not found, 4 permission denied,
5 upload incomplete, 6 invalid events config (D4/D5/D10), 7 protocol desync,
8 input file not found (D11), 9 input file unusable (D12/D14), 75 mid-op
disconnect (EX_TEMPFAIL, systemd-recoverable), 130 user interrupt (D18,
128+SIGINT — friendly line, never a traceback).
"""

import logging
import sys

from .actions import describe_payload
from .backlight import list_backlight_presets
from .config import (
    cmd_card_preview,
    cmd_events,
    cmd_hud_preview,
    cmd_media_preview,
    cmd_radar_preview,
)
from .discovery import find_hidraw_devices  # v0.19.0: sysfs discovery module (§18)
from .errors import AK820Error, DeviceNotFoundError
from .handlers import (  # noqa: F401  (backward-compat re-exports)
    DEVICE_HANDLERS,
    TFT_COMMANDS,
    cmd_backlight,
    cmd_card,
    cmd_hud,
    cmd_media,
    cmd_radar,
    cmd_run,
    cmd_status,
    cmd_sync,
    cmd_upload,
    run_watch_loop,
)
from .parser import build_parser
from .renderers import DRY_RENDERERS
from .transport import AK820ProDevice

log = logging.getLogger("ak820")


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
    if args.command == "card" and getattr(args, "save_preview", None):
        return cmd_card_preview(args)

    # m22 (v0.29.0 re-audit, §25): probe is pure sysfs observation — it opens
    # no hidraw node and sends zero USB bytes — so --dry-run must not swallow
    # its output. `--dry-run probe` used to print "nothing to preview" instead
    # of the interface report, which is the exact thing a dry-run user wants
    # to see before a live command. Route it before the dry-run block.
    if args.command == "probe":
        tft, ctrl = find_hidraw_devices()
        log.debug("hidraw discovery: tft=%s ctrl=%s", tft, ctrl)
        print(f"TFT Data Interface (0xFF68):    {tft or 'NOT FOUND'}")
        print(f"Control/RTC Interface (0xFF13): {ctrl or 'NOT FOUND'}")
        return 0

    # m35 (v0.41.0, CI self-heal): `backlight list` prints the static preset
    # catalog — it opens no device and sends zero USB bytes — yet it routed
    # through discovery + open, so a machine without the keyboard exited 3
    # instead of printing the table (the v0.40 device-free test passed locally
    # only because real hardware was plugged in; CI proved it). Same class as
    # m22's probe: an informational command must not require the device it
    # merely describes. Routed before --dry-run for the same reason — the
    # table IS the command's output, not a payload preview (supersedes
    # v0.29's "nothing to preview" pin for `list`; device-bound non-render
    # actions keep that line).
    if args.command == "backlight" and args.bl_action == "list":
        list_backlight_presets()
        return 0

    # B7: --dry-run renders the exact payload and reports digest + chunk count
    # without ever opening a hidraw node — zero USB traffic, works headless.
    if args.dry_run:
        renderer = DRY_RENDERERS.get(args.command)
        if renderer is None:
            print(
                f"[dry-run] '{args.command}' sends no rendered payload — nothing to preview, nothing sent."
            )
            return 0
        log.debug("dry-run: rendering '%s' with no USB traffic", args.command)
        frames, delays, label = renderer(args)
        return describe_payload(frames, delays, label)

    tft, ctrl = find_hidraw_devices()
    log.debug("hidraw discovery: tft=%s ctrl=%s", tft, ctrl)
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
    FileMissingError -> 8, InvalidFileError -> 9 (D12/D14),
    DeviceDisconnectedError -> 75, argparse usage -> 2,
    KeyboardInterrupt -> 130 (D18: Ctrl+C, no traceback).
    Returns 0 on success (dedup skip included).

    v0.25.0: ConfigError joined the AK820Error taxonomy in `errors.py`, so the
    single `except AK820Error` branch covers exit 6 too — `main()` no longer
    reaches into the `milestone_radar` shim (the package is self-contained).
    """
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    try:
        result = _dispatch(args)
    except AK820Error as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(exc.exit_code)
    except KeyboardInterrupt:
        # D18 (v0.21.0, §20): Ctrl+C during an upload, a keypress wait, or a
        # `run` child dumped a raw traceback. The §3.5 pipe flag is already
        # pessimistically dirty and the digest unrecorded mid-upload, so an
        # interrupt is always safe to re-run. Exit 130 = 128+SIGINT, the shell
        # convention — same status Python's default produced, minus the traceback.
        print("\nInterrupted (Ctrl+C).", file=sys.stderr)
        sys.exit(130)
    if isinstance(result, bool):
        # D20 (v0.24.0, §21): upload_frames_to_screen returns True (uploaded)
        # or False (dedup skip) — BOTH are success (exit 0). bool is an int
        # subclass, so `return result or 0` handed True to the console-script
        # wrapper's sys.exit(True) -> exit 1 on a *successful* upload, while
        # the dedup skip exited 0. §3.4: 0 is the only success status.
        return 0
    return result or 0


if __name__ == "__main__":
    sys.exit(main())
