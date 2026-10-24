#!/usr/bin/env python3
"""v0.25.0 regression suite: the milestone_radar/widgets monolith split.

Locks the architecture refactor against the failure modes that matter:
1. Re-export identity — every historical shim name is the SAME object as the
   new package module's (no copy drift; user scripts and systemd timers keep
   working through `milestone_radar.*` / `widgets.*` unchanged).
2. Package self-containment — no `ak820/` module imports the top-level shims
   anymore; the dependency arrow points one way (shims -> package).
3. Domain/rendering separation — events/telemetry/palette/errors are PIL-free;
   cards never imports events (renderers consume plain row dicts); hud keeps
   its telemetry import function-level.
4. ConfigError re-parenting — AK820Error subclass, exit 6, one class object
   across every surface, and `cli.main()` still maps it to 6.
5. Module budget — every `ak820/` module AND both shims stay strictly < 300.
6. No import cycle: a fresh interpreter imports every new module + shim.
All offline, zero hardware.
"""

import contextlib
import io
import re
import subprocess
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ak820
import ak820.cards
import ak820.events
import ak820.hud
import ak820.palette
import ak820.telemetry
import milestone_radar as mr
import widgets
from ak820 import cards, cli, errors, events, hud, palette, telemetry
from ak820.errors import AK820Error, ConfigError

FIXTURE_CFG = {
    "events": [
        {"id": "bday", "title": "Birthday", "short_title": "BDAY", "date": "1990-10-26", "category": "birthday"},
        {"id": "anni", "title": "Anniversary", "short_title": "ANNI", "date": "2020-11-06", "category": "anniversary"},
    ]
}
FIXTURE_TODAY = date(2026, 10, 24)


class TestShimIdentity(unittest.TestCase):
    """Shims must re-export the package objects — same objects, never copies."""

    MR_MAP = {
        "get_default_events_path": events,
        "load_events_config": events,
        "save_events_config": events,
        "parse_event_date": events,
        "compute_upcoming_events": events,
        "_safe_date": events,
        "_threshold": events,
        "_ordinal": events,
        "render_hero_card": cards,
        "render_board_card": cards,
        "render_custom_card": cards,
        "_fit_text_font": cards,
        "_draw_category_icon": cards,
        "THEMES": palette,
        "ConfigError": errors,
    }

    WIDGETS_MAP = {
        "get_system_stats": telemetry,
        "get_media_status": telemetry,
        "render_hud_card": hud,
        "render_media_card": hud,
    }

    def test_milestone_radar_names_are_the_package_objects(self):
        for name, mod in self.MR_MAP.items():
            with self.subTest(name):
                self.assertIs(getattr(mr, name), getattr(mod, name), f"milestone_radar.{name} drifted")

    def test_widgets_names_are_the_package_objects(self):
        for name, mod in self.WIDGETS_MAP.items():
            with self.subTest(name):
                self.assertIs(getattr(widgets, name), getattr(mod, name), f"widgets.{name} drifted")

    def test_package_surface_exports_config_error(self):
        self.assertIs(ak820.ConfigError, errors.ConfigError)

    def test_screen_render_common_still_shared(self):
        from ak820 import screen

        self.assertIs(mr._load_font, screen._load_font)
        self.assertIs(widgets._load_font, screen._load_font)
        self.assertIs(mr.FONT_BOLD_PATHS, screen.FONT_BOLD_PATHS)


class TestPackageSelfContainment(unittest.TestCase):
    """The ak820 package must never reach back into the top-level shims."""

    SHIM_IMPORT = re.compile(r"^\s*(from|import)\s+(milestone_radar|widgets)\b", re.M)

    def test_no_package_module_imports_the_shims(self):
        for py in sorted((ROOT / "ak820").glob("*.py")):
            m = self.SHIM_IMPORT.search(py.read_text(encoding="utf-8"))
            self.assertIsNone(m, f"{py.name} imports a top-level shim: {m and m.group(0)!r}")


