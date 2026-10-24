"""Sysfs device discovery for the AK820 Pro hidraw nodes (v0.19.0, §18).

Split out of `transport.py` so each module keeps one responsibility: this
module answers *which* nodes belong to the keyboard, `transport.py` answers
*how* to talk over them. Discovery reads sysfs attribute files with plain
`open()` only — no raw fds, no ioctls, no polling — so the §10.2 fd/io seam
stays exclusive to `transport.py` (refined in §18: sysfs path strings and
plain attribute reads live here) and this module is fully testable against a
fake sysfs tree (`base=`).

hidraw numbering follows probe-attach order, not per-device identity (D6):
hardcoded hidrawN node paths watch the wrong device once a hub or dongle
shifts the numbering, so every scan matches by VID/PID from the uevent HID_ID
line and separates interfaces by the report descriptor's Usage Page.
"""

import os

from . import protocol
from .protocol import parse_usage_page


def _read_hid_identity(dev_dir):
    """Parse (vid, pid) from a sysfs device dir's uevent HID_ID line, or None."""
    try:
        with open(os.path.join(dev_dir, "uevent")) as f:
            uevent = f.read()
    except OSError:
        return None

    hid_id_line = [line for line in uevent.splitlines() if line.startswith("HID_ID=")]
    if not hid_id_line:
        return None
    parts = hid_id_line[0].split("=")[1].split(":")
    if len(parts) != 3:
        return None

    try:
        return int(parts[1], 16), int(parts[2], 16)
    except ValueError:
        return None


def _parse_usage_page(rd_path):
    """Extract the top-level Usage Page from a HID report descriptor file."""
    try:
        with open(rd_path, "rb") as f:
            desc = f.read()
    except OSError:
        return None
    return parse_usage_page(desc)


def find_hidraw_devices(base="/sys/class/hidraw"):
    """Locate the Data pipe (0xFF68) and Control pipe (0xFF13) for the AK820 Pro.

    `base` is parameterized so tests can point at a fake sysfs tree (D6/B8).
    """
    tft_dev = None
    ctrl_dev = None
    if not os.path.isdir(base):
        return None, None

    for entry in sorted(os.listdir(base)):
        dev_dir = os.path.join(base, entry, "device")
        identity = _read_hid_identity(dev_dir)
        if identity is None:
            continue

        dev_vid, dev_pid = identity
        if dev_vid != protocol.VID or dev_pid not in protocol.PIDS:
            continue

        usage_page = _parse_usage_page(os.path.join(dev_dir, "report_descriptor"))
        if usage_page == protocol.USAGE_PAGE_DATA:
            tft_dev = f"/dev/{entry}"
        elif usage_page == protocol.USAGE_PAGE_CONTROL:
            ctrl_dev = f"/dev/{entry}"

    return tft_dev, ctrl_dev


def find_keyboard_hidraw(base="/sys/class/hidraw"):
    """All hidraw nodes belonging to the AK820 Pro's own HID interfaces (D6).

    hidraw numbering follows probe-attach order, not per-device identity:
    hardcoded /dev/hidrawN paths watch the wrong device once a hub or dongle
    shifts the numbering. Scanning sysfs by VID/PID keeps discovery stable.
    """
    paths = []
    if not os.path.isdir(base):
        return paths
    for entry in sorted(os.listdir(base)):
        identity = _read_hid_identity(os.path.join(base, entry, "device"))
        if identity is None:
            continue
        dev_vid, dev_pid = identity
        if dev_vid == protocol.VID and dev_pid in protocol.PIDS:
            paths.append(f"/dev/{entry}")
    return paths
