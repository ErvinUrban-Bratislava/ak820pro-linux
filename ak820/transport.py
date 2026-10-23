"""HID transport seam: the one module allowed to touch fds/ioctls/sysfs (v0.7.0).

`HidTransport` is the injection seam from REVIEW.md §5.2: everything above it
(`device.AK820Pro`) is a pure state machine testable without hardware.
`LinuxHidrawTransport` wraps real fds and translates errno into the
`errors.py` taxonomy (§3.3), so callers never see bare OSErrors.
"""

import errno
import fcntl
import os
import select
from typing import Optional, Protocol, runtime_checkable

from . import protocol
from .errors import DeviceDisconnectedError, PermissionDeniedError
from .protocol import parse_usage_page


@runtime_checkable
class HidTransport(Protocol):
    """Control pipe (feature reports) + data pipe (bulk chunks + ACKs)."""

    def set_feature(self, report64: bytes) -> None: ...

    def get_feature(self) -> bytes: ...

    def write_data(self, payload: bytes) -> None: ...

    def read_data(self, timeout: float) -> Optional[bytes]: ...


class LinuxHidrawTransport:
    """Real-fd transport over two hidraw nodes (control + data interface).

    Owns the fds it is handed and closes them exactly once (fixes m1 leak).
    """

    def __init__(self, ctrl_fd=None, data_fd=None):
        self.ctrl_fd = ctrl_fd
        self.data_fd = data_fd

    def close(self):
        for attr in ("data_fd", "ctrl_fd"):
            fd = getattr(self, attr)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, attr, None)

    @staticmethod
    def _translate(exc: OSError, what: str):
        """Map a raw OSError onto the AK820Error taxonomy (§3.3)."""
        if exc.errno in (errno.EPERM, errno.EACCES):
            return PermissionDeniedError(
                f"{what}: permission denied on the device node "
                "(install the udev rule from udev/ or run with access to /dev)."
            )
        if exc.errno == errno.ENODEV:
            return DeviceDisconnectedError(f"{what}: device disappeared (ENODEV).")
        return exc

    # --- control pipe (Interface 3, Usage Page 0xFF13) ----------------------
    def set_feature(self, report64: bytes) -> None:
        """Send a 64-byte feature report with Report ID 0x00."""
        buf = bytearray(65)
        buf[0] = 0x00  # Always report ID 0x00 so kernel strips it cleanly
        buf[1:1 + min(len(report64), 64)] = bytes(report64)[:64]
        try:
            fcntl.ioctl(self.ctrl_fd, protocol.HIDIOCSFEATURE_65, bytes(buf))
        except OSError as exc:
            raise self._translate(exc, "SET_FEATURE") from exc

    def get_feature(self) -> bytes:
        """Read back a 64-byte feature report (handshake ACK)."""
        buf = bytearray(65)
        buf[0] = 0x00
        try:
            fcntl.ioctl(self.ctrl_fd, protocol.HIDIOCGFEATURE_65, buf)
        except OSError as exc:
            raise self._translate(exc, "GET_FEATURE") from exc
        return bytes(buf[1:])

    # --- data pipe (Interface 2, Usage Page 0xFF68) --------------------------
    def write_data(self, payload: bytes) -> None:
        # Prepend 0x00 so hidraw strips the report ID byte and sends the full payload
        try:
            os.write(self.data_fd, b"\x00" + bytes(payload))
        except OSError as exc:
            raise self._translate(exc, "data-pipe write") from exc

    def read_data(self, timeout: float) -> Optional[bytes]:
        """Return one 64-byte ACK if one arrives within `timeout`, else None."""
        try:
            r, _, _ = select.select([self.data_fd], [], [], timeout)
            if r:
                return os.read(self.data_fd, 64)
        except OSError:
            return None
        return None


def _read_hid_identity(dev_dir):
    """Parse (vid, pid) from a sysfs device dir's uevent HID_ID line, or None."""
    try:
        with open(os.path.join(dev_dir, "uevent")) as f:
            uevent = f.read()
    except OSError:
        return None

    hid_id_line = [l for l in uevent.splitlines() if l.startswith("HID_ID=")]
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
