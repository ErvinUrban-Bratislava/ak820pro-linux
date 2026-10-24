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
  braces) is a ConfigError at load, never a KeyError at render time.

`load_events_config` is the only caller; consumers that inject hand-built
dicts directly (unit-test seams) bypass this and keep their own contract.
"""

import string

from .errors import ConfigError

# String-valued event fields every consumer renders, lowercases or sorts by.
_EVENT_TEXT_FIELDS = ("id", "title", "short_title", "date", "category", "note")
# Idle-backlight fields persisted by `backlight set --save` (backlight.py).
_IDLE_TEXT_FIELDS = ("idle_preset", "idle_color")
_IDLE_INT_FIELDS = ("idle_mode", "idle_brightness", "idle_speed", "idle_direction")


def _kind(value: object) -> str:
    """JSON-flavoured type name for error messages (null, not NoneType)."""
    return "null" if value is None else type(value).__name__


def _as_text(value: object, where: str, path: str) -> str:
    """Scalar -> str (lossless); null/list/object -> ConfigError (6)."""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):  # bool included: int subclass
        return str(value)
    raise ConfigError(f"Events config {path}: {where} must be a string, got {_kind(value)}.")


def _as_int(value: object, where: str, path: str) -> int:
    """int / integral float / numeric string -> int; anything else -> ConfigError (6)."""
    if isinstance(value, int):  # bool included: True & 0xFF == 1, harmless byte value
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            pass
    raise ConfigError(f"Events config {path}: {where} must be an integer, got {_kind(value)}.")


def _check_note(note: str, where: str, path: str) -> None:
    """Only {years} may be interpolated (compute_upcoming_events' format call)."""
    try:
        parsed = list(string.Formatter().parse(note))
    except ValueError as exc:  # unbalanced / lone brace
        raise ConfigError(f"Events config {path}: {where} has malformed braces: {exc}") from exc
    for _, field, _, _ in parsed:
        if field not in (None, "years"):
            raise ConfigError(
                f"Events config {path}: {where} uses {{{field}}}; only {{years}} is supported."
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
            _check_note(ev["note"], f"events[{idx}].note", path)
    bl = cfg.get("backlight")
    if isinstance(bl, dict):
        for field in _IDLE_TEXT_FIELDS:
            if field in bl:
                bl[field] = _as_text(bl[field], f"backlight.{field}", path)
        for field in _IDLE_INT_FIELDS:
            if field in bl:
                bl[field] = _as_int(bl[field], f"backlight.{field}", path)
    return cfg
