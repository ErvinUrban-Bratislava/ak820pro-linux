"""Entry-point rules for events-config values — what the CLI may CREATE (v0.26.0).

This module is the CLI half of one fix-the-config contract; the loader half lives
in `ak820.coercion` (moved out here in v0.34.0 under the §10 line budget). The
seam is which side of the trust boundary a rule runs on, and it decides where a
rule belongs:

* `ak820.coercion.coerce_config_values` runs at the `load_events_config` choke
  point on data the user ALREADY WROTE (m18's wrong types, m21's bool spellings,
  m23's idle int bounds). It is permissive about anything that works: rejecting
  a config the user already wrote turns every command into exit 6.
* THIS module guards what the CLI is about to CREATE, where refusing is cheap
  and silently accepting something inert is not. `validate_note` is the one
  rule both sides call (the `{years}` rule is a data rule), so `coercion`
  imports it, never the reverse — the dependency is one-way by design.

Rules:

* `validate_note` — a note may only interpolate `{years}`; any other placeholder
  (or unbalanced braces) is a ConfigError, never a KeyError at render time. m28
  (v0.32.0): it also strips surrounding whitespace and returns the normalized
  text, so a hand-edited blank note (`"   "`) becomes the documented
  empty-note fallback ("Milestone") instead of rendering an empty column.
* `validate_nonblank` (m27, v0.31.0) — `events add --short`/`--title`/`--note`
  and the `events remove` target must not be empty or whitespace-only (ConfigError
  6, the D4 precedent), and it returns the stripped text so normalization is
  single-home. The loader stays permissive for hand-edited data (REVIEW.md §28).
* A `note` is rendered for `milestone` entries ONLY (m29, v0.33.0):
  `compute_upcoming_events` never consults it for `birthday`/`anniversary`, so
  `--category birthday --note …` stored a note that could never appear on
  screen. `validate_note_category` rejects that combination at the CLI
  (ConfigError 6) against the RESULTING entry, and `--clear-note` removes a
  stored note. The category vocabulary has ONE home here —
  `EVENT_CATEGORIES` feeds `parser.py`'s `--category` choices and `events.py`'s
  renderer, `NOTE_INERT_CATEGORIES` names the categories that never consult a
  note — so a new category cannot be added without this rule accounting for it.
* `validate_max_len` + `MAX_SHORT_LEN` (m30, v0.34.0) bound the one label-shaped
  input the 128x128 screen cannot show in full; `parser.py`'s help text is built
  from the same constant so the documented limit and the enforced one are a
  single value. `--note` is deliberately NOT length-bounded — see
  `validate_max_len`'s reason: the renderer now marks truncation instead of
  hiding it, and any usable bound would still permit text that truncates.
* `validate_single_line` (m31b, v0.36.0) rejects text that cannot render on
  ONE line (`events add --short`/`--title`/`--note`, `card
  --title`/`--value`/`--sub`/`--footer`). v0.35.0 made every renderer CONTAIN a
  break by flattening it, but containment is degradation — the reader never
  learns the text lost its shape. The creation path refuses what the renderer
  can only degrade (the m18/m27/m29 stance); the loader stays permissive.
  m34 (v0.40.0) widened the vocabulary to every control character (a TAB is not
  a break, yet no width rule can align it); m37 (v0.43.0) widened it again to
  the bidi format characters (category Cf — a terminal reverses the render
  ORDER of the rest of the line, which no width rule can fix).
* `flatten_for_display` (m32, v0.37.0) is that rule's display half: characters
  a hand-edited config already holds keep their stored bytes and are flattened
  only on the way to the one-line `events list` table.
"""

import re
import string

from .errors import ConfigError

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

# m30 (v0.34.0): the one length bound on the events surface, and its ONE home —
# `parser.py` builds the `--short` help text from this constant, so the promise
# in the help and the rule at the CLI can never drift (the m23 bounds pattern).
# Measured, not guessed: at the board row's max font size (12, narrow) a label
# fits the typical row's 81 px budget up to ~11 chars ("GRANDMOTHER" = 79 px);
# 12 is the first length that already has to shrink, and the bound is set one
# past the last length that renders at full size. Longer labels are rejected
# rather than shrunk to illegibility or collided with the days pill.
MAX_SHORT_LEN = 12


def kind(value: object) -> str:
    """JSON-flavoured type name for error messages (null, not NoneType).

    Public (v0.29.0): shared with `events.load_events_config`'s D22/D25/D26
    section-shape checks so every config violation names the offending value
    in the spelling the user typed it with — one vocabulary, one home.
    """
    return "null" if value is None else type(value).__name__


