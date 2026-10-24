"""Device-free command handlers: `events` CRUD + `--save-preview` renders (v0.9.0).

Everything here runs with no USB access at all — the CLI routes these before
any hidraw discovery, which is also what makes them CI-testable (§10.5).
"""

import datetime
import json
import os

from . import events
from .errors import ConfigError, InvalidFileError


def today_from_args(args):
    """--date override (YYYY-MM-DD) or the real today.

    D10 (v0.15.0): `radar --date banana` used to leak a raw ValueError
    traceback from `date.fromisoformat` — D4 validated `events add --date`
    but never this path. Invalid overrides now raise ConfigError (exit 6),
    the same contract D4 established for malformed event dates.
    """
    raw = getattr(args, "date", None)
    if not raw:
        return datetime.date.today()
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        raise ConfigError(
            f"Invalid --date {raw!r}: expected a YYYY-MM-DD date (e.g. 2026-10-24)."
        ) from None


def cmd_events(args):
    """`events list|add|remove` against the events JSON config (D4: add validates dates)."""
    cfg_path = getattr(args, "config", None)
    cfg = events.load_events_config(cfg_path)
    action = args.events_action or "list"
    if action == "list":
        upcoming = events.compute_upcoming_events(cfg)
        if getattr(args, "json", False):
            for u in upcoming:
                u["next_date"] = u["next_date"].isoformat()
            print(json.dumps(upcoming, indent=2))
        else:
            print(f"{'EVENT':<22} {'NEXT DATE':<12} {'LEFT':<8} {'MILESTONE':<12} {'URGENCY'}")
            print("-" * 68)
            for u in upcoming:
                print(
                    f"{u['title']:<22} {u['next_date']!s:<12} {str(u['days_left']) + 'd':<8} {u['milestone_str']:<12} {u['theme_key']}"
                )
        return 0
    elif action == "add":
        events.parse_event_date(args.date)  # D4: fail fast at the CLI, never store a bad date
        ev_id = args.short.lower().replace(" ", "-")
        old_events = cfg.get("events", [])
        kept = [e for e in old_events if e.get("id") != ev_id and e.get("title") != args.title]
        kept.append(
            {
                "id": ev_id,
                "title": args.title,
                "short_title": args.short.upper(),
                "date": args.date,
                "category": args.category,
            }
        )
        target_path = cfg_path or events.get_default_events_path()
        if kept == old_events:
            # m20 (v0.26.0, §21): an identical re-add used to rewrite the file
            # as a byte-for-byte no-op — mtime churn for timers/backups and a
            # misleading "Saved" line. Nothing changed -> nothing written.
            print(f"Event '{args.title}' ({args.date}) already up to date in {target_path}")
            return 0
        cfg["events"] = kept
        events.save_events_config(cfg, cfg_path)
        print(f"Saved event '{args.title}' ({args.date}) to {target_path}")
        return 0
    elif action == "remove":
        before = len(cfg.get("events", []))
        cfg["events"] = [
            e
            for e in cfg.get("events", [])
            if e.get("id") != args.target and e.get("title", "").lower() != args.target.lower()
        ]
        if len(cfg["events"]) == before:
            # D13 (v0.18.0): a no-match remove used to print "Removed 0 event(s)."
            # and exit 0 — a typo looked like success, and the config file was
            # rewritten for nothing. Fail friendly (exit 6) and leave the file alone.
            raise ConfigError(
                f"No event with id or title matching {args.target!r} — check `ak820 events list`."
            )
        events.save_events_config(cfg, cfg_path)
        print(f"Removed {before - len(cfg['events'])} event(s).")
        return 0
    return 0


def _prepare_preview_file(path: str) -> str:
    """D14 (v0.18.0): `--save-preview FILE` targets must be writable file paths.

    Missing parent directories are created (mirroring `radar --save-preview`'s
    directory semantics — `hud --save-preview out/hud.png` used to leak a raw
    FileNotFoundError); an existing directory passed as a file target becomes
    the typed InvalidFileError (exit 9) instead of PIL's IsADirectoryError.

    D23 (v0.24.0, §21): the makedirs itself can fail (EACCES under a read-only
    parent, EEXIST race, ENOSPC) — same unusable-target class -> exit 9.
    """
    if os.path.isdir(path):
        raise InvalidFileError(
            f"Preview target {path!r} is a directory — give a file path (e.g. {path}/hud.png)."
        )
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    except OSError as exc:
        raise InvalidFileError(
            f"Preview target {path!r} is not writable: {exc.strerror or exc}"
        ) from exc
    return path


def _save_preview(img, path: str) -> None:
    """D23 (v0.24.0): PIL's save() raises raw OSErrors (EACCES, ENOSPC, EROFS)
    on an unwritable target, and a raw ValueError for an unknown/extensionless
    filename (`--save-preview out/x.xyz`) — same unusable-target class as
    D12/D14 -> exit 9."""
    try:
        img.save(path)
    except OSError as exc:
        raise InvalidFileError(f"Cannot write preview {path!r}: {exc.strerror or exc}") from exc
    except ValueError as exc:  # unknown file extension — PIL's own "unusable target"
        raise InvalidFileError(f"Cannot write preview {path!r}: {exc}") from exc


def cmd_radar_preview(args):
    """`radar --save-preview DIR`: hero + board PNGs, no device."""
    from . import cards  # rendering domain: function-level per §10.2

    today = today_from_args(args)
    cfg = events.load_events_config(getattr(args, "config", None))
    upcoming = events.compute_upcoming_events(cfg, today=today)
    try:
        os.makedirs(args.save_preview, exist_ok=True)
    except (FileExistsError, NotADirectoryError) as exc:
        # D14b (v0.18.0): `radar --save-preview <existing-file>` used to leak a
        # raw FileExistsError — a preview target that exists but is not a
        # directory is the same unusable-path class as D12 (exit 9).
        raise InvalidFileError(
            f"Preview target {args.save_preview!r} is not a usable directory — "
            "a file already exists at that path."
        ) from exc
    except OSError as exc:
        # D23 (v0.24.0): unwritable preview directory (EACCES, read-only fs)
        # -> same exit-9 class, never a raw makedirs traceback.
        raise InvalidFileError(
            f"Preview target {args.save_preview!r} is not usable: {exc.strerror or exc}"
        ) from exc
    p1 = os.path.join(args.save_preview, "radar_hero.png")
    p2 = os.path.join(args.save_preview, "radar_board.png")
    _save_preview(cards.render_hero_card(upcoming, today=today), p1)
    _save_preview(cards.render_board_card(upcoming, today=today), p2)
    print(f"Saved previews to {p1} and {p2}")
    return 0


def cmd_hud_preview(args):
    from . import hud  # rendering domain: function-level per §10.2

    img = hud.render_hud_card()
    _save_preview(img, _prepare_preview_file(args.save_preview))
    print(f"Saved HUD preview to {args.save_preview}")
    return 0


def cmd_media_preview(args):
    from . import hud  # rendering domain: function-level per §10.2

    img = hud.render_media_card()
    _save_preview(img, _prepare_preview_file(args.save_preview))
    print(f"Saved media preview to {args.save_preview}")
    return 0
