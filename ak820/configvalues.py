"""Entry-point rules for events-config values — what the CLI may CREATE (v0.26.0).

This module is the CLI half of one fix-the-config contract; the loader half lives
in `ak820.coercion` (moved out here in v0.34.0 under the §10 line budget). The
seam is which side of the trust boundary a rule runs on, and it decides where a
rule belongs:

* `ak820.coercion.coerce_config_values` runs at the `load_events_config` choke
  point on data the user ALREADY WROTE (m18's wrong-typed values, m21's bool
  spellings, m23's idle int bounds). It is permissive about anything that works:
  rejecting a config the user already wrote turns every command into exit 6.
* THIS module guards what the CLI is about to CREATE, where refusing is cheap
  and silently accepting something inert is not (the m18/m27/m28/m29/m30 stance).
  `validate_note` is the one rule both sides call — the `{years}` rule is a data
  rule, not a CLI rule — so it stayed here and `coercion` imports it, never the
  reverse (the dependency is one-way by design; a cycle would mean the seam was
  drawn in the wrong place).

Rules:

* `validate_note` — a note may only interpolate `{years}`; any other placeholder
  (or unbalanced braces) is a ConfigError, never a KeyError at render time. m28
  (v0.32.0): it also strips incidental surrounding whitespace and returns the
  normalized text, so a hand-edited blank note (`"   "`) becomes the documented
  empty-note fallback ("Milestone") instead of rendering an empty column.
* `validate_nonblank` (m27, v0.31.0) — `events add --short`/`--title`/`--note`
  and the `events remove` target must not be empty or whitespace-only (ConfigError
  6, the D4 precedent), and it returns the stripped text so normalization is
  single-home. The loader stays permissive for hand-edited data by decision (see
  its trust-boundary note and REVIEW.md §28).
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
* `validate_max_len` + `MAX_SHORT_LEN` (m30, v0.34.0) bound the one label-shaped
  input the 128x128 screen cannot show in full; `parser.py`'s help text is built
  from the same constant so the documented limit and the enforced one are a
  single value. `--note` is deliberately NOT length-bounded — see
  `validate_max_len`'s reason: the renderer now marks truncation instead of
  hiding it, and any usable bound would still permit text that truncates.
* `validate_single_line` (m31b, v0.36.0) rejects a line break in text that
  renders on ONE line (`events add --short`/`--title`/`--note`, `card
  --title`/`--value`/`--sub`/`--footer`). v0.35.0 made every renderer CONTAIN a
  break by flattening it, but containment is degradation — the reader never
  learns the text lost its shape. The creation path refuses what the renderer
  can only degrade (the m18/m27/m29 stance); the loader stays permissive.
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


# m31b (v0.36.0): the CLI half of the single-line rule. The pattern is a
# deliberate PIL-free TWIN of `screen._LINE_BREAK_RE` (v0.35.0): this module
# sits under the PIL-free events domain (§10.2 — `events add` must never load
# PIL), so the vocabulary cannot be imported from its render-side home. The
# two definitions are locked equal by an equivalence table in
# tests/test_minors_v036.py instead: one rule, two homes, test-enforced.
_LINE_BREAK_RE = re.compile("[\\n\\r\\v\\f\\x1c\\x1d\\x1e\\x85\\u2028\\u2029]+")


def validate_single_line(value: str, where: str) -> str:
    """Reject text that contains a line break (m31b, v0.36.0). ConfigError 6.

    Every field this guards renders on a single line — the 128x128 screen's
    one-line boxes and the `events list` table alike. Since v0.35.0 the
    renderer flattens a break to a space instead of letting it escape its
    box, but a silently re-shaped string is still a lie about what the user
    typed: the CLI names the problem and the fix at the entry point, exactly
    as m27 named blank and m29 named an inert note. Composes AFTER
    `validate_nonblank` (call it on the stripped text). The value is returned
    unchanged — unlike blank/placeholder text there is nothing to normalize.

    Trust boundary (m27/m28/m29, table-locked): the loader stays permissive —
    a hand-edited `note`/`short_title` containing `\\n` still loads and
    renders, contained by `screen.flatten_lines`, because rejecting data the
    user already wrote would brick whole configs. Only the creation path
    refuses; a note a re-add PRESERVES from such a config is not re-judged
    here either (it is hand-edited data arriving through the permissive door).
    """
    if _LINE_BREAK_RE.search(value):
        raise ConfigError(
            f"{where} contains a line break; this field renders on a single "
            f"line. Replace the break with a space."
        )
    return value


def validate_max_len(value: str, where: str, limit: int) -> str:
    """Bound a CLI string's length (m30, v0.34.0). ConfigError 6.

    Composes with `validate_nonblank` (call it on the stripped text, so the
    bound applies to what actually gets stored). The rule lives here, in one
    home, and `parser.py`'s help text is built from `MAX_SHORT_LEN` — the
    documented limit and the enforced one cannot drift (m23's bounds pattern,
    applied to length instead of to numbers).

    Why only `--short` is bounded: `--short` is a *label* whose whole job is to
    be read on a 128x128 screen, and its help already promised "max ~10 chars"
    while nothing enforced anything (20 chars accepted) — a documented limit
    with no rule behind it is m29's class. `--note` deliberately gets NO length
    bound: the board row shows roughly 15 characters of a note at its fixed
    size 9, so any bound large enough to be usable prose still permits text that
    truncates, and a bound small enough to guarantee full display (~15 chars)
    would reject legitimate notes while the loader happily renders them from
    JSON. Truncation there is now marked with an ellipsis by `screen.fit_text`
    instead of amputated silently, which is the honest outcome; a hard bound
    would buy nothing that the mark does not already provide.
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
