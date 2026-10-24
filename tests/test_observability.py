#!/usr/bin/env python3
"""v0.14.0 regression suite: Ops & Observability (REVIEW.md B6/B7 + §6.5).

Covers: `logging` to stderr + global `--verbose/-v` with stdout reserved for
machine-readable data; single templated `playerctl` call (§4.3); shipped
systemd units (`Restart=on-failure` pairs with exit 75); global `--dry-run`
(render + digest + chunk count, zero USB, zero state writes); the
`wait_for_keypress` stdin-EOF edge (/dev/null must NOT read as a keypress).
All device-free.
"""

import io
import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import widgets
from ak820 import actions, cli, renderers, transport

EVENTS_CFG = {
    "events": [
        {"id": "test-bday", "title": "Test Birthday", "short_title": "BDAY",
         "date": "12-25", "category": "birthday"}
    ]
}


class TestVerboseLogging(unittest.TestCase):
    """B6: diagnostics on stderr, stdout pure; --verbose flips INFO -> DEBUG."""

    def tearDown(self):
        logging.basicConfig(level=logging.WARNING, force=True)

    def test_parser_accepts_verbose_flags(self):
        self.assertTrue(cli.build_parser().parse_args(["-v", "probe"]).verbose)
        self.assertTrue(cli.build_parser().parse_args(["--verbose", "probe"]).verbose)
        self.assertFalse(cli.build_parser().parse_args(["probe"]).verbose)

    def test_configure_logging_levels_and_stream(self):
        cli._configure_logging(True)
        root = logging.getLogger()
        self.assertEqual(root.level, logging.DEBUG)
        self.assertIs(root.handlers[0].stream, sys.stderr)
        cli._configure_logging(False)
        self.assertEqual(root.level, logging.INFO)
        self.assertIs(root.handlers[0].stream, sys.stderr)

    def test_events_json_stdout_is_pure_json_even_verbose(self):
        """`events list --json` stdout must parse with stderr discarded."""
        with tempfile.TemporaryDirectory() as td:
            cfg = os.path.join(td, "events.json")
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump(EVENTS_CFG, f)
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, {"AK820_EVENTS_PATH": cfg}):
                with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
                    rc = cli.main(["--verbose", "events", "list", "--json"])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())  # raises if any log line leaked
            self.assertEqual(data[0]["title"], "Test Birthday")


