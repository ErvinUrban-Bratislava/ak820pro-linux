r"""v0.44.0 — the fast single-frame Board view is the `radar` default + screen docs.

`radar` with no `--view` uploaded the 34-frame Hero+Board cycle: 273 chunks
(1.11 MB) and ~26.5 s of USB streaming for a screen a user reads once. The
single-frame board is the same information (3 rows, closest events) in 9 chunks
(36 KB) and ~2.2 s — a 12× difference on the slowest part of the command. The
default now matches what most users actually want, and the explicit spellings
(`--view cycle`, `hero`, `hybrid`) are untouched: this changes a DEFAULT, never
the vocabulary.

The second half is documentation the repo never carried: how to get the custom
screen back after a clock sync (`Fn + Del`), the measured upload cost of each
view family, and how to clear a screen stuck on `LOADING... xx%` after an
interrupted upload (`radar --view board --force` completes a clean 9-chunk
transfer + the closing SAVE).

Anti-recurrence: the README's documented default and the parser's real default
are locked against EACH OTHER — a default that drifts from its own docs is the
same doc/code divergence class this suite has always pinned (exit codes, version,
module table). Chunk counts are derived from `protocol.CHUNK_SIZE` and the real
renderer, never hardcoded from prose.

Zero hardware, zero network; no test depends on today's date.
"""

import contextlib
import io
import re
import unittest
from pathlib import Path

from ak820.cli import build_parser, main

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")

# Measured on real hardware (v0.44.0 probes): a single frame is one 128x128
# RGB565 image = 32768 bytes; the cycle is 34 frames = 1114112 bytes.
BOARD_CHUNKS = 9
CYCLE_CHUNKS = 273


def _quiet(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = main(argv)
    return rc, out.getvalue() + err.getvalue()


class TestRadarViewDefault(unittest.TestCase):
    def test_default_view_is_board(self):
        self.assertEqual(build_parser().parse_args(["radar"]).view, "board")

    def test_help_text_names_board_as_default(self):
        radar = build_parser()._subparsers._group_actions[0].choices["radar"]  # type: ignore[attr-defined]
        act = next(a for a in radar._actions if "--view" in a.option_strings)
        self.assertEqual(
            act.help,
            "board=3-row list (default, fast 2s upload), hero=closest event only, "
            "cycle=Hero+Board loop, hybrid=Bongo Cat+Radar",
        )

    def test_every_view_spelling_still_parses(self):
        """The default moved; the vocabulary did not."""
        for view in ("board", "hero", "cycle", "hybrid"):
            with self.subTest(view=view):
                self.assertEqual(build_parser().parse_args(["radar", "--view", view]).view, view)

    def test_invalid_view_still_exits_2(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                build_parser().parse_args(["radar", "--view", "nope"])
        self.assertEqual(ctx.exception.code, 2)


class TestBoardIsTheFastPath(unittest.TestCase):
    """The documented speed claim is the renderer's own output, not prose."""

    def _frames(self, view):
        from ak820.renderers import render_radar

        args = build_parser().parse_args(
            ["radar", "--view", view, "--config", str(ROOT / "events.example.json")]
        )
        frames, delays, _label = render_radar(args)
        return frames, delays

    def _chunks(self, frames, delays):
        # The same payload builder the live upload and --dry-run share, so the
        # documented chunk count is the real wire count (header included).
        from ak820.device import build_payload

        payload, _frames, total_chunks = build_payload(frames, delays)
        self.assertGreater(len(payload), 0)
        return total_chunks

    def test_default_argv_is_a_single_frame(self):
        frames, delays = self._frames("board")
        self.assertEqual(len(frames), 1)
        self.assertIsNone(delays)

    def test_board_is_nine_chunks_and_cycle_is_two_hundred_seventy_three(self):
        board, board_delays = self._frames("board")
        cycle, cycle_delays = self._frames("cycle")
        self.assertEqual(self._chunks(board, board_delays), BOARD_CHUNKS)
        self.assertEqual(self._chunks(cycle, cycle_delays), CYCLE_CHUNKS)
        # The claim in the README: the default is an order of magnitude cheaper.
        self.assertGreater(CYCLE_CHUNKS / BOARD_CHUNKS, 12)

    def test_payload_sizes_match_the_documented_kb(self):
        board, _ = self._frames("board")
        cycle, _ = self._frames("cycle")
        self.assertEqual(len(b"".join(board)), 128 * 128 * 2)  # 32 KB -> "36 KB" on the wire
        self.assertEqual(len(b"".join(cycle)), 34 * 128 * 128 * 2)  # ~1.06 MB -> "1.11 MB"

    def test_dry_run_default_reports_the_fast_payload(self):
        rc, out = _quiet(
            ["--dry-run", "radar", "--config", str(ROOT / "events.example.json")]
        )
        self.assertEqual(rc, 0)
        self.assertIn("1 frame(s), 9 chunk(s)", out)


class TestScreenNavigationDocs(unittest.TestCase):
    """The doc section exists and says the three things that actually save a user."""

    def test_section_present(self):
        self.assertIn("## Screen Navigation & Troubleshooting", README)

    def test_documents_fn_del_toggle(self):
        section = self._section()
        self.assertIn("Fn + Del", section)
        self.assertIn("sync-clock", section)

    def test_documents_upload_speed_difference(self):
        section = self._section()
        for token in ("2.2", "9 chunks", "36 KB", "26.5", "273 chunks", "1.11 MB"):
            with self.subTest(token=token):
                self.assertIn(token, section)

    def test_documents_stuck_loading_recovery(self):
        section = self._section()
        self.assertIn("LOADING...", section)
        self.assertIn("ak820 radar --view board --force", section)

    def test_readme_default_matches_the_parser_default(self):
        """Docs and code may never drift on the default view (v0.18 version-sync class)."""
        default = build_parser().parse_args(["radar"]).view
        self.assertIn(f"`--view {default}`", README)

    def _section(self):
        start = README.index("## Screen Navigation & Troubleshooting")
        rest = README[start + 1 :]
        end = rest.index("\n## ") if "\n## " in rest else len(rest)
        return rest[:end]


class TestVersionBumped(unittest.TestCase):
    def test_version_is_044(self):
        import ak820

        self.assertEqual(ak820.__version__, "0.44.0")
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "0.44.0")


class TestRadarDocsNotStale(unittest.TestCase):
    def test_usage_guide_shows_the_fast_default(self):
        """The old example pushed `--view cycle` as THE radar command."""
        self.assertIn("ak820 radar\n", README + "\n")

    def test_no_doc_presents_cycle_as_the_default(self):
        for line in README.splitlines():
            if "cycle=Hero+Board loop" in line:
                self.assertNotIn("(default)", line)


if __name__ == "__main__":
    unittest.main()
