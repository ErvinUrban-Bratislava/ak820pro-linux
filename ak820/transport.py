"""HID transport seam: the one module allowed to touch fds/ioctls/select (v0.7.0).

`HidTransport` is the injection seam from REVIEW.md §5.2: everything above it
(`device.AK820Pro`) is a pure state machine testable without hardware.
`LinuxHidrawTransport` wraps real fds and translates errno into the
`errors.py` taxonomy (§3.3), so callers never see bare OSErrors.

v0.19.0 (§18): sysfs *discovery* (which /dev nodes are ours) moved to
`discovery.py`; this module re-exports those names (identity, not copies) so
every historical import site — `ak820.transport.find_hidraw_devices`, the
`ak820_ctrl` shim, and mock-patch targets in the test suite — keeps working.
"""

import contextlib
import errno
import fcntl
import os
import select
import sys
from typing import Protocol, runtime_checkable

from . import protocol
from .discovery import (  # v0.19.0 re-exports (identity, not copies): §18
    _parse_usage_page,
    _read_hid_identity,
    find_hidraw_devices,
    find_keyboard_hidraw,
)
from .errors import DeviceDisconnectedError, PermissionDeniedError, ProtocolError

# v0.19.0 backward-compat contract (§18): identity helpers are re-exported
# through this module so every pre-split import site and mock.patch target
# keeps working. `__all__` documents the contract and pins the re-exports
# against the lint gate's unused-import rule (F401).
__all__ = [
    "AK820ProDevice",
    "HidTransport",
    "LinuxHidrawTransport",
    "_parse_usage_page",
    "_read_hid_identity",
    "find_hidraw_devices",
    "find_keyboard_hidraw",
    "wait_for_keypress",
]


@runtime_checkable
class HidTransport(Protocol):
    """Control pipe (feature reports) + data pipe (bulk chunks + ACKs)."""

    def set_feature(self, report64: bytes) -> None: ...

    def get_feature(self) -> bytes: ...

    def write_data(self, payload: bytes) -> None: ...

    def read_data(self, timeout: float) -> bytes | None: ...


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
                with contextlib.suppress(OSError):
                    os.close(fd)
                setattr(self, attr, None)

    @staticmethod
    def _translate(exc: OSError, what: str):
        """Map a raw OSError onto the AK820Error taxonomy (§3.3)."""
        if exc.errno in (errno.EPERM, errno.EACCES):
            return PermissionDeniedError(
                f"{what}: permission denied on the device node "
                "(install the udev rule from udev/ or run with access to /dev)."
            )
        if exc.errno in (errno.ENODEV, errno.ENOENT, errno.ENXIO):
            # v0.20.0 (§19): ENOENT/ENXIO join ENODEV — a node that vanished
            # between sysfs discovery and open (or whose driver detached) is
            # the same transient mid-op disconnect, exit 75, not a traceback.
            return DeviceDisconnectedError(
                f"{what}: device disappeared ({exc.strerror or exc.errno})."
            )
        return exc

    # --- control pipe (Interface 3, Usage Page 0xFF13) ----------------------
    def set_feature(self, report64: bytes) -> None:
        """Send a 64-byte feature report with Report ID 0x00."""
        buf = bytearray(65)
        buf[0] = 0x00  # Always report ID 0x00 so kernel strips it cleanly
        buf[1 : 1 + min(len(report64), 64)] = bytes(report64)[:64]
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
        """Write-all loop (v0.17.0, §16): the 0x00-prefixed payload must land in
        full — a short write desyncs the firmware ring counter like a dropped
        packet. Zero-progress attempts are capped so a wedged pipe fails fast.
        """
        view = memoryview(b"\x00" + bytes(payload))
        written = stalls = 0
        try:
            while written < len(view):
                n = os.write(self.data_fd, view[written:])
                if n <= 0:
                    stalls += 1
                    if stalls >= protocol.MAX_WRITE_RETRIES:
                        raise ProtocolError(
                            f"data-pipe write stalled: {written}/{len(view)} bytes "
                            f"accepted after {protocol.MAX_WRITE_RETRIES} attempts."
                        )
                    continue
                written += n
                stalls = 0
        except OSError as exc:
            raise self._translate(exc, "data-pipe write") from exc

    def read_data(self, timeout: float) -> bytes | None:
        """Return one 64-byte ACK if one arrives within `timeout`, else None.

        Timeout guard (v0.11.0): a plain timeout is None, but ENODEV means the
        device vanished — surface it as DeviceDisconnectedError (exit 75) so an
        unplug mid-upload is a recoverable event, not a silent ACK shortfall
        misattributed to flaky firmware (UploadIncompleteError, exit 5).
        """
        try:
            r, _, _ = select.select([self.data_fd], [], [], timeout)
            if r:
                return os.read(self.data_fd, 64)
        except OSError as exc:
            translated = self._translate(exc, "data-pipe read")
            if isinstance(translated, DeviceDisconnectedError):
                raise translated from exc
            return None  # EAGAIN/EBADF etc: treat as "no ACK within timeout"
        return None


