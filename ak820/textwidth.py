"""Terminal display width at the print seam (m33, v0.39.0; m36, v0.42.0).

`events list` and `backlight list` padded their columns with `str.ljust`-style
format specs — padding by CHARACTER COUNT, the exact proxy m31b rejected for
the 128x128 screen (v0.36.0). East-Asian-wide codepoints occupy 2 terminal
cells per character, so one CJK title shifted every following column of its
row (live-reproved: `お盆休み` — 4 chars, 8 cells — pushed NEXT DATE 4
cells past its header).
The data is fine — this is purely a print-seam bug: `--json` and the stored
bytes keep the raw text (the m27/m28 boundary again).

DECIDED (v0.39.0): pad by DISPLAY WIDTH computed from
`unicodedata.east_asian_width` — one home, stdlib-only, no new dependency.
The alternative (documenting the ragged table as accepted) would pin a defect
the screen side already rejected; the char-count proxy class is closed, not
relocated to a different surface.

Width rule (deterministic, terminal-conventional):

* East Asian Wide (`W`) and Fullwidth (`F`) -> 2 cells.
* Combining marks (categories `Mn`/`Me`) and the explicit zero-width
  codepoints -> 0 cells; they ride on the preceding cell.
* Everything else -> 1 cell. Ambiguous (`A`) counts as 1: that is what
  Western terminals — this project's production locale — render. A CJK
  locale configured to render ambiguous as 2 is a terminal setting, not a
  data bug, and no shipped string depends on the difference.

Clusters, not codepoints (m36, v0.42.0): the cell is the unit a terminal
advances, and several codepoints share one. `display_width` therefore scans
CLUSTERS — a base codepoint plus everything that rides on it (combining marks,
variation selectors, skin-tone modifiers) — and collapses a U+200D-joined run
that contains an emoji into its widest member. Live-reproved before fixing:
`Family 👨\u200d👩\u200d👧 Reunion` measured 21 cells while the joined family glyph renders
as 17, shifting every following cell of that row LEFT (the mirror of m33's CJK
shift, which moved them RIGHT).
A joiner binds nothing across whitespace: `👨\u200d 👩` is three separate glyphs (2 + 1 + 2
cells), and the flatten seam creates exactly that shape when a hand-edited
config breaks a joined run mid-sequence (live-reproved: `👨\u200d\n👩` flattened to
`👨\u200d 👩` and measured 4, shifting the row LEFT by a cell).

DECIDED (v0.42.0): implement the heuristic rather than pin the boundary. The
codepoint count is the same proxy class m33 rejected — it is not the rendered
unit — and relocating a known misalignment to a different surface has never
been this project's answer. stdlib has no grapheme-cluster API (`unicodedata`
exposes no `Emoji` property), so the rule is a deterministic heuristic over the
classes real text contains, locked against hand-computed cell counts.

Accepted residuals (terminal-dependent, documented so an audit reads a decision
and not an oversight — the ambiguous=1 stance again):

* VS16 (`FE0F`) asks for emoji presentation, so a text-default glyph it follows
  (`©\ufe0f`, `1\ufe0f\u20e3`) may render 2 cells where this rule counts 1.
* A stray ZWJ between ordinary text and an emoji (`a\u200d😀`) has no ligature to
  collapse into; the run is treated as one glyph (2 cells) though a terminal may
  draw both (3). Defined sequences are the common case and win that trade.
* A terminal without emoji fonts draws the members of a joined run separately.
  Font availability is a machine setting, not a data bug.

Why a leaf module: the consumers are print seams (`config.py`'s events table,
`backlight.py`'s presets table), neither of which may pull PIL, and the rule is
neither config validation (`configvalues`) nor calendar text (`dates`).
`textwidth.py` imports only stdlib `unicodedata` — a legal dependency of
everything, the `dates.py` leaf precedent (v0.38.0). The screen side is
unaffected by any of this: it measures real pixels with `draw.textbbox`.
"""

import unicodedata

# Codepoints that occupy no cell of their own but are not combining marks by
# category: the joiners, word joiner, BOM, and zero-width space.
_ZERO_WIDTH = frozenset({0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF})

# U+200D ZERO WIDTH JOINER: the operator that binds the next cluster into this
# one (handled in `display_width`, not absorbed as an extender below).
_ZWJ = 0x200D

# Variation selectors (VS1..VS16) and emoji skin-tone modifiers ride the base
# glyph. They are listed explicitly rather than trusted to their `unicodedata`
# category, so the rule cannot shift with the Unicode table an interpreter
# happens to ship — v0.38's lesson: an accident is never a promise.
_VARIATION_SELECTORS = frozenset(range(0xFE00, 0xFE10))
_SKIN_TONES = frozenset(range(0x1F3FB, 0x1F400))

# Codepoints that extend the cluster they follow. ZWJ is deliberately absent:
# absorbing it here would fold `a\u200db` into ONE cluster, and no ligature
# exists for two letters — the v0.39 pin says that pair is 2 cells.
_EXTENDERS = (_ZERO_WIDTH | _VARIATION_SELECTORS | _SKIN_TONES) - {_ZWJ}

# Combining marks and enclosing marks: zero cells by Unicode category.
_MARKS = frozenset({"Mn", "Me"})


def _cells(ch: str) -> int:
    """Cells a lone codepoint occupies — the v0.39 per-codepoint rule."""
    if unicodedata.category(ch) in _MARKS or ord(ch) in _ZERO_WIDTH:
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _extends(ch: str) -> bool:
    """True when `ch` rides the preceding cluster's cell instead of adding one."""
    return unicodedata.category(ch) in _MARKS or ord(ch) in _EXTENDERS


def _run_width(run: list[int]) -> int:
    """Cells a U+200D-joined run of clusters occupies.

    A run holding an emoji (any 2-cell member) is ONE glyph: the terminal draws
    the joined sequence once, so it costs its widest member, never the sum of
    its parts. An all-narrow run has no ligature to collapse into — `a\u200db`
    renders as two letters (the v0.39 pin) — so it sums.
    """
    if len(run) > 1 and 2 in run:
        return max(run)
    return sum(run)


def display_width(text: str) -> int:
    """Terminal cells `text` occupies (module docstring states the rule)."""
    total = 0
    run: list[int] = []
    i, n = 0, len(text)
    while i < n:
        run.append(_cells(text[i]))
        i += 1
        while i < n and _extends(text[i]):
            i += 1  # a mark, selector or skin tone: same cell, no new cluster
        if i + 1 < n and ord(text[i]) == _ZWJ and not text[i + 1].isspace():
            i += 1  # the joiner binds the next cluster into this run
            continue
        total += _run_width(run)
        run = []
    return total + _run_width(run)


def pad(text: str, width: int) -> str:
    """Right-pad `text` with spaces to `width` DISPLAY cells (never truncate).

    `str.ljust` semantics measured in cells instead of characters: byte-
    identical output for ASCII (parity-locked), and an over-wide cell is left
    whole — the row shifts right, marked rather than amputated, exactly the
    screen side's overflow stance (v0.34/v0.36).
    """
    shortfall = width - display_width(text)
    return text + " " * shortfall if shortfall > 0 else text


def rule(text: str, char: str = "-") -> str:
    """A separator rule exactly as wide as `text` occupies on screen (m34).

    Table rules used to be magic numbers nothing derived them from —
    `"-" * 68` under a 65-cell header, `"-" * 80` under a 69-cell one. The
    rule is a consequence of the table geometry, so it is computed from the
    header it sits under, never copied as a second number that can drift.
    """
    return char * display_width(text)
