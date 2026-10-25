"""Per-command render helpers shared by live upload and `--dry-run` (B7, v0.14.0).

Each renderer returns `(frames, delays_ms, label)` — the exact inputs
`actions.upload_frames_to_screen` consumes — so `--dry-run` can produce the
identical payload digest + chunk count without ever opening a hidraw node.
Rendering-domain imports (`cards`, `hud`, `telemetry`) stay function-level
per the §10.2 dependency rule; this module never touches protocol/transport.
"""

import os

from . import configvalues
from .actions import build_radar_frames, load_frames_guarded
from .config import today_from_args
from .errors import FileMissingError
from .screen import image_to_rgb565


def render_upload(args):
    """(frames, delays, label) for `upload <file>` — pure disk + PIL work."""
    if not os.path.exists(args.file):
        # D11 (v0.15.0): typed error -> exit 8, never a raw traceback.
        raise FileMissingError(f"File not found: {args.file}")
    frames, delays = load_frames_guarded(args.file, fit_mode=args.fit)
    return frames, delays, os.path.basename(args.file)


def render_radar(args):
    from . import events  # rendering domain: function-level per §10.2

    today = today_from_args(args)
    cfg = events.load_events_config(getattr(args, "config", None))
    upcoming = events.compute_upcoming_events(cfg, today=today)
    frames, delays = build_radar_frames(args.view, upcoming, today, slide_seconds=args.seconds)
    label = f"radar:{args.view}:{args.seconds}s:{today.isoformat()}"
    return frames, delays, label


def card_image(args):
    """The validated custom-card IMAGE — the one seam live, --dry-run and preview share.

    m31b (v0.36.0): the single validation seam for card text, shared by the
    live upload and --dry-run so both paths fail identically (the D12
    precedent). A line break is unrenderable on the card's one-line boxes —
    the renderer can only flatten it (v0.35.0), which degrades silently —
    so the creation path refuses it (ConfigError 6) instead.

    m32 (v0.37.0): `card --save-preview` joins that seam rather than opening a
    second one. A preview is the same render the user would upload, so it must
    be rejected by the same rule; a preview-only path that skipped validation
    would let `--save-preview` show a card the CLI refuses to send.
    """
    from . import cards  # rendering domain: function-level per §10.2

    title = configvalues.validate_single_line(args.title, f"--title {args.title!r}")
    value = configvalues.validate_single_line(args.value, f"--value {args.value!r}")
    sub = configvalues.validate_single_line(args.sub, f"--sub {args.sub!r}")
    footer = configvalues.validate_single_line(args.footer, f"--footer {args.footer!r}")
    return cards.render_custom_card(title, value, sub, footer, args.theme)


def render_card(args):
    img = card_image(args)
    title, value = args.title, args.value
    sub, footer, theme = args.sub, args.footer, args.theme
    label = f"card:{title}:{value}:{sub}:{footer}:{theme}"
    return [image_to_rgb565(img)], None, label


def render_hud(args):
    from . import hud, telemetry  # render + probe seams: function-level per §10.2

    stats = telemetry.get_system_stats()
    img = hud.render_hud_card(stats)
    label = f"hud:{stats['cpu_pct']}:{stats['ram_pct']}:{stats['temp_c']}:{stats['time']}"
    return [image_to_rgb565(img)], None, label


def render_media(args):
    from . import hud, telemetry  # render + probe seams: function-level per §10.2

    media = telemetry.get_media_status()
    img = hud.render_media_card(media)
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
