"""v0.36.0 — m31b: the containment rule never reached hud.py; the CLI refuses line breaks.

Live repros (measured against the real renderers, before the fix):

    * `render_media_card` bounded title/artist/album by CHARACTER COUNT
      (`title[:16]`, `artist[:20]`, `album[:22]`) — the exact proxy m30
      rejected. 16 wide glyphs at size 12 measure 182 px; drawn from x=8 they
      were amputated mid-glyph by the screen edge, silent, exit 0.
    * The two-line wrap loop silently DROPPED line 1 for a title with no word
      boundary inside its first 14 chars (measured: line 1 painted nothing,
      line 2 held the truncated word).
    * `render_hud_card` drew `uptime` unbounded: "12345d 23h" painted to x=123,
      across the uptime card's right border at x=121.
    * `render_hud_card` drew `hostname` unbounded: a 40-char node name painted
      cyan to x=125, sliding under the clock that starts at x=98.

DECIDED (v0.36.0):
* Renderer — every dynamic TEXT string in both renderers goes through
  `screen.fit_text` (the single home of containment), with each budget derived
  from the element the text is actually drawn next to: the hostname's budget
  is the clock's measured start x, the uptime's is the card border, the
  media strings' is the outer border. Numeric telemetry strings (clock, CPU,
  RAM, temp) stay the documented bounded-by-construction exemption — the same
  class cards.py documents for the days pill and countdown numerics.
* The media wrap is now a measured greedy wrap that never drops content: a
  single over-long word gets its own ellipsized line, and a tail that cannot
  fit on either line is marked, not amputated.
* CLI — `configvalues.validate_single_line` (ConfigError 6) rejects a line
  break in `events add --short`/`--title`/`--note` and `card
  --title`/`--value`/`--sub`/`--footer`: v0.35.0 made renderers CONTAIN a
  break by flattening it, but containment is degradation — the creation path
  refuses what the renderer can only degrade (the m18/m27/m29 stance). The
  loader stays permissive (m27/m28/m29 trust boundary, table-locked below):
  hand-edited JSON with `\\n` still loads and renders, contained.
* The CLI's line-break pattern is a deliberate PIL-free twin of
  `screen._LINE_BREAK_RE` (configvalues must never import PIL); the two
  definitions are locked equal by the equivalence table below.

Zero hardware, zero network, and no test depends on today's date.
"""

import ast
import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw

from ak820 import cli, configvalues, events, hud, screen
from ak820.errors import ConfigError
from ak820.hud import HUD_HOST_GAP, HUD_UPTIME_W, MEDIA_TEXT_W, _wrap_media_title
from ak820.screen import ELLIPSIS, FONT_BOLD_PATHS, FONT_MONO_PATHS, _load_font, text_width

# ---------------------------------------------------------------------------
# The pre-v0.36.0 renderers, copied verbatim so byte-identical parity for the
# strings that always fitted is PROVEN against the old code, not asserted.
# ---------------------------------------------------------------------------


