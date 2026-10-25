"""Events-config domain: load/validate/save + countdown computation (v0.25.0).

Split out of `milestone_radar.py` under the §10 modularity budget. This module
owns the *data* half of the radar domain — events.json path resolution, shape
validation (m7/D22), date parsing (D4/D5), urgency thresholds (D7), and the
countdown rows every consumer renders — and imports NO rendering code: PIL
never loads on `events list|add|remove`, backlight idle-preset persistence, or
any config-only path. The pixel half lives in `ak820.cards`.

`milestone_radar.py` re-exports every name here unchanged (identity, not copy).
"""

import contextlib
import datetime
import json
import os
import tempfile

from . import configvalues
from .errors import ConfigError


def get_default_events_path() -> str:
    """Return default path to ak820 events config with env/legacy/XDG fallback."""
    if "AK820_EVENTS_PATH" in os.environ:
        return os.path.expanduser(os.environ["AK820_EVENTS_PATH"])
    legacy = os.path.expanduser("~/personal-data/ak820_events.json")
    if os.path.exists(legacy):
        return legacy
    xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(xdg_config, "ak820", "events.json")


# m5 (closed v0.12.0): the old module-level DEFAULT_EVENTS_PATH froze
# $AK820_EVENTS_PATH at import time and was never referenced — deleted.
# Always call get_default_events_path() so env changes are honored.


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _safe_date(year: int, month: int, day: int) -> datetime.date:
    """Build a date, clamping Feb 29 -> Feb 28 ONLY on non-leap years (D5).

    The previous blanket `except ValueError` fallback assumed Feb-29-on-non-leap
    was the only possible failure and itself crashed on any other invalid
    component (e.g. month 13 -> `date(y, 13, 28)` -> ValueError). Every other
    invalid combination now raises ConfigError with the offending components.
    """
    try:
        return datetime.date(year, month, day)
    except ValueError:
        if (month, day) == (2, 29):  # the ONLY expected failure mode
            return datetime.date(year, 2, 28)
        raise ConfigError(f"Invalid event date components: {year:04d}-{month:02d}-{day:02d}")


def parse_event_date(raw: str) -> tuple[int | None, int, int]:
    """Validate an event date at input time (D4): 'YYYY-MM-DD' or 'MM-DD'.

    Returns (year, month, day); year is None for recurring MM-DD dates.
    Raises ConfigError with an informative message on anything else, so
    `events add` fails fast and bad data never reaches render time.
    """
    if not isinstance(raw, str):
        raise ConfigError(f"Invalid date {raw!r}: expected YYYY-MM-DD or MM-DD")
    parts = raw.split("-")
    try:
        if len(parts) == 3:
            y, m, d = (int(p) for p in parts)
            datetime.date(y, m, d)
            return y, m, d
        if len(parts) == 2:
            m, d = (int(p) for p in parts)
            datetime.date(2024, m, d)  # 2024 is a leap year: validates Feb 29
            return None, m, d
    except ValueError:
        pass
    raise ConfigError(f"Invalid date {raw!r}: expected YYYY-MM-DD or MM-DD")


def load_events_config(config_path: str | None = None) -> dict:
    """Load the events config, validating its SHAPE at this single choke point.

    m7 (v0.12.0) covered corrupt JSON only. D22 (v0.24.0, §21) closes the rest
    of the raw-traceback family: a config that is a directory or unreadable
    (OSError), valid JSON of the wrong shape (top level not an object, `events`
    not a list, an entry not an object) used to crash every consumer
    (`cfg.get`/`ev.get` AttributeError, IsADirectoryError) with exit 1. All of
    them are fix-the-config errors -> ConfigError (exit 6), the D4/D5/m7 class.

    D25/D26 (v0.29.0 re-audit, §25) extend the same section-shape rule to the
    two violations D22's guard left open: `"events": null` passed the
    `is not None` guard but every consumer reads `cfg.get("events", [])` —
    which returns the null, not the default — and crashed with a raw TypeError
    (exit 1); a non-object `backlight` section (string/list/number/null) was
    never checked at all and crashed every idle-restore consumer
    (`get_idle_backlight_cfg`'s `bl.get`) with a raw AttributeError. A present
    section must have its declared type — missing stays fine (defaults apply),
    null is named (`got null`), never guessed into an empty list/object.
    """
    path = config_path or get_default_events_path()
    if not os.path.exists(path):
        return {"default_mode": "radar", "default_preset": "bongo", "events": []}
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except json.JSONDecodeError as exc:
        # m7 (closed v0.12.0): corrupt JSON is a ConfigError (exit 6) with the
        # offending path, not a raw JSONDecodeError traceback.
        raise ConfigError(f"Invalid JSON in events config {path}: {exc}") from exc
    except OSError as exc:
        # D22: directory-as-config (EISDIR), EACCES, or a vanish race — same
        # fix-the-config class as corrupt JSON.
        raise ConfigError(f"Cannot read events config {path}: {exc.strerror or exc}") from exc
    if not isinstance(cfg, dict):
        raise ConfigError(
            f"Events config {path} must contain a JSON object, got {configvalues.kind(cfg)}."
        )
    # D26 (v0.29.0): `in cfg`, not `is not None` — a present-but-null `events`
    # reaches consumers through `cfg.get("events", [])` as None (the default
    # only covers an ABSENT key) and crashes every countdown/add loop.
    if "events" in cfg and not isinstance(cfg["events"], list):
        raise ConfigError(
            f"Events config {path}: 'events' must be a list, got {configvalues.kind(cfg['events'])}."
        )
    for idx, ev in enumerate(cfg.get("events") or []):
        if not isinstance(ev, dict):
            raise ConfigError(
                f"Events config {path}: events[{idx}] must be an object, "
                f"got {configvalues.kind(ev)}."
            )
    # D25 (v0.29.0): the idle-backlight section is read with `.get` by every
    # idle restore (status/run/backlight) — a non-object here is the same
    # fix-the-config class, named at load instead of an AttributeError at use.
    if "backlight" in cfg and not isinstance(cfg["backlight"], dict):
        raise ConfigError(
            f"Events config {path}: 'backlight' must be an object, "
            f"got {configvalues.kind(cfg['backlight'])}."
        )
    # m18 (v0.26.0, §21): value-type coercion at the same choke point as the
    # D22 shape checks — wrong-typed field values (int title, null category,
    # stray {placeholder} note, string idle_mode) never reach a consumer.
    return configvalues.coerce_config_values(cfg, path)