class TestDryRun(unittest.TestCase):
    """B7: --dry-run renders + reports digest/chunks, opens no hidraw node."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.cfg = os.path.join(self.td.name, "events.json")
        with open(self.cfg, "w", encoding="utf-8") as f:
            json.dump(EVENTS_CFG, f)
        self.state = os.path.join(self.td.name, "state.json")
        self.env = {"AK820_EVENTS_PATH": self.cfg, "AK820_STATE_PATH": self.state}

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, self.env):
            with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
                rc = cli.main(argv)
        return rc, out.getvalue()

    def test_parser_accepts_dry_run(self):
        self.assertTrue(cli.build_parser().parse_args(["--dry-run", "probe"]).dry_run)
        self.assertFalse(cli.build_parser().parse_args(["probe"]).dry_run)

    def test_card_dry_run_no_device_no_state(self):
        # No hardware here: if dry-run failed to intercept, DeviceNotFound -> 3.
        rc, out = self._run(["--dry-run", "card", "--title", "AI", "--value", "READY"])
        self.assertEqual(rc, 0)
        self.assertIn("[dry-run]", out)
        self.assertIn("sha256=", out)
        self.assertIn("chunk(s)", out)
        self.assertFalse(os.path.exists(self.state), "dry-run must not touch dedup state")

    def test_radar_dry_run(self):
        rc, out = self._run(["--dry-run", "radar", "--view", "hero", "--date", "2026-12-20"])
        self.assertEqual(rc, 0)
        self.assertIn("radar:hero", out)

    def test_upload_dry_run_matches_real_digest(self):
        from PIL import Image

        png = os.path.join(self.td.name, "x.png")
        Image.new("RGB", (128, 128), (10, 20, 30)).save(png)
        rc, out = self._run(["--dry-run", "upload", png])
        self.assertEqual(rc, 0)
        frames, delays, label = renderers.render_upload(
            cli.build_parser().parse_args(["upload", png])
        )
        self.assertEqual(label, "x.png")
        digest = actions._payload_digest(frames, delays)
        self.assertIn(digest, out)

    def test_non_render_command_dry_run_is_noop(self):
        rc, out = self._run(["--dry-run", "sync-clock"])
        self.assertEqual(rc, 0)
        self.assertIn("nothing sent", out)

    def test_describe_payload_does_not_record_state(self):
        frames = [bytes(128 * 128 * 2)]
        with mock.patch.dict(os.environ, self.env):
            out = io.StringIO()
            with mock.patch.object(sys, "stdout", out):
                rc = actions.describe_payload(frames, None, "probe")
        self.assertEqual(rc, 0)
        self.assertIn("1 frame(s)", out.getvalue())
        self.assertFalse(os.path.exists(self.state))


class TestPlayerctlSingleCall(unittest.TestCase):
    """§4.3/B6: one templated playerctl call replaces four subprocesses."""

    def test_single_templated_call(self):
        calls = []

        def fake_check_output(cmd, **kwargs):
            calls.append(cmd)
            return "Playing\tBohemian Rhapsody\tQueen\tA Night at the Opera\n"

        # v0.25.0: the probe seam moved to ak820.telemetry (widgets is a shim);
        # the call still goes through the shim to prove re-export identity.
        with mock.patch("ak820.telemetry.shutil.which", return_value="/usr/bin/playerctl"), \
             mock.patch("ak820.telemetry.subprocess.check_output", side_effect=fake_check_output):
            media = widgets.get_media_status()

        self.assertEqual(len(calls), 1, f"expected 1 subprocess call, got {len(calls)}")
        self.assertEqual(calls[0][:2], ["playerctl", "-f"])
        self.assertIn("{{status}}", calls[0][2])
        self.assertIn("{{album}}", calls[0][2])
        self.assertEqual(media["status"], "Playing")
        self.assertEqual(media["title"], "Bohemian Rhapsody")
        self.assertEqual(media["artist"], "Queen")
        self.assertEqual(media["album"], "A Night at the Opera")

    def test_no_player_falls_through(self):
        def fake_check_output(cmd, **kwargs):
            return ""

        with mock.patch("ak820.telemetry.shutil.which", return_value="/usr/bin/playerctl"), \
             mock.patch("ak820.telemetry.subprocess.check_output", side_effect=fake_check_output):
            media = widgets.get_media_status()
        self.assertFalse(media["active"])


class TestWaitForKeypressStdinEof(unittest.TestCase):
    """v0.14.0: /dev/null stdin is EOF-readable — must NOT be a false keypress."""

    def test_devnull_stdin_is_not_a_keypress(self):
        with mock.patch.object(transport, "find_keyboard_hidraw", return_value=[]):
            with open(os.devnull) as devnull:
                with mock.patch.object(sys, "stdin", devnull):
                    self.assertFalse(transport.wait_for_keypress(timeout=1))

    def test_real_stdin_byte_is_a_keypress(self):
        r, w = os.pipe()
        os.write(w, b"\n")
        os.close(w)
        with os.fdopen(r) as f:
            with mock.patch.object(transport, "find_keyboard_hidraw", return_value=[]):
                with mock.patch.object(sys, "stdin", f):
                    self.assertTrue(transport.wait_for_keypress(timeout=1))


class TestSystemdUnits(unittest.TestCase):
    """B7: shipped units — oneshot+timer for radar, Restart=on-failure for HUD."""

    def test_units_exist_and_pair_with_exit_contract(self):
        svc = (ROOT / "systemd" / "ak820-hud.service").read_text()
        self.assertIn("Restart=on-failure", svc)  # exit 75 -> systemd re-spawns
        self.assertIn("ak820 hud --watch", svc)

        radar = (ROOT / "systemd" / "ak820-radar.service").read_text()
        self.assertIn("Type=oneshot", radar)
        self.assertIn("ak820 radar", radar)
        self.assertNotIn("Restart=", radar)  # exit 3 must not restart-loop

        timer = (ROOT / "systemd" / "ak820-radar.timer").read_text()
        self.assertIn("OnUnitActiveSec=", timer)
        self.assertIn("timers.target", timer)


if __name__ == "__main__":
    unittest.main()
