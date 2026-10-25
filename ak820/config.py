"""Device-free command handlers: `events` CRUD + `--save-preview` renders (v0.9.0).

Everything here runs with no USB access at all — the CLI routes these before
any hidraw discovery, which is also what makes them CI-testable (§10.5).
"""

import datetime
import json
import os

from . import configvalues, events
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


def _same_event(ev: dict, ev_id: str | None, title: str | None) -> bool:
    """One event-identity rule for `add` and `remove` (m24, v0.30.0).

    ids are generated lowercase by `events add --short`, but hand-edited
    configs and typed targets arrive in any case. `remove` used to match id
    case-sensitively while matching title case-insensitively (`events remove
    DAUGHTER` exited 6 next to a working `events remove daughter`), and `add`
    matched both case-sensitively (a re-add with a differently-cased title
    duplicated the entry). Both fields now compare case-insensitively through
    this single home so the two sub-actions can never drift. An empty needle
    never matches: a hand-edited entry with an absent id must not collide
    with `--short ""` or an empty remove target.
    """
    if ev_id and (ev.get("id") or "").lower() == ev_id.lower():
        return True
    return bool(title and (ev.get("title") or "").lower() == title.lower())


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
        # m27 (v0.31.0): --short is the id's only source — an empty or
        # whitespace-only value stored the degenerate id ""/"-" and a blank
        # screen label. Non-blank-after-strip is validated at the CLI
        # (ConfigError 6, the D4/--note precedent); validate_nonblank returns
        # the stripped text, so the canonical id/label normalization is
        # single-home and callers cannot forget to strip.
        short = configvalues.validate_nonblank(args.short, f"--short {args.short!r}")
        # m30 (v0.34.0): the stripped label is then length-bounded against
        # MAX_SHORT_LEN (ConfigError 6) — `--short`'s help promised "max ~10
        # chars" while 20 chars were accepted, and a long label shrinks to
        # illegibility or collides with the row's days pill. Checked on the
        # stripped form so padding never buys extra length.
        short = configvalues.validate_max_len(
            short, f"--short {short!r}", configvalues.MAX_SHORT_LEN
        )
        title = configvalues.validate_nonblank(args.title, f"--title {args.title!r}")
        note = getattr(args, "note", None)
        # m29 (v0.33.0): --clear-note is the only way to REMOVE a stored note —
        # omitting --note preserves it (v0.27.0) and blank is rejected (m28), so
        # JSON hand-editing was the only exit. Combining the two contradicts
        # itself; like every other note rule that is a fix-your-input error (6).
        clear_note = getattr(args, "clear_note", False)
        if clear_note and note is not None:
            raise ConfigError(
                "--note and --clear-note contradict each other: give a note "
                "template or clear the stored one, not both."
            )
        if note is not None:
            # m28 (v0.32.0): a whitespace-only --note was stored verbatim and
            # rendered an empty MILESTONE column (live-proven: `'   '` persisted,
            # blank cell listed). The same non-blank rule as --short/--title/
            # remove-target rejects it here (one home, ConfigError 6) — treating
            # blank as absent would guess at the user's intent and still leave
            # no way to clear a note. validate_note then enforces the
            # {years}-only rule (v0.27.0, never a render KeyError) and returns
            # the stripped text, so incidental padding never reaches the config.
            note = configvalues.validate_nonblank(note, f"--note {note!r}")
            note = configvalues.validate_note(note, f"--note {note!r}")
        ev_id = short.lower().replace(" ", "-")
        old_events = cfg.get("events", [])
        prior = next((e for e in old_events if _same_event(e, ev_id, title)), None)
        # m25 (v0.30.0): rebuilding from the six schema fields dropped unknown
        # hand-added fields (icon, priority, ...) silently on every update —
        # start from the prior entry and overwrite the schema fields instead.
        # This also subsumes the v0.27.0 note preservation: an omitted --note
        # keeps the replaced entry's note via the copy; --note always wins.
        entry = dict(prior) if prior is not None else {}
        entry.update(
            {
                "id": ev_id,
                "title": title,
                "short_title": short.upper(),
                "date": args.date,
                "category": args.category,
            }
        )
        if clear_note:
            # m29 (v0.33.0): the key goes ABSENT, not "" — absence is what "no
            # note" means here (the documented fallback renders "Milestone"
            # either way) and a later plain re-add cannot resurrect it.
            entry.pop("note", None)
        elif note is not None:
            entry["note"] = note
        # m29 (v0.33.0): a note is consulted ONLY for milestone entries, so an
        # inert one is rejected against the RESULTING entry — that covers both an
        # explicit --note on a birthday AND the note the m25 prior-copy preserves
        # when --category switches a noted milestone over to a birthday.
        configvalues.validate_note_category(
            args.category,
            entry.get("note"),
            f"--note {note!r}" if note is not None else "the stored note",
        )
        kept = [e for e in old_events if not _same_event(e, ev_id, title)]
        kept.append(entry)
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
        # m27 (v0.31.0): the target goes through the same non-blank rule as
        # add's flags (one home) — an empty/whitespace target is named as such
        # instead of falling through to the D13 no-match message. Stripping
        # keeps remove symmetric with add's stripped id/title (m24's class:
        # identity must not be sensitive to incidental formatting).
        target = configvalues.validate_nonblank(args.target, f"remove target {args.target!r}")
        before = len(cfg.get("events", []))
        # m24 (v0.30.0): id matched case-sensitively while title matched
        # case-insensitively — one identity rule now, shared with `add`.
        cfg["events"] = [e for e in cfg.get("events", []) if not _same_event(e, target, target)]
        if len(cfg["events"]) == before:
            # D13 (v0.18.0): a no-match remove used to print "Removed 0 event(s)."
            # and exit 0 — a typo looked like success, and the config file was
            # rewritten for nothing. Fail friendly (exit 6) and leave the file alone.
            raise ConfigError(
                f"No event with id or title matching {target!r} — check `ak820 events list`."
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