def _legacy_render_hud_card(stats: dict) -> Image.Image:
    img = Image.new("RGB", (128, 128), (10, 14, 22))
    draw = ImageDraw.Draw(img)
    font_mono_s = _load_font(FONT_MONO_PATHS, 9)
    font_mono_xs = _load_font(FONT_MONO_PATHS, 8)
    font_bold_s = _load_font(FONT_BOLD_PATHS, 10)
    font_bold_m = _load_font(FONT_BOLD_PATHS, 13)
    draw.rectangle([0, 0, 127, 17], fill=(16, 24, 38))
    draw.line([0, 17, 127, 17], fill=(0, 229, 255))
    draw.text((4, 3), stats["hostname"], fill=(0, 229, 255), font=font_bold_s)
    clock_str = stats.get("time", "")
    c_bbox = draw.textbbox((0, 0), clock_str, font=font_mono_s)
    draw.text(
        (128 - 4 - (c_bbox[2] - c_bbox[0]), 4), clock_str, fill=(240, 246, 252), font=font_mono_s
    )
    cpu = stats["cpu_pct"]
    draw.text((6, 22), "CPU", fill=(148, 163, 184), font=font_mono_xs)
    cpu_str = f"{cpu:.0f}%"
    val_box = draw.textbbox((0, 0), cpu_str, font=font_bold_s)
    draw.text((128 - 6 - (val_box[2] - val_box[0]), 21), cpu_str, fill=(56, 189, 248), font=font_bold_s)
    draw.rectangle([6, 35, 121, 40], fill=(22, 32, 48), outline=(30, 48, 72))
    fill_w = int((128 - 14) * (min(cpu, 100.0) / 100.0))
    if fill_w > 0:
        bar_color = (239, 68, 68) if cpu >= 85 else (245, 158, 11) if cpu >= 60 else (56, 189, 248)
        draw.rectangle([7, 36, 6 + fill_w, 39], fill=bar_color)
    ram = stats["ram_pct"]
    draw.text((6, 47), "RAM", fill=(148, 163, 184), font=font_mono_xs)
    ram_str = f"{stats['ram_used_gb']:.0f}/{stats['ram_total_gb']:.0f}G ({ram:.0f}%)"
    r_box = draw.textbbox((0, 0), ram_str, font=font_mono_xs)
    draw.text((128 - 6 - (r_box[2] - r_box[0]), 47), ram_str, fill=(192, 132, 252), font=font_mono_xs)
    draw.rectangle([6, 59, 121, 64], fill=(22, 32, 48), outline=(30, 48, 72))
    r_fill_w = int((128 - 14) * (min(ram, 100.0) / 100.0))
    if r_fill_w > 0:
        r_bar_color = (239, 68, 68) if ram >= 90 else (192, 132, 252)
        draw.rectangle([7, 60, 6 + r_fill_w, 63], fill=r_bar_color)
    draw.rectangle([6, 71, 60, 111], fill=(16, 24, 38), outline=(30, 48, 72))
    draw.text((10, 75), "TEMP", fill=(148, 163, 184), font=font_mono_xs)
    if stats["temp_c"] is not None:
        t_val = stats["temp_c"]
        t_color = (
            (239, 68, 68) if t_val >= 80 else (245, 158, 11) if t_val >= 65 else (52, 211, 153)
        )
        t_str = f"{t_val:.0f}°C"
        draw.text((10, 89), t_str, fill=t_color, font=font_bold_m)
    else:
        draw.text((10, 89), "N/A", fill=(148, 163, 184), font=font_bold_m)
    draw.rectangle([66, 71, 121, 111], fill=(16, 24, 38), outline=(30, 48, 72))
    draw.text((71, 75), "UPTIME", fill=(148, 163, 184), font=font_mono_xs)
    draw.text((71, 91), stats["uptime"], fill=(251, 191, 36), font=font_bold_s)
    draw.rectangle([0, 0, 127, 127], outline=(30, 48, 72))
    draw.rectangle([0, 114, 127, 127], fill=(14, 20, 32))
    draw.text((6, 117), "AJAZZ AK820 PRO", fill=(71, 85, 105), font=font_mono_xs)
    return img


