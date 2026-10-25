"""Events-config field VALUE coercion — the m18 choke point (v0.26.0).

D22 (v0.24.0) locked the config's SHAPE at `load_events_config` (top level,
`events` list, entry objects). m18 (REVIEW.md §21) is the value half of the
same coin: otherwise-valid entries with wrong-typed values crashed every
downstream consumer with raw tracebacks — `"title": 123` -> `.lower()`/
`.upper()` AttributeError and a TypeError in the sort key, `"note": "{years}
in {place}"` -> KeyError in `note.format(years=...)`, `"idle_mode": "1"` ->
TypeError in `set_backlight`'s byte assembly (`mode & 0xFF`).

Contract (mirrors D22's taxonomy at the same single choke point):

* JSON scalars coerce losslessly — numbers/bools take their text form for
  string fields; numeric strings and integral floats become ints for the
  idle-backlight fields that feed byte arithmetic.
* `null`, lists and objects have no lossless scalar form -> ConfigError (6),
  the fix-the-config class, naming the offending `events[i].field` location.
* `note` may only interpolate `{years}` — any other placeholder (or unbalanced
  braces) is a ConfigError at load, never a KeyError at render time. m28
  (v0.32.0): `validate_note` also strips incidental surrounding whitespace and
  returns the normalized text, so a hand-edited blank note (`"   "`) becomes
  the documented empty-note fallback ("Milestone") instead of rendering blank.
* `idle_rainbow` is the one bool-valued field (m21, v0.28.0): bools pass
  through, `0`/`1` ints and `"true"/"false"/"1"/"0"` text coerce losslessly,
  everything else — `"yes"`, `2`, `null` — is ConfigError (6), never a truthy
  guess in `set_backlight`'s `1 if rainbow else 0` byte assembly.
* idle int fields are range-checked against the firmware/CLI bounds (m23,
  v0.30.0): `idle_mode` 0..19 (mirrors `parse_mode`), brightness/speed 0..5
  and direction 0..3 (mirror the argparse choices). A hand-edited 255 used to
  reach the wire as byte 255 and 256 wrapped silently to 0 via `& 0xFF`.
* `validate_nonblank` (m27, v0.31.0) is the CLI-side half of the same
  fix-the-config contract: `events add --short`/`--title`/`--note` and the
  `events remove` target must not be empty or whitespace-only (ConfigError 6,
  the D4 precedent). The loader stays permissive for hand-edited data by
  decision — the CLI can never CREATE a blank-field entry, and rejecting data
  the user already wrote would turn every command into exit 6 (see
  `validate_nonblank`'s trust-boundary note and REVIEW.md §28).
* A `note` is rendered for `milestone` entries ONLY (m29, v0.33.0):
  `compute_upcoming_events` never consults it for `birthday`/`anniversary`, so
  `--category birthday --note …` stored a note that could never appear on
  screen. `validate_note_category` rejects that combination at the CLI
  (ConfigError 6) against the RESULTING entry, and `--clear-note` closes the
  gap m28 left open (no CLI path removed a stored note). The category
  vocabulary has ONE home here — `EVENT_CATEGORIES` feeds `parser.py`'s
  `--category` choices and `events.py`'s renderer, `NOTE_INERT_CATEGORIES`
  names the categories that never consult a note — so a new category cannot be
  added to the CLI without this rule accounting for it (the m23 bounds pattern).

`load_events_config` is the only caller of the coercion above; consumers that
inject hand-built dicts directly (unit-test seams) bypass this and keep their
own contract.
"""

import string

from .errors import ConfigError
from .protocol import MAX_BRIGHTNESS, MAX_DIRECTION, MAX_LIGHT_MODE, MAX_SPEED

# String-valued event fields every consumer renders, lowercases or sorts by.
_EVENT_TEXT_FIELDS = ("id", "title", "short_title", "date", "category", "note")
# Idle-backlight fields persisted by `backlight set --save` (backlight.py).
_IDLE_TEXT_FIELDS = ("idle_preset", "idle_color")
# m23 (v0.30.0): every idle int field carries its firmware/CLI bound, and the
# field list is DERIVED from this dict — an unbounded idle int field is no
# longer representable, so m23's root cause (a coerced-but-unbounded int) is
# structurally dead, not just patched. Bounds mirror the CLI exactly:
# `parse_mode` 0..MAX_LIGHT_MODE and the argparse choices 0..5 / 0..3 live in
# protocol.py + parser.py built from the same constants — one home, no drift.
_IDLE_INT_RANGES: dict[str, tuple[int, int]] = {
    "idle_mode": (0, MAX_LIGHT_MODE),
    "idle_brightness": (0, MAX_BRIGHTNESS),
    "idle_speed": (0, MAX_SPEED),
    "idle_direction": (0, MAX_DIRECTION),
}
_IDLE_INT_FIELDS = tuple(_IDLE_INT_RANGES)
# The one bool-valued idle field (m21): it feeds `1 if rainbow else 0` in
# set_backlight's MODE_DATA byte, where any non-empty string is truthy.
_IDLE_BOOL_FIELDS = ("idle_rainbow",)
_TRUE_TEXTS = ("true", "1")
_FALSE_TEXTS = ("false", "0")