def wait_for_keypress(timeout=None):
    """Wait for user input on the AK820 Pro keyboard (keypress or knob event).

    Lives here because it is the only keypress path that touches fds/select
    (§10.2: fd/io code stays inside transport.py). D6: dynamic sysfs scan.
    """
    dev_paths = find_keyboard_hidraw()
    fds = []
    for p in dev_paths:
        with contextlib.suppress(OSError):
            fds.append(os.open(p, os.O_RDONLY | os.O_NONBLOCK))
    if not fds:
        # Fallback to stdin wait if no AK820 Pro hidraw node is accessible.
        # v0.14.0: select() reports /dev/null (and any EOF pipe) as *readable*,
        # which used to answer an instant false "keypress". Consume the byte
        # and treat EOF (b"") as no keypress — only real input counts.
        r, _, _ = select.select([sys.stdin], [], [], timeout)
        if not r:
            return False
        try:
            return bool(os.read(sys.stdin.fileno(), 1))
        except OSError:
            return False
    try:
        # Drain stale unread inputs
        for fd in fds:
            while select.select([fd], [], [], 0)[0]:
                try:
                    os.read(fd, 64)
                except OSError:
                    break
        # Wait for next input
        r, _, _ = select.select(fds, [], [], timeout)
        if r:
            for fd in r:
                with contextlib.suppress(OSError):
                    os.read(fd, 64)
            return True
        return False
    finally:
        for fd in fds:
            os.close(fd)


class AK820ProDevice:
    """Thin factory: opens the hidraw nodes and delegates every protocol call
    to an `AK820Pro` state machine over a `LinuxHidrawTransport` (v0.7.0 seam).
    Constructor signature and context-manager usage are unchanged for callers."""

    def __init__(self, tft_path, ctrl_path):
        self.tft_path = tft_path
        self.ctrl_path = ctrl_path
        self.pro = None

    def __enter__(self):
        from .device import AK820Pro  # local import: device imports this module

        ctrl_fd = data_fd = None
        try:
            if self.ctrl_path:
                ctrl_fd = os.open(self.ctrl_path, os.O_RDWR)
            if self.tft_path:
                data_fd = os.open(self.tft_path, os.O_RDWR)
        except OSError as exc:
            # m1: never leak the fd that did open when the second one fails
            for fd in (ctrl_fd, data_fd):
                if fd is not None:
                    os.close(fd)
            # D15 (v0.20.0, §19): an unplug between discovery and open (ENOENT/
            # ENXIO/ENODEV) or a missing udev rule (EACCES/EPERM) used to leak a
            # raw OSError traceback (exit 1) — translate to the taxonomy: 75 / 4.
            raise LinuxHidrawTransport._translate(exc, "hidraw open") from exc
        self.pro = AK820Pro(LinuxHidrawTransport(ctrl_fd, data_fd))
        return self

    def __exit__(self, *args):
        if self.pro is not None:
            self.pro.close()
            self.pro = None

    def __getattr__(self, name):
        """Delegate protocol calls (send_feature, handshake, send_data_chunk,
        align_data_pipe, sync_clock, ...) to the underlying AK820Pro."""
        pro = self.__dict__.get("pro")
        if pro is not None:
            return getattr(pro, name)
        raise AttributeError(name)
