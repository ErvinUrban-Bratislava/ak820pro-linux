#!/usr/bin/env python3
"""v0.19.0 regression suite: the transport.py -> discovery.py split (§18).

Locks the modularity refactor against the failure modes that matter:
1. Re-export identity — `transport.X is discovery.X` and the `ak820` package
   and `ak820_ctrl` shim resolve every discovery name to the SAME object
   (one implementation, no copy drift).
2. §10.2 fd/io seam — discovery.py answers *which* nodes, transport.py
   answers *how*; discovery must contain no fd/io (no `import fcntl`,
   `import select`, `os.open(`) so the transport-only seam invariant holds
   for the new module too.
3. Mock-patch surface — `mock.patch.object(transport, "find_keyboard_hidraw")`
   must still steer `wait_for_keypress` (many existing tests depend on it).
4. Functional parity — fake-sysfs scans behave exactly as before the move.
5. Module budget (<300 lines) and no import cycle in a fresh interpreter.
All offline, zero hardware.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ak820
import ak820_ctrl
from ak820 import cli, discovery, transport

DISCOVERY_NAMES = (
    "find_hidraw_devices",
    "find_keyboard_hidraw",
    "_read_hid_identity",
    "_parse_usage_page",
)


def _mk_hidraw(base, name, vid, pid, usage=None):
    """Create a fake /sys/class/hidraw/<name>/device tree."""
    dev = os.path.join(base, name, "device")
    os.makedirs(dev)
    with open(os.path.join(dev, "uevent"), "w") as f:
        f.write(f"DRIVER=hid\nHID_ID=0003:{vid:08X}:{pid:08X}\n")
    if usage is not None:
        if usage <= 0xFF:
            rd = bytes([0x05, usage])
        else:
            rd = bytes([0x06, usage & 0xFF, (usage >> 8) & 0xFF])
        with open(os.path.join(dev, "report_descriptor"), "wb") as f:
            f.write(rd)


class TestReexportIdentity(unittest.TestCase):
    """discovery is the canonical home; every compat surface aliases it."""

    def test_transport_reexports_are_the_same_objects(self):
        for name in DISCOVERY_NAMES:
            self.assertIs(getattr(transport, name), getattr(discovery, name),
                          f"transport.{name} is a copy, not the discovery object")

    def test_package_and_shim_resolve_to_discovery(self):
        for name in ("find_hidraw_devices", "find_keyboard_hidraw"):
            self.assertIs(getattr(ak820, name), getattr(discovery, name),
                          f"ak820.{name} drifted")
            self.assertIs(getattr(ak820_ctrl, name), getattr(discovery, name),
                          f"ak820_ctrl.{name} drifted")

    def test_cli_uses_the_discovery_object(self):
        self.assertIs(cli.find_hidraw_devices, discovery.find_hidraw_devices)


class TestFdIoSeamPreserved(unittest.TestCase):
    """§10.2: discovery answers *which* nodes with plain file reads only;
    the fd/io seam (fcntl/select/os.open) stays exclusive to transport.py."""

    def test_discovery_module_has_no_fd_io(self):
        text = (ROOT / "ak820" / "discovery.py").read_text(encoding="utf-8")
        for forbidden in ("import fcntl", "import select", "os.open("):
            self.assertNotIn(forbidden, text,
                             f"discovery.py touches fd/io ({forbidden!r}) — §10.2 breach")

    def test_transport_still_owns_the_io_primitives(self):
        text = (ROOT / "ak820" / "transport.py").read_text(encoding="utf-8")
        for required in ("import fcntl", "import select", "os.open("):
            self.assertIn(required, text,
                          f"transport.py lost its fd/io role ({required!r} missing)")


class TestMockPatchSurface(unittest.TestCase):
    """wait_for_keypress must resolve find_keyboard_hidraw through the
    transport module globals so existing mock targets keep working."""

    def test_patching_transport_name_steers_wait_for_keypress(self):
        with mock.patch.object(transport, "find_keyboard_hidraw", return_value=[]), \
                mock.patch.object(transport.select, "select",
                                  return_value=([], [], [])) as sel:
            self.assertFalse(transport.wait_for_keypress(timeout=0))
        sel.assert_called_once()  # fell through to the stdin fallback

    def test_patched_paths_are_used_as_fds_source(self):
        # A fake hidraw path that does not exist is skipped by os.path.exists,
        # so the call still degrades to the stdin fallback without raising.
        with mock.patch.object(transport, "find_keyboard_hidraw",
                               return_value=["/nonexistent/hidraw9"]), \
                mock.patch.object(transport.select, "select",
                                  return_value=([], [], [])):
            self.assertFalse(transport.wait_for_keypress(timeout=0))


class TestFakeSysfsParity(unittest.TestCase):
    """Functional parity: identical scan semantics through discovery directly."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name
        _mk_hidraw(self.base, "hidraw0", 0x045E, 0x0850)                          # other vendor
        _mk_hidraw(self.base, "hidraw1", ak820_ctrl.VID, 0x800A, usage=0xFF68)    # data pipe
        _mk_hidraw(self.base, "hidraw2", ak820_ctrl.VID, 0x800A, usage=0xFF13)    # control pipe
        _mk_hidraw(self.base, "hidraw3", ak820_ctrl.VID, 0x800A, usage=0x01)      # keyboard
        _mk_hidraw(self.base, "hidraw4", ak820_ctrl.VID, 0x800A)                  # no report desc
        _mk_hidraw(self.base, "hidraw5", ak820_ctrl.VID, 0x1234, usage=0x01)      # wrong PID

    def tearDown(self):
        self.tmp.cleanup()

    def test_find_hidraw_devices_via_discovery(self):
        tft, ctrl = discovery.find_hidraw_devices(base=self.base)
        self.assertEqual(tft, "/dev/hidraw1")
        self.assertEqual(ctrl, "/dev/hidraw2")

    def test_find_keyboard_hidraw_via_discovery(self):
        paths = discovery.find_keyboard_hidraw(base=self.base)
        self.assertEqual(paths, ["/dev/hidraw1", "/dev/hidraw2",
                                 "/dev/hidraw3", "/dev/hidraw4"])

    def test_missing_base_is_empty_not_error(self):
        missing = os.path.join(self.base, "nope")
        self.assertEqual(discovery.find_keyboard_hidraw(base=missing), [])
        self.assertEqual(discovery.find_hidraw_devices(base=missing), (None, None))

    def test_identity_helpers(self):
        vid, pid = discovery._read_hid_identity(
            os.path.join(self.base, "hidraw1", "device"))
        self.assertEqual((vid, pid), (ak820_ctrl.VID, 0x800A))
        self.assertEqual(
            discovery._read_hid_identity(os.path.join(self.base, "hidraw0", "device")),
            (0x045E, 0x0850))  # parses any vendor; filtering happens in the scan
        self.assertIsNone(discovery._read_hid_identity(os.path.join(self.base, "ghost")))

    def test_parse_usage_page_helper(self):
        rd = os.path.join(self.base, "hidraw1", "device", "report_descriptor")
        self.assertEqual(discovery._parse_usage_page(rd), 0xFF68)
        self.assertIsNone(discovery._parse_usage_page(os.path.join(self.base, "ghost")))


class TestBudgetAndNoCycle(unittest.TestCase):
    def test_discovery_under_300_lines(self):
        n = len((ROOT / "ak820" / "discovery.py").read_text(encoding="utf-8").splitlines())
        self.assertLess(n, 300, f"discovery.py is {n} lines")

    def test_transport_shrank_after_split(self):
        n = len((ROOT / "ak820" / "transport.py").read_text(encoding="utf-8").splitlines())
        self.assertLess(n, 250, f"transport.py should have shrunk well below budget, is {n}")

    def test_fresh_interpreter_imports_discovery_without_cycle(self):
        code = (f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
                "import ak820.discovery, ak820.transport, ak820.cli, ak820_ctrl; "
                "import ak820; print('ok')")
        res = subprocess.run([sys.executable, "-I", "-c", code], cwd=ROOT,
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("ok", res.stdout)


if __name__ == "__main__":
    unittest.main()