# m29 (v0.33.0): the event-category vocabulary has ONE home. `parser.py` builds
# its `--category` choices from `EVENT_CATEGORIES` and `events.py` renders from
# the same names, so a category can never be added to the CLI without the note
# rule below accounting for it (the m23 bounds pattern: one home, no drift).
CATEGORY_BIRTHDAY = "birthday"
CATEGORY_ANNIVERSARY = "anniversary"
CATEGORY_MILESTONE = "milestone"
EVENT_CATEGORIES = (CATEGORY_BIRTHDAY, CATEGORY_ANNIVERSARY, CATEGORY_MILESTONE)
# Categories whose MILESTONE column is FIXED text ("Turns N" / "N Years"): the
# note is never consulted for them, so a stored note is accepted-but-inert —
# exactly the silent no-op m29 refuses to persist.
NOTE_INERT_CATEGORIES = (CATEGORY_BIRTHDAY, CATEGORY_ANNIVERSARY)


def kind(value: object) -> str:
    """JSON-flavoured type name for error messages (null, not NoneType).

    Public (v0.29.0): shared with `events.load_events_config`'s D22/D25/D26
    section-shape checks so every config violation names the offending value
    in the spelling the user typed it with — one vocabulary, one home.
    """
    return "null" if value is None else type(value).__name__


def _as_text(value: object, where: str, path: str) -> str:
    """Scalar -> str (lossless); null/list/object -> ConfigError (6)."""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):  # bool included: int subclass
        return str(value)
    raise ConfigError(f"Events config {path}: {where} must be a string, got {kind(value)}.")


def _as_int(value: object, where: str, path: str, bounds: tuple[int, int] | None = None) -> int:
    """int / integral float / numeric string -> int; anything else -> ConfigError (6).

    m23 (v0.30.0): `bounds=(lo, hi)` rejects out-of-range values at the choke
    point instead of letting `set_backlight`'s byte assembly guess — 255 went
    out as MODE_DATA[0] == 255 (outside the firmware's 0..19 mode range), 256
    wrapped silently to 0 via `& 0xFF`, and brightness/speed/direction were
    clamped or masked (9 -> 5, 7 -> 3). Present-but-wrong is named, never
    guessed — the same stance m18/m21 take on wrong-typed values.
    """
    num: int | None
    if isinstance(value, int):  # bool included: True & 0xFF == 1, harmless byte value
        num = value
    elif isinstance(value, float) and value.is_integer():
        num = int(value)
    elif isinstance(value, str):
        try:
            num = int(value.strip())
        except ValueError:
            num = None
    else:
        num = None
    if num is None:
        raise ConfigError(f"Events config {path}: {where} must be an integer, got {kind(value)}.")
    if bounds is not None and not bounds[0] <= num <= bounds[1]:
        raise ConfigError(
            f"Events config {path}: {where} must be in {bounds[0]}..{bounds[1]}, got {num}."
        )
    return num


