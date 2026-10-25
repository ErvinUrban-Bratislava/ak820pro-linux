"""Terminal display width at the print seam (m33, v0.39.0).

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

Why a leaf module: the consumers are print seams (`config.py`'s events table,
`backlight.py`'s presets table), neither of which may pull PIL, and the rule
is neither config validation (`configvalues`) nor calendar text (`dates`).
`textwidth.py` imports only stdlib `unicodedata` — a legal dependency of
everything, the `dates.py` leaf precedent (v0.38.0).
"""

import unicodedata

# Codepoints that occupy no cell of their own but are not combining marks by
# category: the joiners, word joiner, BOM, and zero-width space.
_ZERO_WIDTH = frozenset({0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF})


def display_width(text: str) -> int:
    """Terminal cells `text` occupies (module docstring states the rule)."""
    width = 0
    for ch in text:
        if unicodedata.category(ch) in ("Mn", "Me") or ord(ch) in _ZERO_WIDTH:
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def pad(text: str, width: int) -> str:
    """Right-pad `text` with spaces to `width` DISPLAY cells (never truncate).

    `str.ljust` semantics measured in cells instead of characters: byte-
    identical output for ASCII (parity-locked), and an over-wide cell is left
    whole — the row shifts right, marked rather than amputated, exactly the
    screen side's overflow stance (v0.34/v0.36).
    """
    shortfall = width - display_width(text)
    return text + " " * shortfall if shortfall > 0 else text
