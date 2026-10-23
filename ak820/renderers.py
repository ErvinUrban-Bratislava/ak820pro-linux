"""Per-command render helpers shared by live upload and `--dry-run` (B7, v0.14.0).

Each renderer returns `(frames, delays_ms, label)` — the exact inputs
`actions.upload_frames_to_screen` consumes — so `--dry-run` can produce the
identical payload digest + chunk count without ever opening a hidraw node.
Rendering-domain imports (`milestone_radar`, `widgets`) stay function-level
per the §10.2 dependency rule; this module never touches protocol/transport.
"""

import os

from .actions import build_radar_frames
from .config import today_from_args
from .screen import image_to_rgb565, load_frames_from_file


def render_upload(args):
    """(frames, delays, label) for `upload <file>` — pure disk + PIL work."""
    if not os.path.exists(args.file):
        raise FileNotFoundError(f"File not found: {args.file}")
    frames, delays = load_frames_from_file(args.file, fit_mode=args.fit)
    return frames, delays, os.path.basename(args.file)


def render_radar(args):
    import milestone_radar as mr

    today = today_from_args(args)
    cfg = mr.load_events_config(getattr(args, "config", None))
    upcoming = mr.compute_upcoming_events(cfg, today=today)
    frames, delays = build_radar_frames(args.view, upcoming, today, slide_seconds=args.seconds)
    label = f"radar:{args.view}:{args.seconds}s:{today.isoformat()}"
    return frames, delays, label


def render_card(args):
    import milestone_radar as mr

    img = mr.render_custom_card(args.title, args.value, args.sub, args.footer, args.theme)
    label = f"card:{args.title}:{args.value}:{args.sub}:{args.footer}:{args.theme}"
    return [image_to_rgb565(img)], None, label


def render_hud(args):
    import widgets

    stats = widgets.get_system_stats()
    img = widgets.render_hud_card(stats)
    label = f"hud:{stats['cpu_pct']}:{stats['ram_pct']}:{stats['temp_c']}:{stats['time']}"
    return [image_to_rgb565(img)], None, label


def render_media(args):
    import widgets

    media = widgets.get_media_status()
    img = widgets.render_media_card(media)
    label = f"media:{media['status']}:{media['title']}:{media['artist']}"
    return [image_to_rgb565(img)], None, label


# Commands `--dry-run` can preview (everything that renders a TFT payload).
DRY_RENDERERS = {
    "upload": render_upload,
    "radar": render_radar,
    "card": render_card,
    "hud": render_hud,
    "stats": render_hud,
    "media": render_media,
}