def _as_bool(value: object, where: str, path: str) -> bool:
    """bool / int 0|1 / "true"/"false"/"1"/"0" text -> bool; else ConfigError (6).

    m21 (v0.28.0): a hand-edited `"idle_rainbow": "false"` is a non-empty
    string — truthy in `set_backlight`'s byte assembly, so rainbow turned ON
    when the user meant OFF (live-probe proven: MODE_DATA[8] == 1), and `null`
    silently meant OFF instead of a fix-the-config error. Only the lossless
    spellings pass (text case/whitespace-insensitive, mirroring `_as_int`'s
    strip); every other value — `"yes"`, `2`, `1.0`, `null`, list/object —
    names the field and exits 6, never a silent truthy guess.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):  # bool already returned
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_TEXTS:
            return True
        if text in _FALSE_TEXTS:
            return False
    raise ConfigError(f"Events config {path}: {where} must be a boolean, got {kind(value)}.")


def validate_note(note: str, where: str) -> str:
    """Only {years} may be interpolated; returns the note stripped (m28).

    Public seam (v0.27.0): the single home of the `{years}`-only rule, shared
    by the load-time coercion below and `events add --note` CLI validation,
    so the two entry points can never drift. `where` is the full location
    phrase in the message (e.g. "events[0].note" or "--note").

    m28 (v0.32.0): returns the stripped text — the `validate_nonblank` pattern,
    so normalization is single-home and no caller can forget to strip.
    Surrounding whitespace is incidental (m27's class: nothing meaningful
    should be sensitive to it), and a hand-edited `"note": "   "` used to
    survive verbatim and render an EMPTY milestone column — `note_tpl or
    "Milestone"` is truthy for spaces. Stripped to `""` it now takes the
    documented empty-note fallback and renders "Milestone". Internal
    whitespace and the `{years}` placeholder are untouched; only the
    incidental edges go.
    """
    stripped = note.strip()
    try:
        parsed = list(string.Formatter().parse(stripped))
    except ValueError as exc:  # unbalanced / lone brace
        raise ConfigError(f"{where} has malformed braces: {exc}") from exc
    for _, field, _, _ in parsed:
        if field not in (None, "years"):
            raise ConfigError(f"{where} uses {{{field}}}; only {{years}} is supported.")
    return stripped


def validate_nonblank(value: str, where: str) -> str:
    """Require a non-blank value; return the stripped text (m27, v0.31.0).

    Public seam: the single home of the non-blank rule for `events add
    --short`/`--title` and the `events remove` target, so the entry points can
    never drift (the `validate_note` pattern). m27: `--short` is the id's only
    source — `--short " "` stored the degenerate id `"-"` and `--short ""` the
    empty id, each with a blank `short_title` screen label; the `_same_event`
    empty-needle guard kept them from colliding but the entry was unusable
    (radar rendered an empty label). Returning the stripped text keeps the
    normalization single-home too: callers cannot forget to strip, so
    `--short " DAUGHTER "` yields the canonical id `daughter` and label
    `DAUGHTER` instead of the dash-padded `-daughter-` (identity must not be
    sensitive to incidental formatting — m24's class).

    Trust boundary (decided like v0.27.0's render-path decision): the CLI
    enforces non-blank; `load_events_config` stays permissive for hand-edited
    configs — rejecting existing hand-edited data would turn every command
    into exit 6, and the CLI can no longer create such entries.
    """
    stripped = value.strip()
    if not stripped:
        raise ConfigError(f"{where} must not be empty or whitespace-only.")
    return stripped


def validate_note_category(category: str, note: str | None, origin: str) -> None:
    """Reject a note its category can never render (m29, v0.33.0). ConfigError 6.

    `compute_upcoming_events` consults `note` in the milestone branch ONLY —
    for `birthday`/`anniversary` the MILESTONE column is fixed text, so
    `events add --category birthday --note "ignored {years}"` exited 0, stored
    the note, and listed `Turns 1` (live-proven in the v0.32.0 probes). A flag
    that is accepted, persisted and never rendered is a silent no-op; naming it
    at the entry point is the stance every m18/m21/m23/m26/m27/m28 fix takes.

    `origin` says where the note came from, because two paths reach the same
    inert state: an explicit `--note` on a birthday, and a note the m25
    prior-copy PRESERVES when `--category` switches a noted milestone to a
    birthday. The caller therefore validates the RESULTING entry, not just the
    flag, so neither path can slip a note the screen will never show.

    Trust boundary unchanged (m27/m28): the loader stays permissive — a
    hand-edited birthday+note config still loads and renders `Turns N`, since
    rejecting data the user already wrote would brick whole configs. The
    creation path is where the rule lives; `--clear-note` is the escape hatch
    the error message offers for a preserved note.
    """
    if note and category in NOTE_INERT_CATEGORIES:
        raise ConfigError(
            f"{origin} is never rendered for --category {category}: birthdays and "
            f"anniversaries show fixed text in the MILESTONE column. Omit --note "
            f"(or pass --clear-note to drop a stored note), or use "
            f"--category {CATEGORY_MILESTONE}."
        )


def coerce_config_values(cfg: dict, path: str) -> dict:
    """Coerce event + idle-backlight field values in place; raise ConfigError (6).

    Called by `load_events_config` right after the D22 shape checks, so every
    consumer (events list, radar/board renders, backlight idle restore) sees
    the same type-guaranteed dict and the add/remove save paths persist the
    coerced (valid) form instead of propagating the hand-edited original.
    """
    for idx, ev in enumerate(cfg.get("events") or []):
        for field in _EVENT_TEXT_FIELDS:
            if field in ev:
                ev[field] = _as_text(ev[field], f"events[{idx}].{field}", path)
        if "note" in ev:  # coerced to str just above, safe to parse
            # m28 (v0.32.0): the stripped form is persisted, so a hand-edited
            # blank/padded note renders the documented fallback instead of a
            # blank column — normalization at the choke point, not per consumer.
            ev["note"] = validate_note(ev["note"], f"Events config {path}: events[{idx}].note")
    bl = cfg.get("backlight")
    if isinstance(bl, dict):
        for field in _IDLE_TEXT_FIELDS:
            if field in bl:
                bl[field] = _as_text(bl[field], f"backlight.{field}", path)
        for field in _IDLE_INT_FIELDS:
            if field in bl:
                lo, hi = _IDLE_INT_RANGES[field]
                bl[field] = _as_int(bl[field], f"backlight.{field}", path, bounds=(lo, hi))
        for field in _IDLE_BOOL_FIELDS:
            if field in bl:
                bl[field] = _as_bool(bl[field], f"backlight.{field}", path)
    return cfg