class TestDomainRenderSeparation(unittest.TestCase):
    """PIL-free domain/IO modules vs the pixel modules; one-way data seam."""

    PIL_FREE = ("events.py", "telemetry.py", "palette.py", "errors.py", "state.py")
    PIXEL = ("cards.py", "hud.py")
    PIL_IMPORT = re.compile(r"^\s*(from PIL import|import PIL)", re.M)

    def test_domain_modules_are_pil_free(self):
        for name in self.PIL_FREE:
            text = (ROOT / "ak820" / name).read_text(encoding="utf-8")
            self.assertIsNone(self.PIL_IMPORT.search(text),
                              f"{name} must not import PIL (domain/IO module)")

    def test_pixel_modules_import_pil(self):
        for name in self.PIXEL:
            text = (ROOT / "ak820" / name).read_text(encoding="utf-8")
            self.assertIn("from PIL import", text, f"{name} is a render module")

    def test_cards_never_imports_events(self):
        # Rendering consumes plain row dicts; config reads stay out of the pixel path.
        text = (ROOT / "ak820" / "cards.py").read_text(encoding="utf-8")
        self.assertNotIn("from .events", text)
        self.assertNotIn("import events", text)

    def test_hud_keeps_telemetry_function_level(self):
        # The renderers take their dicts as parameters (offline-testable seam),
        # so the telemetry import must stay indented (inside the None branch).
        text = (ROOT / "ak820" / "hud.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"^from \. import telemetry", text, re.M),
                          "hud must not import telemetry at module level")
        self.assertIn("    from . import telemetry", text)


class TestConfigErrorTaxonomy(unittest.TestCase):
    """ConfigError joined AK820Error in errors.py — exit 6 contract intact."""

    def test_reparented_under_ak820_error(self):
        self.assertTrue(issubclass(ConfigError, AK820Error))
        self.assertEqual(ConfigError.exit_code, 6)

    def test_single_class_object_everywhere(self):
        self.assertIs(mr.ConfigError, ConfigError)
        self.assertIs(ak820.ConfigError, ConfigError)

    def test_main_maps_config_error_to_exit_6(self):
        with mock.patch.object(cli, "_dispatch", side_effect=ConfigError("boom")):
            with self.assertRaises(SystemExit) as ctx:
                with contextlib.redirect_stderr(io.StringIO()):
                    cli.main(["probe"])
        self.assertEqual(ctx.exception.code, 6)


class TestModuleBudgetV025(unittest.TestCase):
    """§10 DoD extended to the split modules AND the two top-level shims."""

    def test_new_package_modules_under_300(self):
        for name in ("events.py", "cards.py", "palette.py", "telemetry.py", "hud.py"):
            n = len((ROOT / "ak820" / name).read_text(encoding="utf-8").splitlines())
            self.assertLess(n, 300, f"ak820/{name} is {n} lines")

    def test_top_level_shims_under_300(self):
        for name in ("milestone_radar.py", "widgets.py", "ak820_ctrl.py"):
            n = len((ROOT / name).read_text(encoding="utf-8").splitlines())
            self.assertLess(n, 300, f"{name} is {n} lines")


class TestNoImportCycle(unittest.TestCase):
    def test_fresh_interpreter_imports_everything(self):
        code = (
            f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
            "import ak820.events, ak820.cards, ak820.palette, ak820.telemetry, "
            "ak820.hud, ak820.errors, milestone_radar, widgets, ak820_ctrl; print('ok')"
        )
        res = subprocess.run([sys.executable, "-I", "-c", code], cwd=ROOT,
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("ok", res.stdout)


class TestBehaviorParitySmoke(unittest.TestCase):
    """The split must be behavior-neutral through both surfaces."""

    def test_compute_upcoming_matches_through_both_surfaces(self):
        via_shim = mr.compute_upcoming_events(FIXTURE_CFG, today=FIXTURE_TODAY)
        via_pkg = events.compute_upcoming_events(FIXTURE_CFG, today=FIXTURE_TODAY)
        self.assertEqual(via_shim, via_pkg)
        self.assertEqual([u["id"] for u in via_shim], ["bday", "anni"])

    def test_hero_card_bytes_identical_through_both_surfaces(self):
        import io as _io

        upcoming = events.compute_upcoming_events(FIXTURE_CFG, today=FIXTURE_TODAY)
        b1, b2 = _io.BytesIO(), _io.BytesIO()
        mr.render_hero_card(upcoming, today=FIXTURE_TODAY).save(b1, format="PNG")
        cards.render_hero_card(upcoming, today=FIXTURE_TODAY).save(b2, format="PNG")
        self.assertEqual(b1.getvalue(), b2.getvalue())

    def test_hud_card_bytes_identical_through_both_surfaces(self):
        import io as _io

        stats = {"cpu_pct": 42.0, "ram_pct": 61.0, "ram_used_gb": 39.0,
                 "ram_total_gb": 64.0, "temp_c": 55.0, "uptime": "3d 4h",
                 "hostname": "TEST", "time": "13:37"}
        b1, b2 = _io.BytesIO(), _io.BytesIO()
        widgets.render_hud_card(stats).save(b1, format="PNG")
        hud.render_hud_card(stats).save(b2, format="PNG")
        self.assertEqual(b1.getvalue(), b2.getvalue())


if __name__ == "__main__":
    unittest.main()