def save_events_config(cfg: dict, config_path: str | None = None):
    """Atomically write the events config (m6, closed v0.12.0).

    mkstemp + os.replace means a crash mid-write can never leave a truncated
    config behind, and a bare relative filename (dirname == "") resolves to
    "." instead of raising in os.makedirs.

    D22 (v0.24.0): an unwritable target (EACCES parent, read-only fs) is a
    fix-the-config error too -> ConfigError (exit 6), never a raw traceback.
    """
    path = config_path or get_default_events_path()
    parent = os.path.dirname(path) or "."
    try:
        os.makedirs(parent, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=".events-", suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            os.replace(tmp_path, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise
    except OSError as exc:
        raise ConfigError(f"Cannot write events config {path}: {exc.strerror or exc}") from exc


def _threshold(cfg: dict, key: str, default: int) -> int:
    """Read an urgency threshold from config (D7), falling back on invalid values."""
    try:
        value = int(cfg.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def compute_upcoming_events(cfg: dict, today: datetime.date | None = None) -> list[dict]:
    """Countdown rows for every event in `cfg`, sorted by (days_left, title).

    Library trust boundary (v0.27.0 decision): the type/shape guarantees this
    function relies on (string text fields, `{years}`-only notes, parseable
    dates) are established ONCE at the load choke point — `load_events_config`
    runs the D22 shape checks and the m18 `configvalues` coercion before any
    consumer sees a dict. Callers that hand-build dicts and inject them here
    directly (unit-test seams, embedding tools) bypass that choke point and own
    their own contract: pass the coerced form (strings for text fields, valid
    dates) or load through `load_events_config`. No second guard is duplicated
    in this hot path — the single choke point is the invariant's only home
    (D22/m18 architecture), and duplicating it here would only drift.
    """
    if today is None:
        today = datetime.date.today()

    # D7: honor configurable urgency thresholds (defaults match previous hardcodes)
    urgent_days = _threshold(cfg, "urgent_days", 3)
    warn_days = _threshold(cfg, "warn_days", 14)
    soon_days = _threshold(cfg, "soon_days", 35)

    upcoming = []
    for ev in cfg.get("events", []):
        raw_date = ev.get("date", "")
        orig_year, month, day = parse_event_date(raw_date)  # D4: friendly error, no traceback
        if orig_year is None:
            orig_year = today.year

        next_date = _safe_date(today.year, month, day)  # D5: narrow Feb-29 clamp only
        if next_date < today:
            next_date = _safe_date(today.year + 1, month, day)

        days_left = (next_date - today).days
        years = next_date.year - orig_year
        category = ev.get("category", "milestone")

        if category == "birthday":
            milestone_str = f"Turns {years}" if years > 0 else "Birthday"
            short_age = _ordinal(years) if years > 0 else "B-Day"
        elif category == "anniversary":
            milestone_str = f"{years} Years" if years > 0 else "Anniversary"
            short_age = f"{years} yrs" if years > 0 else "Anniv"
        else:
            note_tpl = ev.get("note", "")
            milestone_str = (
                note_tpl.format(years=years) if "{years}" in note_tpl else (note_tpl or "Milestone")
            )
            short_age = milestone_str

        if days_left == 0:
            theme_key = "today"
        elif days_left <= urgent_days:
            theme_key = "urgent"
        elif days_left <= warn_days:
            theme_key = "warn"
        elif days_left <= soon_days:
            theme_key = "soon"
        else:
            theme_key = "calm"

        upcoming.append(
            {
                "id": ev.get("id", ev.get("title", "event").lower().replace(" ", "-")),
                "title": ev.get("title", "Milestone"),
                "short_title": ev.get("short_title", ev.get("title", "EVENT").upper()[:11]),
                "orig_date": raw_date,
                "next_date": next_date,
                "date_label": next_date.strftime("%b %d"),
                "days_left": days_left,
                "years": years,
                "category": category,
                "milestone_str": milestone_str,
                "short_age": short_age,
                "theme_key": theme_key,
            }
        )

    upcoming.sort(key=lambda x: (x["days_left"], x["title"]))
    return upcoming
