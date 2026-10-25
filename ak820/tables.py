"""Terminal print tables — the CLI's aligned-text surfaces (m34, v0.40.0).

`events list` and `backlight list` each print a header, a rule, and padded
rows. Before this module the geometry was duplicated magic numbers: column
widths 22/12/8/12 lived in the header literal AND in every `pad` call, and the
separators (`"-" * 68`, `"-" * 80`) were derived from nothing — the headers
they sat under are 65 and 69 cells wide. m23's one-home pattern, applied to
table geometry: every width is a constant here, the header and every padded
column name it, and the rule is as wide as the header it sits under
(`textwidth.rule`), never a second number that can drift.

Why a module of its own: the two print seams (`config.cmd_events`,
`backlight.list_backlight_presets`) sit at the §10 line budget, and table
geometry + row formatting is a distinct responsibility — the print-side
analogue of `screen.py` for the 128x128 surface. The formatters are PURE
(data in, lines out), so the `--json` branch in `config.py` cannot reach the
flattening or padding by construction: stored bytes and JSON keep raw
codepoints (the m27/m28 boundary), and only the printed copy is display-shaped.

Dependency: `tables -> {configvalues, textwidth}`, both stdlib-only leaves.
Never PIL, never a device module — the print seam stays CI-testable (§10.5).
"""

from . import configvalues, textwidth

# The events table geometry — ONE home each (m34). The trailing URGENCY column
# is unbounded (theme keys are the fixed THEMES vocabulary) and never padded.
EVENT_COL_W = 22
DATE_COL_W = 12
LEFT_COL_W = 8
MILESTONE_COL_W = 12
URGENCY_HEADER = "URGENCY"

# The backlight preset table geometry — ONE home each (m34). The trailing
# DESCRIPTION column is catalog-fixed text and never padded.
PRESET_KEY_W = 18
PRESET_NAME_W = 20
MODE_COL_W = 6
COLOR_COL_W = 10
DESCRIPTION_HEADER = "DESCRIPTION"


def format_events_table(upcoming: list[dict]) -> list[str]:
    """`events list` lines: header, rule, one row per event (m32 + m33 + m34).

    Hand-edited breaks/TABs flatten FIRST (one row per event), columns pad to
    DISPLAY cells SECOND (CJK = 2 cells, never char count). Over-wide cells
    stay whole — the row shifts right, marked not amputated (v0.34/v0.36).
    """
    header = (
        f"{textwidth.pad('EVENT', EVENT_COL_W)} {textwidth.pad('NEXT DATE', DATE_COL_W)} "
        f"{textwidth.pad('LEFT', LEFT_COL_W)} {textwidth.pad('MILESTONE', MILESTONE_COL_W)} "
        f"{URGENCY_HEADER}"
    )
    lines = [header, textwidth.rule(header)]
    for u in upcoming:
        title = textwidth.pad(configvalues.flatten_for_display(u["title"]), EVENT_COL_W)
        next_col = textwidth.pad(str(u["next_date"]), DATE_COL_W)
        left_col = textwidth.pad(f"{u['days_left']}d", LEFT_COL_W)
        milestone = textwidth.pad(
            configvalues.flatten_for_display(u["milestone_str"]), MILESTONE_COL_W
        )
        lines.append(f"{title} {next_col} {left_col} {milestone} {u['theme_key']}")
    return lines


def format_preset_table(presets: list[dict]) -> list[str]:
    """`backlight list` lines: header, rule, one row per preset (m33 + m34).

    The catalog data is ASCII today; the rule is what is locked, not the data.
    """
    header = (
        f"{textwidth.pad('PRESET KEY', PRESET_KEY_W)} {textwidth.pad('NAME', PRESET_NAME_W)} "
        f"{textwidth.pad('MODE', MODE_COL_W)} {textwidth.pad('COLOR', COLOR_COL_W)} "
        f"{DESCRIPTION_HEADER}"
    )
    lines = [header, textwidth.rule(header)]
    for p in presets:
        lines.append(
            f"{textwidth.pad(p['key'], PRESET_KEY_W)} {textwidth.pad(p['name'], PRESET_NAME_W)} "
            f"{textwidth.pad(str(p['mode']), MODE_COL_W)} {textwidth.pad(p['color'], COLOR_COL_W)} "
            f"{p['desc']}"
        )
    return lines
