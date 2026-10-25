"""v0.41.0 — m35: `backlight list` required the device it merely describes (CI self-heal).

Live-reproved by CI run 40206869749 (all four Python versions): the v0.40
device-free test `test_backlight_list_prints_table_and_footer` failed with
`3 != 0 : Error: AK820 Pro USB HID interfaces not found` — it passed locally
ONLY because this machine has the keyboard plugged in. The suite's own premise
("device-free") was silently violated by the one unmocked device-bound call in
the whole suite: `_dispatch` routed `backlight list` through discovery + open,
so on hardware-less CI it died with exit 3 instead of printing the catalog.

DECIDED (v0.41.0): route `backlight list` offline in `_dispatch`, before the
--dry-run block — exactly the m22 precedent (`probe`): an informational
command that opens no device and sends zero USB bytes must not require the
device it merely describes, and its output IS the command's result, not a
payload preview. This supersedes v0.29's pin of "nothing to preview" for
`--dry-run backlight list` (the pin moved to a device-bound non-render action
in test_reaudit_v029.py); device-bound actions keep that line unchanged.

Anti-recurrence (the CLASS, not the instance): an AST lock over every shipped
test file — any `main`-style call whose argv names a device-bound command must
mock the device (`find_hidraw_devices` / `AK820ProDevice` / `FakeTransport`)
in the enclosing function, so a future test can never again pass locally on
hardware and break CI. Offline-by-construction argv (--dry-run/--save-preview)
is skipped; the one argparse-rejects-before-discovery site is exempt by name.
The lock proves itself non-vacuous on a synthetic source.

Zero hardware, zero network; no test depends on today's date.
"""

import ast
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ak820 import cli, tables  # noqa: E402
from ak820.presets import SHOWCASE_PRESETS  # noqa: E402