def _legacy_render_media_card(media: dict) -> Image.Image:
    img = Image.new("RGB", (128, 128), (12, 10, 18))
    draw = ImageDraw.Draw(img)
    font_bold_m = _load_font(FONT_BOLD_PATHS, 12)
    font_bold_s = _load_font(FONT_BOLD_PATHS, 10)
    font_mono_xs = _load_font(FONT_MONO_PATHS, 8)
    font_reg_s = _load_font(screen.FONT_REGULAR_PATHS, 9)
    is_playing = media.get("status") == "Playing"
    is_paused = media.get("status") == "Paused"
    # m32 (v0.37.0) deliberately changed the header TAG LITERAL ("▶ PLAYING" ->
    # "PLAYING" — the glyphs rendered .notdef tofu on the production font). This
    # frozen copy exists to prove the v0.36 CONTAINMENT algorithm is byte-identical
    # for the DYNAMIC strings (title/artist/album); the tag is a literal, not a
    # dynamic string, so it tracks the current renderer and the parity claim is
    # unchanged. tests/test_minors_v037.py owns the tag's own lock.
    if is_playing:
        header_bg, header_fg, tag = (16, 185, 129), (6, 24, 16), "PLAYING"
    elif is_paused:
        header_bg, header_fg, tag = (245, 158, 11), (24, 16, 6), "PAUSED"
    else:
        header_bg, header_fg, tag = (30, 41, 59), (148, 163, 184), "MEDIA"
    draw.rectangle([0, 0, 127, 18], fill=header_bg)
    draw.text((6, 3), tag, fill=header_fg, font=font_bold_s)
    title = media.get("title", "No Track")
    if len(title) > 16:
        words = title.split()
        l1: list[str] = []
        l2: list[str] = []
        for w in words:
            if len(" ".join([*l1, w])) <= 14:
                l1.append(w)
            else:
                l2.append(w)
        line1 = " ".join(l1)[:16]
        line2 = " ".join(l2)[:16]
        draw.text((8, 28), line1, fill=(255, 255, 255), font=font_bold_m)
        draw.text((8, 46), line2, fill=(255, 255, 255), font=font_bold_m)
    else:
        draw.text((8, 36), title, fill=(255, 255, 255), font=font_bold_m)
    artist = media.get("artist", "")
    draw.text((8, 72), artist[:20], fill=(0, 229, 255), font=font_reg_s)
    album = media.get("album", "")
    if album:
        draw.text((8, 88), album[:22], fill=(148, 163, 184), font=font_mono_xs)
    else:
        draw.text((8, 88), "AK820 Pro Display", fill=(71, 85, 105), font=font_mono_xs)
    draw.rectangle([0, 114, 127, 127], fill=(18, 14, 28))
    draw.line([0, 114, 127, 114], fill=(45, 30, 60))
    state_label = "NOW PLAYING" if is_playing else "PAUSED" if is_paused else "IDLE"
    draw.text((8, 117), state_label, fill=(148, 163, 184), font=font_mono_xs)
    draw.rectangle([0, 0, 127, 127], outline=(45, 30, 60))
    return img


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

OVERFLOW_TITLES = [
    "WWWWWWWWWWWWWWWW",  # 16 chars — the old cap let 182 px through untouched
    "Supercalifragilisticexpialidocious",  # one word, no boundary at all
    "Bohemian Rhapsody Bohemian Rhapsody Bohemian",
    "The quick brown fox jumps over the lazy dog and keeps running far",
    "a\nb\nc\nd\ne\nf\ng\nh",  # newlines must not become vertical escapes
]

OVERFLOW_ARTISTS = ["A" * 60, "Artist — with — many — wide — separator — glyphs", "x\ny"]
OVERFLOW_ALBUMS = ["B" * 80, "An album name long enough to cross the border of the card"]

STATS_BASE = {
    "cpu_pct": 10.0,
    "ram_pct": 40.0,
    "ram_used_gb": 50.0,
    "ram_total_gb": 128.0,
    "temp_c": 45.0,
    "time": "14:32",
}

# Realistic metadata that fitted the old caps AND the measured box: these must
# render byte-identically to the legacy code (the parity contract of m30/m31).
MEDIA_PARITY = [
    {"status": "Playing", "title": "No Track", "artist": "Queen", "album": "A Night at the Opera"},
    {"status": "Playing", "title": "Bohemian Rhapsody", "artist": "Queen", "album": "A Night at the Opera"},
    {"status": "Paused", "title": "Short", "artist": "", "album": ""},
    {"status": "Stopped", "title": "", "artist": "x", "album": "y"},
    {"status": "Playing", "title": "W.A.G.E.", "artist": "Twenty chars exactly", "album": "Twentytwo chars exact!"},
    {"status": "Playing", "title": "Let It Be", "artist": "The Beatles", "album": ""},
]

