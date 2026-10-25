"""Load-time VALUE coercion for the events config — the m18 choke point's engine.

Split out of `configvalues.py` in v0.34.0 under the §10 modularity budget. The
two modules are the two halves of one contract, and the seam is which side of the
trust boundary they sit on:

* `ak820.coercion` (this module) runs ONLY at the `load_events_config` choke
  point, on data the user already wrote. Its job is to make every consumer's
  type assumptions true — losslessly where a lossless spelling exists, and a
  named `ConfigError` (6) where none does. It never rejects data that works:
  the loader's permissiveness (m27/m28/m29 trust boundary) is a property of
  THIS side of the boundary.
* `ak820.configvalues` holds the entry-point rules (`validate_nonblank`,
  `validate_max_len`, `validate_note_category`) that guard what the CLI is about
  to CREATE, where refusing is cheap and a silent no-op is not.

`validate_note` stayed in `configvalues` because BOTH sides call it — the
`{years}`-only rule is a data rule, not a CLI rule — so `coerce_config_values`
imports it from there and the dependency stays one-way (no cycle).

D22 (v0.24.0) locked the config's SHAPE at the choke point; m18 (REVIEW.md §21)
is the value half of the same coin: otherwise-valid entries with wrong-typed
values crashed every downstream consumer with raw tracebacks — `"title": 123` ->
`.lower()`/`.upper()` AttributeError and a TypeError in the sort key, `"note":
"{years} in {place}"` -> KeyError in `note.format(years=...)`, `"idle_mode":
"1"` -> TypeError in `set_backlight`'s byte assembly (`mode & 0xFF`).

Contract (mirrors D22's taxonomy at the same single choke point):

* JSON scalars coerce losslessly — numbers/bools take their text form for
  string fields; numeric strings and integral floats become ints for the
  idle-backlight fields that feed byte arithmetic.
* `null`, lists and objects have no lossless scalar form -> ConfigError (6),
  the fix-the-config class, naming the offending `events[i].field` location.
* `note` may only interpolate `{years}` (the rule itself lives in
  `configvalues.validate_note`; m28 added the strip-and-return).
* `idle_rainbow` is the one bool-valued field (m21, v0.28.0): bools pass
  through, `0`/`1` ints and `"true"/"false"/"1"/"0"` text coerce losslessly,
  everything else — `"yes"`, `2`, `null` — is ConfigError (6), never a truthy
  guess in `set_backlight`'s `1 if rainbow else 0` byte assembly.
* idle int fields are range-checked against the firmware/CLI bounds (m23,
  v0.30.0): `idle_mode` 0..19 (mirrors `parse_mode`), brightness/speed 0..5
  and direction 0..3 (mirror the argparse choices). A hand-edited 255 used to
  reach the wire as byte 255 and 256 wrapped silently to 0 via `& 0xFF`.

`load_events_config` is the only caller; consumers that inject hand-built dicts
directly (unit-test seams) bypass this and keep their own contract.
"""

from .configvalues import kind, validate_note
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