def validate_note(note: str, where: str) -> str:
    """Only {years} may be interpolated; returns the note stripped (m28).

    Public seam (v0.27.0): the single home of the `{years}`-only rule, shared
    by the load-time coercion and `events add --note` validation so the two
    entry points cannot drift. `where` is the full location phrase ("--note").

    m28 (v0.32.0): returns the stripped text — the `validate_nonblank` pattern,
    so normalization is single-home and no caller can forget to strip. A
    hand-edited `"note": "   "` used to survive verbatim and render an EMPTY
    milestone column (`note_tpl or "Milestone"` is truthy for spaces); stripped
    to `""` it takes the documented fallback. Internal whitespace and the
    `{years}` placeholder are untouched — only the incidental edges go.
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
    source — `--short " "` stored the degenerate id `"-"` with a blank
    `short_title` label and the entry was unusable. Returning the stripped
    text keeps normalization single-home too: `--short " DAUGHTER "` yields
    the canonical id `daughter`, never the dash-padded `-daughter-` (identity
    must not be sensitive to incidental formatting — m24's class).

    Trust boundary (v0.27.0's precedent): the CLI enforces non-blank;
    `load_events_config` stays permissive for hand-edited configs — rejecting
    data the user already wrote would turn every command into exit 6, and the
    CLI can no longer create such entries.
    """
    stripped = value.strip()
    if not stripped:
        raise ConfigError(f"{where} must not be empty or whitespace-only.")
    return stripped


# m31b (v0.36.0): the CLI half of the single-line rule — a deliberate PIL-free
# TWIN of `screen._LINE_BREAK_RE`: this module sits under the PIL-free events
# domain (§10.2 — `events add` must never load PIL), so the vocabulary cannot
# be imported from its render-side home; the two definitions are locked equal
# by test instead: one rule, two homes, test-enforced (v0.36/v0.37/v0.40).
_LINE_BREAK_RE = re.compile("[\\n\\r\\v\\f\\x1c\\x1d\\x1e\\x85\\u2028\\u2029]+")
# Twin of `screen._WS_RUN_RE` (m32, v0.37.0): `flatten_for_display` needs the
# same collapse step its render-side twin does.
_WS_RUN_RE = re.compile(r"\s+")
# m34 (v0.40.0): ALL control characters (Cc = C0 + C1; U+2028/U+2029 join as
# Zl/Zp the break set already carried) — every control character has a
# terminal-defined, unpredictable advance (live-reproved: `A\tB` counted one
# cell, advanced to a position-dependent tab stop).
# m37 (v0.43.0): the class was always "terminal-defined, unpredictable
# rendering", never "Cc" — the 12 Unicode Bidi_Control codepoints join (Cf,
# NOT Cc; live-reproved: `Trip\u202eHome` stored exit 0 and the terminal
# rendered the rest of the row reversed — no width rule can fix ORDER). Cf
# wholesale is the WRONG vocabulary: it also holds the joiners v0.42 made
# meaningful (ZWJ/ZWNJ/WJ/BOM) and format chars that never reorder (soft
# hyphen) — so Bidi_Control's members are listed explicitly (v0.38: an
# accident is never a promise). Twin of `screen._CONTROL_RE`.
_CONTROL_RE = re.compile(
    "[\\x00-\\x1f\\x7f-\\x9f\\u061c\\u200e\\u200f\\u2028\\u2029\\u202a-\\u202e\\u2066-\\u2069]+"
)
# The bidi subset exists for message discrimination only — answering an ORDER
# defect with the tab-stop message would misdescribe it. Not a second
# vocabulary: locked a subset of the union in tests/test_minors_v043.py.
_BIDI_RE = re.compile("[\\u061c\\u200e\\u200f\\u202a-\\u202e\\u2066-\\u2069]+")


def validate_single_line(value: str, where: str) -> str:
    """Reject text that cannot render on one line (m31b; m34, m37 widened). ConfigError 6.

    Every field this guards renders on a single line — the 128x128 screen's
    one-line boxes and the `events list` table alike. Since v0.35.0 the
    renderer flattens a break to a space instead of letting it escape its
    box, but a silently re-shaped string is still a lie about what the user
    typed: the CLI names the problem and the fix at the entry point, exactly
    as m27 named blank and m29 named an inert note. Composes AFTER
    `validate_nonblank` (call it on the stripped text). The value is returned
    unchanged — unlike blank/placeholder text there is nothing to normalize.

    m34/m37: the vocabulary is every control character plus the bidi format
    characters — a TAB pasted from a spreadsheet misaligns the whole row, and
    `Trip\\u202eHome` (live-reproved) stores exit 0 while the terminal runs the
    Unicode bidi algorithm and renders the rest of the line reversed; no
    width rule can fix ORDER, so the creation seam refuses. Check order:
    break FIRST (v0.36: a string holding both names the more fundamental
    problem), then bidi (the union `_CONTROL_RE` matches it too, and the
    tab-stop message would misdescribe an ordering defect), then the rest of Cc.

    Trust boundary (m27/m28/m29, table-locked): the loader stays permissive —
    a hand-edited `note`/`short_title` containing `\\n`, `\\t` or `\\u202e` still
    loads and renders, contained by `screen.flatten_lines`, because rejecting
    data the user already wrote would brick whole configs. Only the creation
    path refuses; a note a re-add PRESERVES from such a config is not
    re-judged here either (it is hand-edited data arriving through the
    permissive door).
    """
    if _LINE_BREAK_RE.search(value):
        raise ConfigError(
            f"{where} contains a line break; this field renders on a single "
            f"line. Replace the break with a space."
        )
    if _BIDI_RE.search(value):
        raise ConfigError(
            f"{where} contains a bidi format character; a terminal runs the "
            f"Unicode bidi algorithm and renders the rest of the line "
            f"REVERSED, scrambling this row and every column after it. No "
            f"width rule can fix render order — remove the bidi character "
            f"(paste as plain text)."
        )
    if _CONTROL_RE.search(value):
        raise ConfigError(
            f"{where} contains a TAB or other control character; a tab "
            f"advances to a position-dependent tab stop, so no column can "
            f"stay aligned. This field renders on a single line — replace "
            f"the control character with a space."
        )
    return value