STATS_PARITY = [
    {"hostname": "HALOGEN", "uptime": "12d 5h", **STATS_BASE},
    {"hostname": "ERVIN-PC", "uptime": "365d 23h", "time": "09:05", "cpu_pct": 100.0,
     "ram_pct": 99.0, "ram_used_gb": 127.0, "ram_total_gb": 128.0, "temp_c": 88.0},
    {"hostname": "X", "uptime": "0m", "time": "", "cpu_pct": 0.0, "ram_pct": 0.0,
     "ram_used_gb": 0.0, "ram_total_gb": 1.0, "temp_c": None},
]

BREAK_CHARS = ["\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"]


def _main(argv):
    """cli.main(argv) capturing stdout+stderr; SystemExit code becomes the rc."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cli.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


def _paint_extent(img, band, pred, lo=0, hi=128):
    xs = [x for y in band for x in range(lo, hi) if pred(img.getpixel((x, y)))]
    return (min(xs), max(xs)) if xs else None


def WHITE(p) -> bool:
    """Title text (255,255,255) on the dark media card."""
    return sum(p[:3]) > 400


def AMBER(p) -> bool:
    """Uptime text (251,191,36)."""
    return p[0] > 200 and p[1] > 150 and p[2] < 100


def CYAN(p) -> bool:
    """Hostname / artist text (0,229,255)."""
    return p[1] > 180 and p[2] > 180 and p[0] < 120


def CLOCK_WHITE(p) -> bool:
    """Clock text (240,246,252)."""
    return min(p[:3]) > 180


def GRAY(p) -> bool:
    """Album text (148,163,184) vs the dark fills."""
    return sum(p[:3]) > 300


class TestMediaWrapUnit(unittest.TestCase):
    """The measured greedy wrap: never drops content, marks the tail."""

    def setUp(self):
        self.draw = ImageDraw.Draw(Image.new("RGB", (128, 128)))
        self.font = _load_font(FONT_BOLD_PATHS, 12)

    def test_fits_is_one_line_verbatim(self):
        for title in ["No Track", "Bohemian", "W.A.G.E.", "", "x"]:
            with self.subTest(title=title):
                self.assertEqual(_wrap_media_title(self.draw, title, self.font, MEDIA_TEXT_W), [title])

    def test_every_line_fits_the_measured_box(self):
        for title in OVERFLOW_TITLES + OVERFLOW_TITLES[:2] * 2 + ["word " * 40]:
            with self.subTest(title=title[:20]):
                for line in _wrap_media_title(self.draw, title, self.font, MEDIA_TEXT_W):
                    self.assertLessEqual(text_width(self.draw, line, self.font), MEDIA_TEXT_W)

    def test_no_line_is_ever_empty(self):
        """The old loop left line 1 EMPTY for a single long word — never again."""
        for title in OVERFLOW_TITLES + ["word " * 40, "a" * 200]:
            with self.subTest(title=title[:20]):
                lines = _wrap_media_title(self.draw, title, self.font, MEDIA_TEXT_W)
                self.assertTrue(all(line for line in lines), lines)

    def test_at_most_two_lines(self):
        for title in OVERFLOW_TITLES + ["word " * 60]:
            with self.subTest(title=title[:20]):
                self.assertLessEqual(len(_wrap_media_title(self.draw, title, self.font, MEDIA_TEXT_W)), 2)

    def test_single_long_word_gets_its_own_marked_line(self):
        lines = _wrap_media_title(self.draw, "Supercalifragilisticexpialidocious", self.font, MEDIA_TEXT_W)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].endswith(ELLIPSIS))
        self.assertTrue(lines[0][:3], "the word's head must still be shown")

    def test_two_lines_preserve_every_word_when_they_all_fit(self):
        # "Bohemian Rhapsody" is 120 px on the production font — 1 px over the
        # 119 px box — so it wraps; either way EVERY word must survive.
        title = "Bohemian Rhapsody"
        lines = _wrap_media_title(self.draw, title, self.font, MEDIA_TEXT_W)
        self.assertEqual(" ".join(lines), title, "a word was dropped")
        for line in lines:
            self.assertLessEqual(text_width(self.draw, line, self.font), MEDIA_TEXT_W)

    def test_dropped_tail_is_marked_not_amputated(self):
        lines = _wrap_media_title(self.draw, "word " * 40, self.font, MEDIA_TEXT_W)
        self.assertTrue(lines[-1].endswith(ELLIPSIS), "the reader must see text existed")

    def test_newlines_never_survive_into_a_line(self):
        for title in OVERFLOW_TITLES:
            with self.subTest(title=title[:16]):
                for line in _wrap_media_title(self.draw, title, self.font, MEDIA_TEXT_W):
                    self.assertNotIn("\n", line)
                    self.assertNotIn("\r", line)


class TestMediaCardContainment(unittest.TestCase):
    """The m31b defect itself: the media card's char-count caps, in pixels."""

    def test_title_never_reaches_the_screen_border(self):
        for title in OVERFLOW_TITLES:
            with self.subTest(title=title[:20]):
                img = hud.render_media_card({"status": "Playing", "title": title, "artist": "", "album": ""})
                extent = _paint_extent(img, range(26, 62), WHITE)
                self.assertIsNotNone(extent, "nothing was drawn")
                self.assertLessEqual(extent[1], 126, "title painted into/past the outer border")
                self.assertGreaterEqual(extent[0], 8)

    def test_artist_and_album_never_reach_the_border(self):
        for artist in OVERFLOW_ARTISTS:
            with self.subTest(artist=artist[:16]):
                img = hud.render_media_card({"status": "Playing", "title": "ok", "artist": artist, "album": ""})
                extent = _paint_extent(img, range(72, 87), CYAN)
                self.assertIsNotNone(extent)
                self.assertLessEqual(extent[1], 126)
        for album in OVERFLOW_ALBUMS:
            with self.subTest(album=album[:16]):
                img = hud.render_media_card({"status": "Playing", "title": "ok", "artist": "", "album": album})
                extent = _paint_extent(img, range(88, 105), GRAY)
                self.assertIsNotNone(extent)
                self.assertLessEqual(extent[1], 126)

    def test_newline_title_renders_exactly_like_its_flattened_form(self):
        """Mechanism proof: the card draws the flattened single line."""
        for title in ["A\nB", "line1\nline2\nline3\nline4\nline5"]:
            with self.subTest(title=title):
                a = hud.render_media_card({"status": "Playing", "title": title, "artist": "a", "album": "b"})
                b = hud.render_media_card(
                    {"status": "Playing", "title": screen.flatten_lines(title), "artist": "a", "album": "b"}
                )
                self.assertEqual(a.tobytes(), b.tobytes())

    def test_legacy_parity_fitting_metadata_is_byte_identical(self):
        """Nothing that fitted the old caps AND the measured box moved."""
        for media in MEDIA_PARITY:
            with self.subTest(title=media["title"]):
                new = hud.render_media_card(media)
                self.assertEqual(new.tobytes(), _legacy_render_media_card(media).tobytes())


