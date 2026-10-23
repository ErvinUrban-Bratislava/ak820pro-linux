#!/usr/bin/env python3
"""v0.10.0 invariant audit: cross-cutting contracts that must NEVER regress.

1. Every shipped .py module compiles (py_compile) — mirrors the CI gate.
2. Every AK820Error subclass carries an int exit_code in the documented set
   AND `cli.main()` actually maps it to that exact process exit code (§3.4).
3. README "Exit Codes" table stays in lockstep with `ak820/errors.py`
   (documentation cannot silently drift from the shipped contract).
4. pyproject console scripts point at `ak820.cli:main` (pipx entry points).
"""

import io
import py_compile
import re
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ak820 import cli
from ak820.errors import AK820Error

# §3.4 exit-code contract (D8 reconciled v0.12.0): 0 success, 2 argparse usage,
# 3 device not found, 4 permission denied, 5 upload incomplete,
# 6 invalid events config, 7 protocol desync, 8 input file not found (D11),
# 75 EX_TEMPFAIL transient mid-op disconnect (systemd Restart= retries).
DOCUMENTED_CODES = {0, 2, 3, 4, 5, 6, 7, 8, 75}


def _all_error_classes(cls=AK820Error):
    """Recursively collect every concrete AK820Error subclass."""
    for sub in cls.__subclasses__():
        yield sub
        yield from _all_error_classes(sub)


class TestCompileInvariant(unittest.TestCase):
    """CI gate mirrored offline: every shipped module must py_compile."""

    def test_all_shipped_modules_compile(self):
        files = [ROOT / "ak820_ctrl.py", ROOT / "milestone_radar.py", ROOT / "widgets.py"]
        files += sorted((ROOT / "ak820").glob("*.py"))
        files += sorted((ROOT / "tests").glob("*.py"))
        for py in files:
            try:
                py_compile.compile(str(py), doraise=True)
            except py_compile.PyCompileError as exc:
                self.fail(f"{py.relative_to(ROOT)} failed py_compile: {exc}")


class TestErrorTaxonomyInvariant(unittest.TestCase):
    """Every error class: int exit_code in the documented set, no orphans."""

    def test_taxonomy_is_non_empty(self):
        classes = list(_all_error_classes())
        self.assertGreaterEqual(len(classes), 5, "error taxonomy unexpectedly shrank")

    def test_every_class_has_documented_exit_code(self):
        for cls in _all_error_classes():
            code = getattr(cls, "exit_code", None)
            self.assertIsInstance(code, int, f"{cls.__name__}.exit_code not an int")
            self.assertIn(code, DOCUMENTED_CODES, f"{cls.__name__}.exit_code={code} undocumented")

    def test_main_maps_every_error_class_to_its_exit_code(self):
        for cls in _all_error_classes():
            with self.subTest(cls.__name__):
                with mock.patch.object(cli, "_dispatch", side_effect=cls("boom")), \
                     redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as ctx:
                        cli.main(["probe"])
                self.assertEqual(ctx.exception.code, cls.exit_code)


class TestReadmeExitCodeSync(unittest.TestCase):
    """README's Exit Codes table must list exactly the codes errors.py ships."""

    def _table_codes(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        section = re.search(r"## Exit Codes\n(.*?)(?=\n## )", text, re.S)
        self.assertIsNotNone(section, "README lost its '## Exit Codes' section")
        return {int(m) for m in re.findall(r"^\|\s*`(\d+)`", section.group(1), re.M)}

    def test_readme_covers_every_shipped_exit_code(self):
        codes = self._table_codes()
        shipped = {cls.exit_code for cls in _all_error_classes()} | {0, 2}
        missing = shipped - codes
        self.assertFalse(missing, f"README Exit Codes table missing: {sorted(missing)}")

    def test_readme_documents_every_error_class_name(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for cls in _all_error_classes():
            self.assertIn(cls.__name__, text, f"README never names {cls.__name__}")


class TestDependencyRuleInvariant(unittest.TestCase):
    """§10.2 (enforced from v0.12.0): the fd/io seam lives ONLY in transport.py,
    and the protocol/device/state seam never reaches into the rendering domain."""

    def test_no_fd_io_outside_transport(self):
        for py in sorted((ROOT / "ak820").glob("*.py")):
            if py.name == "transport.py":
                continue
            text = py.read_text(encoding="utf-8")
            for forbidden in ("import fcntl", "import select", "os.open("):
                self.assertNotIn(
                    forbidden, text,
                    f"{py.name} touches fd/io ({forbidden!r}) outside transport.py (§10.2)",
                )

    def test_protocol_seam_never_imports_rendering(self):
        import_line = re.compile(r"^\s*(from|import)\s+milestone_radar\b", re.M)
        for name in ("protocol.py", "transport.py", "device.py", "state.py", "screen.py"):
            text = (ROOT / "ak820" / name).read_text(encoding="utf-8")
            self.assertIsNone(
                import_line.search(text),
                f"{name} imports the rendering domain milestone_radar (§10.2)",
            )


class TestEntryPointInvariant(unittest.TestCase):
    """pipx / console scripts must keep resolving to ak820.cli:main."""

    def test_pyproject_scripts_target_cli_main(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        scripts = text.split("[project.scripts]", 1)[-1].split("[", 1)[0]
        self.assertIn('ak820 = "ak820.cli:main"', scripts)
        self.assertIn('ak820-ctrl = "ak820.cli:main"', scripts)


if __name__ == "__main__":
    unittest.main()
