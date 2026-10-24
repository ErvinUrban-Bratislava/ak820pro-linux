#!/usr/bin/env python3
"""
Regression tests for hidraw device discovery.

Covers Defect D6: `wait_for_keypress()` hardcoded `/dev/hidraw0` and
`/dev/hidraw1`. hidraw numbering is assigned in probe-attach order, so any
hub/dongle plugged in first shifts the numbers and the keypress wait watches
someone else's device (or silently falls back to stdin). Discovery must scan
sysfs by VID/PID instead, via `find_keyboard_hidraw()`.

Also covers the `base=` parameterization (REVIEW B8) so the scan is testable
against a fake sysfs tree with no hardware attached.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ak820_ctrl
from ak820 import transport  # v0.9.0: wait_for_keypress lives in transport.py


def _mk_hidraw(base, name, vid, pid, usage=None):
    """Create a fake /sys/class/hidraw/<name>/device tree."""
    dev = os.path.join(base, name, "device")
    os.makedirs(dev)
    with open(os.path.join(dev, "uevent"), "w") as f:
        f.write(f"DRIVER=hid\nHID_ID=0003:{vid:08X}:{pid:08X}\n")
    if usage is not None:
        if usage <= 0xFF:
            rd = bytes([0x05, usage])           # Usage Page, 1-byte item
        else:
            rd = bytes([0x06, usage & 0xFF, (usage >> 8) & 0xFF])  # 2-byte item
        with open(os.path.join(dev, "report_descriptor"), "wb") as f:
            f.write(rd)


class FakeSysfsScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name
        _mk_hidraw(self.base, "hidraw0", 0x045E, 0x0850)                    # other vendor
        _mk_hidraw(self.base, "hidraw1", ak820_ctrl.VID, 0x800A, usage=0xFF68)   # data pipe
        _mk_hidraw(self.base, "hidraw2", ak820_ctrl.VID, 0x800A, usage=0xFF13)   # control pipe
        _mk_hidraw(self.base, "hidraw3", ak820_ctrl.VID, 0x800A, usage=0x01)     # keyboard
        _mk_hidraw(self.base, "hidraw4", ak820_ctrl.VID, 0x800A)                 # no report desc
        _mk_hidraw(self.base, "hidraw5", ak820_ctrl.VID, 0x1234, usage=0x01)     # wrong PID

    def tearDown(self):
        self.tmp.cleanup()

    def test_find_hidraw_devices_via_fake_sysfs(self):
        tft, ctrl = ak820_ctrl.find_hidraw_devices(base=self.base)
        self.assertEqual(tft, "/dev/hidraw1")
        self.assertEqual(ctrl, "/dev/hidraw2")

    def test_find_keyboard_hidraw_returns_all_ak820_nodes(self):
        paths = ak820_ctrl.find_keyboard_hidraw(base=self.base)
        self.assertEqual(
            paths, ["/dev/hidraw1", "/dev/hidraw2", "/dev/hidraw3", "/dev/hidraw4"]
        )
        # foreign vendor (hidraw0) and wrong PID (hidraw5) must never appear
        self.assertNotIn("/dev/hidraw0", paths)
        self.assertNotIn("/dev/hidraw5", paths)

    def test_missing_sysfs_base_is_graceful(self):
        missing = os.path.join(self.base, "does-not-exist")
        self.assertEqual(ak820_ctrl.find_keyboard_hidraw(base=missing), [])
        self.assertEqual(ak820_ctrl.find_hidraw_devices(base=missing), (None, None))


class WaitForKeypressDynamicScanTests(unittest.TestCase):
    """D6: wait_for_keypress must open the sysfs-discovered nodes, not hidraw0/1."""

    def test_opens_discovered_paths_not_hardcoded(self):
        with mock.patch.object(transport, "find_keyboard_hidraw",
                               return_value=["/dev/hidraw999"]) as m_find, \
             mock.patch.object(ak820_ctrl.os, "open", return_value=42) as m_open, \
             mock.patch.object(ak820_ctrl.os, "read", return_value=b"\x01" * 64), \
             mock.patch.object(ak820_ctrl.os, "close"), \
             mock.patch.object(ak820_ctrl.select, "select",
                               side_effect=[([], [], []), ([42], [], [])]):
            result = ak820_ctrl.wait_for_keypress(timeout=1)
        self.assertTrue(result)
        m_find.assert_called_once()
        opened = [c.args[0] for c in m_open.call_args_list]
        self.assertEqual(opened, ["/dev/hidraw999"])
        self.assertNotIn("/dev/hidraw0", opened)
        self.assertNotIn("/dev/hidraw1", opened)

    def test_stdin_fallback_when_no_device_found(self):
        with mock.patch.object(transport, "find_keyboard_hidraw", return_value=[]), \
             mock.patch.object(ak820_ctrl.select, "select",
                               side_effect=[([], [], [])]) as m_sel:
            result = ak820_ctrl.wait_for_keypress(timeout=0)
        self.assertFalse(result)
        m_sel.assert_called_once()


if __name__ == "__main__":
    unittest.main()