class TestHudCardContainment(unittest.TestCase):
    """The HUD half of m31b: hostname vs clock, uptime vs its card border."""

    def test_uptime_never_crosses_the_card_border(self):
        for uptime in ["12345d 23h", "999999d 23h", "3650d 23h", "a\nb"]:
            with self.subTest(uptime=uptime):
                img = hud.render_hud_card({**STATS_BASE, "hostname": "HALOGEN", "uptime": uptime})
                extent = _paint_extent(img, range(91, 106), AMBER)
                self.assertIsNotNone(extent, "uptime drew nothing")
                self.assertLessEqual(extent[1], 120, "uptime painted over the border at x=121")

    def test_hostname_never_slides_under_the_clock(self):
        for host in ["A" * 40, "a\nb", "VERY-LONG-HOSTNAME-OF-A-SERVER", "HALOGEN"]:
            with self.subTest(host=host[:20]):
                img = hud.render_hud_card({**STATS_BASE, "hostname": host, "uptime": "12d 5h"})
                host_extent = _paint_extent(img, range(3, 17), CYAN)
                clock_extent = _paint_extent(img, range(3, 17), CLOCK_WHITE)
                self.assertIsNotNone(host_extent, "hostname drew nothing")
                self.assertIsNotNone(clock_extent, "clock drew nothing")
                self.assertLess(host_extent[1], clock_extent[0], "hostname reached the clock")

    def test_hostname_and_uptime_stay_on_one_line(self):
        for field in ["hostname", "uptime"]:
            with self.subTest(field=field):
                stats = {**STATS_BASE, "hostname": "HALOGEN", "uptime": "12d 5h", field: "top\nbottom"}
                a = hud.render_hud_card(stats)
                flat = {**stats, field: screen.flatten_lines(stats[field])}
                self.assertEqual(a.tobytes(), hud.render_hud_card(flat).tobytes())

    def test_legacy_parity_normal_stats_is_byte_identical(self):
        for stats in STATS_PARITY:
            with self.subTest(host=stats["hostname"], up=stats["uptime"]):
                self.assertEqual(
                    hud.render_hud_card(stats).tobytes(), _legacy_render_hud_card(stats).tobytes()
                )

    def test_budgets_are_the_derived_values(self):
        self.assertEqual(MEDIA_TEXT_W, 119)  # border x=127 minus text x=8
        self.assertEqual(HUD_UPTIME_W, 50)  # card border x=121 minus text x=71
        self.assertEqual(HUD_HOST_GAP, 4)


