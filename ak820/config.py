"""Device-free command handlers: `events` CRUD + `--save-preview` renders (v0.9.0).

Everything here runs with no USB access at all — the CLI routes these before
any hidraw discovery, which is also what makes them CI-testable (§10.5).
"""

import datetime
import json
import os


def today_from_args(args):
    """--date override (YYYY-MM-DD) or the real today.

    D10 (v0.15.0): `radar --date banana` used to leak a raw ValueError
    traceback from `date.fromisoformat` — D4 validated `events add --date`
    but never this path. Invalid overrides now raise ConfigError (exit 6),
    the same contract D4 established for malformed event dates.
    """
    import milestone_radar as mr

    raw = getattr(args, "date", None)
    if not raw:
        return datetime.date.today()
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        raise mr.ConfigError(
            f"Invalid --date {raw!r}: expected a YYYY-MM-DD date (e.g. 2026-10-24)."
        ) from None


def cmd_events(args):
    """`events list|add|remove` against the events JSON config (D4: add validates dates)."""
    import milestone_radar as mr

    cfg_path = getattr(args, "config", None)
    cfg = mr.load_events_config(cfg_path)
    action = args.events_action or "list"
    if action == "list":
        upcoming = mr.compute_upcoming_events(cfg)
        if getattr(args, "json", False):
            for u in upcoming:
                u["next_date"] = u["next_date"].isoformat()
            print(json.dumps(upcoming, indent=2))
        else:
            print(f"{'EVENT':<22} {'NEXT DATE':<12} {'LEFT':<8} {'MILESTONE':<12} {'URGENCY'}")
            print("-" * 68)
            for u in upcoming:
                print(f"{u['title']:<22} {str(u['next_date']):<12} {str(u['days_left']) + 'd':<8} {u['milestone_str']:<12} {u['theme_key']}")
        return 0
    elif action == "add":
        mr.parse_event_date(args.date)  # D4: fail fast at the CLI, never store a bad date
        ev_id = args.short.lower().replace(" ", "-")
        events = [e for e in cfg.get("events", []) if e.get("id") != ev_id and e.get("title") != args.title]
        events.append({
            "id": ev_id,
            "title": args.title,
            "short_title": args.short.upper(),
            "date": args.date,
            "category": args.category,
        })
        cfg["events"] = events
        mr.save_events_config(cfg, cfg_path)
        target_path = cfg_path or mr.get_default_events_path()
        print(f"Saved event '{args.title}' ({args.date}) to {target_path}")
        return 0
    elif action == "remove":
        before = len(cfg.get("events", []))
        cfg["events"] = [
            e for e in cfg.get("events", [])
            if e.get("id") != args.target and e.get("title", "").lower() != args.target.lower()
        ]
        mr.save_events_config(cfg, cfg_path)
        print(f"Removed {before - len(cfg['events'])} event(s).")
        return 0
    return 0


def cmd_radar_preview(args):
    """`radar --save-preview DIR`: hero + board PNGs, no device."""
    import milestone_radar as mr

    today = today_from_args(args)
    cfg = mr.load_events_config(getattr(args, "config", None))
    upcoming = mr.compute_upcoming_events(cfg, today=today)
    os.makedirs(args.save_preview, exist_ok=True)
    p1 = os.path.join(args.save_preview, "radar_hero.png")
    p2 = os.path.join(args.save_preview, "radar_board.png")
    mr.render_hero_card(upcoming, today=today).save(p1)
    mr.render_board_card(upcoming, today=today).save(p2)
    print(f"Saved previews to {p1} and {p2}")
    return 0


def cmd_hud_preview(args):
    import widgets

    img = widgets.render_hud_card()
    img.save(args.save_preview)
    print(f"Saved HUD preview to {args.save_preview}")
    return 0


def cmd_media_preview(args):
    import widgets

    img = widgets.render_media_card()
    img.save(args.save_preview)
    print(f"Saved media preview to {args.save_preview}")
    return 0
