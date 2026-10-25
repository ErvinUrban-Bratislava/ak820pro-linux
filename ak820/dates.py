"""Locale-independent calendar text for drawn strings (v0.38.0 glyph audit).

The audit that closed the media-header tofu (v0.37.0) found a second source
of drawn text no literal lock can see: `strftime("%b %d")` fed the board
header, the hero ticker and every `date_label`, and `%b` is locale-dependent
— a non-C locale can emit accented month abbreviations (`déc.`) that no
catalog-font lock ever inspected, because the string is built at runtime.

DECIDED (v0.38.0): render dates through a fixed C-locale month table — one
home, byte-stable output, ASCII by construction. The alternative (glyph-probe
the formatted string at draw time) would pay a per-frame probe cost in the
`--watch` loop to discover at runtime what this module makes unrepresentable.

Why a leaf module: `cards.py` may never import `events.py` (§10.2, locked
since v0.25.0 — renderers consume plain row dicts) and `events.py` must stay
PIL-free, so the shared table can live in neither consumer. `dates.py` is
PIL-free and imports nothing from the package: a legal dependency of both.
"""

import datetime

# The C/POSIX `%b` abbreviations, byte for byte. `strftime("%b")` under
# LC_ALL=C produces exactly these; the equality is locked by
# tests/test_minors_v038.py, so this table cannot drift from the C locale it
# replaces, and no locale environment variable can move a rendered date.
MONTH_ABBREVS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def format_date_label(d: datetime.date) -> str:
    """`d` as C-locale `"%b %d"` — e.g. `date(2026, 10, 5)` -> `"Oct 05"`.

    Byte-identical to `d.strftime("%b %d")` under the C/POSIX locale (the
    production and CI default) and ASCII for every date, unlike `strftime`
    which follows `LC_TIME` and can emit codepoints the catalog fonts were
    never locked against.
    """
    return f"{MONTH_ABBREVS[d.month - 1]} {d.day:02d}"