class TestValidateSingleLine(unittest.TestCase):
    """The CLI guard's unit contract."""

    def test_every_line_break_spelling_is_rejected(self):
        for ch in BREAK_CHARS:
            with self.subTest(ch=repr(ch)):
                with self.assertRaises(ConfigError):
                    configvalues.validate_single_line(f"a{ch}b", "--note 'x'")

    def test_newline_free_text_passes_unchanged(self):
        # v0.40.0 supersession: "a\tb" used to pass — m34 widened the single-line
        # vocabulary to ALL control characters (a TAB cannot be aligned on a
        # line), so it now joins the rejected set; see tests/test_minors_v040.py.
        for value in ["Turns 5", "27 DAYS", "", "a b", "  "]:
            with self.subTest(value=value):
                out = configvalues.validate_single_line(value, "--x")
                self.assertIs(out, value)  # nothing to normalize — same object

    def test_message_names_the_flag_and_the_fix(self):
        with self.assertRaises(ConfigError) as cm:
            configvalues.validate_single_line("a\nb", "--value 'x'")
        msg = str(cm.exception)
        self.assertIn("--value", msg)
        self.assertIn("line break", msg)
        self.assertIn("space", msg)

    def test_regexes_are_equivalent_twins(self):
        """One rule, two homes (PIL-free CLI vs PIL render side): locked equal."""
        battery = [f"a{c}b" for c in BREAK_CHARS] + [
            "plain", "", "a b", "a\tb", "…", "a b", "\n", "\r\n", "no break here",
        ]
        for s in battery:
            with self.subTest(s=repr(s)):
                self.assertEqual(
                    bool(configvalues._LINE_BREAK_RE.search(s)),
                    bool(screen._LINE_BREAK_RE.search(s)),
                )