def _main(argv):
    """cli.main(argv) capturing stdout+stderr; SystemExit code becomes the rc."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cli.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


EXPECTED_TABLE = tables.format_preset_table(SHOWCASE_PRESETS)


class TestBacklightListDeviceFree(unittest.TestCase):
    """m35: the catalog command works with zero device access, proven by
    forcing discovery to report NO device (CI's environment) — on a machine
    WITH hardware the unpatched v0.40 test cannot distinguish the two."""

    def test_list_prints_table_when_no_device_exists(self):
        with mock.patch.object(cli, "find_hidraw_devices", return_value=(None, None)):
            rc, out, err = _main(["backlight", "list"])
        self.assertEqual(rc, 0, out + err)
        lines = out.splitlines()
        self.assertEqual(lines[: len(EXPECTED_TABLE)], EXPECTED_TABLE)
        self.assertIn("Supported mode names", "\n".join(lines[len(EXPECTED_TABLE) :]))

    def test_list_never_reaches_discovery(self):
        # Structural: the routing sits BEFORE discovery, so the device layer
        # is not consulted at all — not even to fail. Any future re-route
        # through the device path trips this immediately.
        boom = mock.Mock(side_effect=AssertionError("device access from `backlight list`"))
        with mock.patch.object(cli, "find_hidraw_devices", boom):
            rc, out, err = _main(["backlight", "list"])
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(out.splitlines()[: len(EXPECTED_TABLE)], EXPECTED_TABLE)
        boom.assert_not_called()

    def test_output_identical_hardware_blind_or_not(self):
        # The table is static catalog data — no device-dependent content can
        # leak into it (the same byte-stability the v0.39/v0.40 probes pinned).
        with mock.patch.object(cli, "find_hidraw_devices", return_value=(None, None)):
            _, out_blind, _ = _main(["backlight", "list"])
        with mock.patch.object(
            cli, "find_hidraw_devices", return_value=("/dev/fake-tft", "/dev/fake-ctrl")
        ), mock.patch.object(cli, "AK820ProDevice") as dev_cls:
            dev_cls.return_value.__enter__.return_value = mock.MagicMock()
            _, out_present, _ = _main(["backlight", "list"])
        self.assertEqual(out_blind, out_present)

    def test_dry_run_list_prints_the_table(self):
        # Supersession record (v0.29 pin moved): under --dry-run the table IS
        # the output — m22's probe rationale, verbatim class.
        rc, out, err = _main(["--dry-run", "backlight", "list"])
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(out.splitlines()[: len(EXPECTED_TABLE)], EXPECTED_TABLE)
        self.assertNotIn("nothing to preview", out)

    def test_dry_run_device_bound_action_keeps_nothing_to_preview(self):
        # The v0.29 invariant survives for actions that DO send bytes: the
        # dry-run block still answers them with the documented line.
        rc, out, err = _main(["--dry-run", "backlight", "set", "--preset", "warm-white"])
        self.assertEqual(rc, 0, out + err)
        self.assertIn("nothing to preview", out)

    def test_bare_backlight_still_usage_2(self):
        # The new branch keys on bl_action == "list" only — D21-B untouched.
        rc, out, err = _main(["backlight"])
        self.assertEqual(rc, 2)


# ---------------------------------------------------------------------------
# The class lock: every device-bound CLI call in the shipped suite mocks the
# device. A test that forgets passes locally on hardware and breaks CI — the
# exact failure this iteration heals.
# ---------------------------------------------------------------------------

DEVICE_BOUND_COMMANDS = {
    "upload",
    "radar",
    "card",
    "hud",
    "stats",
    "media",
    "sync-clock",
    "status",
    "run",
}
BACKLIGHT_DEVICE_ACTIONS = {"set", "off", "demo"}  # "list" is offline since m35
OFFLINE_FLAGS = {"--dry-run", "--save-preview"}
DEVICE_MOCK_MARKERS = ("find_hidraw_devices", "AK820ProDevice", "FakeTransport", "fake_transport")
# argv rejected by argparse BEFORE discovery — no device is ever reachable:
EXEMPT = {("test_minors_v030.py", "test_non_finite_still_rejected_d24_regression")}


def _is_main_call(node: ast.Call) -> bool:
    f = node.func
    if not node.args:
        return False
    if isinstance(f, ast.Attribute):
        return f.attr == "main"
    return isinstance(f, ast.Name) and (f.id == "main" or f.id.startswith("_main") or f.id.endswith("_main"))


def _argv_strings(call: ast.Call) -> list[str] | None:
    """Literal argv strings of a main-style call, or None if argv is dynamic."""
    first = call.args[0]
    if not isinstance(first, ast.List) or not first.elts:
        return None
    head = first.elts[0]
    if not (isinstance(head, ast.Constant) and isinstance(head.value, str)):
        return None
    return [e.value for e in first.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]


def _device_bound(strs: list[str]) -> str | None:
    """The device-bound command name an argv reaches dispatch with, else None."""
    if any(s in OFFLINE_FLAGS for s in strs):
        return None
    cmd = strs[0]
    if cmd in DEVICE_BOUND_COMMANDS:
        return cmd
    if cmd == "backlight" and any(s in BACKLIGHT_DEVICE_ACTIONS for s in strs):
        return "backlight"
    return None


def find_unmocked_device_calls(source: str, filename: str) -> list[str]:
    """Violations: device-bound main-style calls whose enclosing function
    mocks none of the device layer."""
    tree = ast.parse(source, filename=filename)
    violations: list[str] = []

    def scan(node, funcname):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call) and _is_main_call(child):
                strs = _argv_strings(child)
                if strs is None:
                    continue
                cmd = _device_bound(strs)
                if cmd is None:
                    continue
                if (filename, funcname) in EXEMPT:
                    continue
                if funcname is None:
                    violations.append(f"{filename}:<module>: {cmd}")
                    continue
                fn = child._enclosing  # set below
                seg = ast.get_source_segment(source, fn) or ""
                if not any(marker in seg for marker in DEVICE_MOCK_MARKERS):
                    violations.append(f"{filename}:{child.lineno}: {cmd} in {funcname}()")
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for inner in ast.walk(child):
                    inner._enclosing = child
                scan(child, child.name)
            else:
                scan(child, funcname)

    scan(tree, None)
    return violations


class TestDeviceBoundTestsMockTheDevice(unittest.TestCase):
    """Anti-recurrence lock for m35: no shipped test may invoke a device-bound
    CLI command without mocking the device — the v0.40 test did exactly that
    and only CI (no hardware) could see it."""

    def test_suite_has_no_unmocked_device_bound_calls(self):
        violations: list[str] = []
        for path in sorted((ROOT / "tests").glob("test_*.py")):
            violations += find_unmocked_device_calls(path.read_text(encoding="utf-8"), path.name)
        self.assertEqual(violations, [], "device-bound CLI calls without a device mock:\n" + "\n".join(violations))

    def test_lock_is_not_vacuous(self):
        synthetic = '''
import cli
class T:
    def test_bad(self):
        cli.main(["upload", "x.png"])

    def test_good(self):
        with mock.patch.object(cli, "find_hidraw_devices", return_value=(None, None)):
            cli.main(["upload", "x.png"])

    def test_offline_skipped(self):
        cli.main(["--dry-run", "upload", "x.png"])

    def test_list_offline(self):
        cli.main(["backlight", "list"])

    def test_dynamic_argv_skipped(self):
        cli.main(build_argv())
'''
        v = find_unmocked_device_calls(synthetic, "synthetic.py")
        self.assertEqual(v, ["synthetic.py:5: upload in test_bad()"])


if __name__ == "__main__":
    unittest.main()