def flatten_for_display(value: str) -> str:
    """Collapse control/bidi characters (breaks, TAB, overrides) for display (m32, m34, m37).

    This is the DISPLAY half of the single-line rule, and it is deliberately not
    `validate_single_line`: the CLI still refuses such text in what it is about
    to CREATE, while a hand-edited config that already holds it keeps loading
    and keeps its stored bytes (the m27/m28/m29/m31b trust boundary). `events
    list` prints through this so a stored break cannot break the table's row
    structure, a stored TAB cannot misalign it, and a stored bidi override
    cannot reverse it (m37) — ragged rows are the print-seam equivalent of the
    render overflow m31 closed.

    The PIL-free twin of `screen.flatten_lines` (same `_CONTROL_RE` vocabulary
    as `validate_single_line`, same collapse-and-strip shape): `events list` must
    never import PIL, so the rule has two homes locked equal by the equivalence
    table in tests/test_minors_v037.py. Only the printed copy is flattened —
    `events list --json` keeps the raw stored value, because that output is data.
    """
    if not value or not _CONTROL_RE.search(value):
        return value
    return _WS_RUN_RE.sub(" ", _CONTROL_RE.sub(" ", value)).strip()


def validate_max_len(value: str, where: str, limit: int) -> str:
    """Bound a CLI string's length (m30, v0.34.0). ConfigError 6.

    Composes with `validate_nonblank` (call it on the stripped text, so the
    bound applies to what actually gets stored). The rule lives here, in one
    home, and `parser.py`'s help text is built from `MAX_SHORT_LEN` — the
    documented limit and the enforced one cannot drift (m23's pattern).

    Why only `--short` is bounded: `--short` is a *label* whose whole job is to
    be read on a 128x128 screen, and its help already promised "max ~10 chars"
    while nothing enforced anything — a documented limit with no rule behind it
    is m29's class. `--note` deliberately gets NO length bound: any bound large
    enough for usable prose still permits text that truncates, and truncation
    is now MARKED by `screen.fit_text`'s ellipsis instead of amputated
    silently — a hard bound would buy nothing the mark does not already give.
    """
    if len(value) > limit:
        raise ConfigError(
            f"{where} is {len(value)} chars; the limit is {limit}. A longer label "
            f"cannot be read on the 128x128 screen — shorten it (the full title "
            f"stays in --title)."
        )
    return value


def validate_note_category(category: str, note: str | None, origin: str) -> None:
    """Reject a note its category can never render (m29, v0.33.0). ConfigError 6.

    `compute_upcoming_events` consults `note` in the milestone branch ONLY —
    for `birthday`/`anniversary` the MILESTONE column is fixed text, so
    `--category birthday --note …` exited 0, stored, and listed `Turns 1`
    (live-proven, v0.32.0). A flag accepted, persisted and never rendered is a
    silent no-op; naming it at the entry point is the m18/m27/m29 stance.

    `origin` says where the note came from, because two paths reach the same
    inert state: an explicit `--note` on a birthday, and a note the m25
    prior-copy PRESERVES when `--category` switches a noted milestone to a
    birthday. The caller validates the RESULTING entry, covering both.

    Trust boundary unchanged (m27/m28): the loader stays permissive — rejecting
    data the user already wrote would brick whole configs. `--clear-note` is
    the escape hatch the message offers for a preserved note.
    """
    if note and category in NOTE_INERT_CATEGORIES:
        raise ConfigError(
            f"{origin} is never rendered for --category {category}: birthdays and "
            f"anniversaries show fixed text in the MILESTONE column. Omit --note "
            f"(or pass --clear-note to drop a stored note), or use "
            f"--category {CATEGORY_MILESTONE}."
        )