class TestCliRejectsLineBreaks(unittest.TestCase):
    """The creation path refuses what the renderer can only degrade."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = f"{self.tmp}/events.json"
        with open(self.cfg, "w", encoding="utf-8") as f:
            json.dump({"events": []}, f)
        self.before = Path(self.cfg).read_bytes()

    def _add(self, *extra):
        return _main(
            ["events", "add", "--config", self.cfg, "--title", "T", "--short", "S", "--date", "06-01", *extra]
        )

    def test_events_flags_with_breaks_exit_6_and_save_nothing(self):
        for flag in ["--note", "--short", "--title"]:
            with self.subTest(flag=flag):
                rc, _out, err = self._add(flag, "line1\nline2")
                self.assertEqual(rc, 6)
                self.assertIn(flag, err)
                self.assertIn("line break", err)
                self.assertEqual(Path(self.cfg).read_bytes(), self.before, "config was written")
                self.assertNotIn("Traceback", err)

    def test_card_flags_with_breaks_exit_6_on_dry_run(self):
        base = ["--dry-run", "card", "--title", "REMINDER", "--value", "27 DAYS", "--sub", "s", "--footer", "f"]
        for flag, value in [("--title", "a\nb"), ("--value", "a\nb"), ("--sub", "a\nb"), ("--footer", "a\nb")]:
            argv = list(base)
            argv[argv.index(flag) + 1] = value
            with self.subTest(flag=flag):
                rc, _out, err = _main(argv)
                self.assertEqual(rc, 6)
                self.assertIn(flag, err)
                self.assertIn("line break", err)

    def test_valid_forms_still_pass(self):
        rc, _out, _err = self._add("--note", "Turns {years} today")
        self.assertEqual(rc, 0)
        rc, _out, _err = _main(["--dry-run", "card", "--title", "AI", "--value", "READY", "--sub", "s", "--footer", "f"])
        self.assertEqual(rc, 0)

    def test_unicode_separators_rejected_too(self):
        rc, _out, err = self._add("--note", "a\u2028b")
        self.assertEqual(rc, 6)
        self.assertIn("line break", err)


class TestTrustBoundary(unittest.TestCase):
    """CLI 6 / loader 0 — rejecting data the user already wrote bricks configs."""

    def _hand_edited(self, note: str) -> str:
        path = f"{tempfile.mkdtemp()}/events.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"events": [{"id": "x", "title": "T", "short_title": "X", "date": "06-01",
                             "category": "milestone", "note": note}]},
                f,
            )
        return path

    def test_hand_edited_note_with_break_still_loads_and_renders(self):
        import datetime

        path = self._hand_edited("First day\nof grade 5")
        cfg = events.load_events_config(path)  # loader: permissive (0)
        upcoming = events.compute_upcoming_events(cfg, today=datetime.date(2026, 5, 1))
        self.assertIn("\n", upcoming[0]["milestone_str"])  # data untouched
        from ak820 import cards

        img = cards.render_board_card(upcoming, today=datetime.date(2026, 5, 1))
        self.assertEqual(img.size, (128, 128))  # renderer: contained, never crashes

    def test_readd_preserving_a_hand_edited_break_note_is_not_re_judged(self):
        """A preserved note is hand-edited data at the permissive door — the
        single-line rule guards what the CLI CREATES, not what it inherits."""
        path = self._hand_edited("keep\nme")
        rc, _out, _err = _main(
            ["events", "add", "--config", path, "--title", "T", "--short", "x", "--date", "06-01"]
        )
        self.assertEqual(rc, 0)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["events"][0]["note"], "keep\nme")

    def test_asymmetry_table(self):
        """Per field: the CLI refuses (6), the loader/coercion side accepts (0)."""
        for field, value in [("note", "a\nb"), ("short_title", "a\nb"), ("title", "a\nb")]:
            with self.subTest(field=field):
                entry = {"id": "x", "title": "T", "short_title": "X", "date": "06-01", field: value}
                if field == "note":
                    entry["category"] = "milestone"
                cfg = {"events": [entry]}
                from ak820 import coercion

                self.assertIs(coercion.coerce_config_values(cfg, "mem"), cfg)  # loader: 0
                with self.assertRaises(ConfigError):  # CLI: 6
                    configvalues.validate_single_line(value, f"--{field}")


class TestHudSourceInvariants(unittest.TestCase):
    """Anti-recurrence: hud.py cannot draw dynamic text unfitted again."""

    # Numeric telemetry strings exempt from fit_text: bounded by construction
    # (telemetry formats "%H:%M" and .0f of bounded physical quantities) — the
    # same documented class as cards.py's days pill and countdown numerics.
    EXEMPT_NAMES = {"clock_str", "cpu_str", "ram_str", "t_str", "state_label"}

    def _allowed_names(self, fn):
        fitted, wrapped, literals = set(), set(), set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                fname = getattr(node.value.func, "id", None)
                if fname == "fit_text":
                    for t in node.targets:
                        if isinstance(t, ast.Tuple):
                            fitted.update(e.id for e in t.elts if isinstance(e, ast.Name))
                elif fname == "_wrap_media_title":
                    wrapped.update(t.id for t in node.targets if isinstance(t, ast.Name))
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                literals.update(t.id for t in node.targets if isinstance(t, ast.Name))
        return fitted, wrapped, literals

    def _unfitted_draws(self, source, func_name):
        tree = ast.parse(source)
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func_name)
        fitted, wrapped, literals = self._allowed_names(fn)
        offenders = []
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "text"):
                continue
            arg = next((kw.value for kw in node.keywords if kw.arg == "text"), node.args[1] if len(node.args) > 1 else None)
            if isinstance(arg, ast.Constant):
                continue
            if isinstance(arg, ast.Subscript) and isinstance(arg.value, ast.Name) and arg.value.id in wrapped:
                continue  # drawn from the measured wrap helper's output
            name = arg.id if isinstance(arg, ast.Name) else None
            if name in fitted or name in self.EXEMPT_NAMES or name in literals:
                continue
            offenders.append((func_name, node.lineno, ast.dump(arg)[:40]))
        return offenders

    def test_every_dynamic_draw_in_hud_is_fitted(self):
        src = (ROOT / "ak820" / "hud.py").read_text(encoding="utf-8")
        for func in ("render_hud_card", "render_media_card"):
            with self.subTest(func=func):
                self.assertEqual(self._unfitted_draws(src, func), [])

    def test_char_count_caps_are_gone(self):
        src = (ROOT / "ak820" / "hud.py").read_text(encoding="utf-8")
        for cap in ("[:16]", "[:20]", "[:22]"):
            self.assertNotIn(cap, src, f"the char-count proxy {cap} is back")

    def test_hud_uses_the_flatten_home_and_defines_no_regex(self):
        src = (ROOT / "ak820" / "hud.py").read_text(encoding="utf-8")
        self.assertIn("flatten_lines", src)  # imports the single home
        self.assertNotIn("_LINE_BREAK_RE", src)
        self.assertNotIn("splitlines", src)  # never re-implements newline handling

    def test_budgets_are_named_constants_derived_from_borders(self):
        src = (ROOT / "ak820" / "hud.py").read_text(encoding="utf-8")
        self.assertIn("MEDIA_TEXT_W = SCREEN_W - 1 - 8", src)
        self.assertIn("HUD_UPTIME_W = SCREEN_W - 7 - 71", src)

    def test_module_budgets(self):
        for name in ("hud.py", "configvalues.py", "renderers.py", "config.py"):
            with self.subTest(name=name):
                lines = (ROOT / "ak820" / name).read_text(encoding="utf-8").splitlines()
                self.assertLess(len(lines), 300, f"{name} blew the §10 budget")

    def test_configvalues_stays_pil_free(self):
        src = (ROOT / "ak820" / "configvalues.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"^\s*(from PIL import|import PIL)", src, re.M))

    def test_validation_seams_are_wired(self):
        rend = (ROOT / "ak820" / "renderers.py").read_text(encoding="utf-8")
        self.assertEqual(rend.count("validate_single_line("), 4)  # card's four flags
        conf = (ROOT / "ak820" / "config.py").read_text(encoding="utf-8")
        self.assertEqual(conf.count("validate_single_line("), 3)  # short/title/note


if __name__ == "__main__":
    unittest.main()
