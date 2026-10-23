"""AK820 error taxonomy + CLI exit-code contract (REVIEW.md §3.4).

Every driver error carries the process exit code the CLI maps it to, so
scripts can branch on `$?` without parsing stderr.
"""


class AK820Error(Exception):
    """Base class for all AK820 driver errors; carries the CLI exit code."""

    exit_code = 1


class ProtocolError(AK820Error):
    """Device answered with a malformed/unexpected reply (e.g. ACK magic
    mismatch — minor defect m3), or the data pipe will not align. The transfer
    state is unknown; abort.

    v0.12.0 (D8): code 7, not 4 — §3.4 reserves 4 for permission denied so
    scripts can branch "install the udev rule" (4) vs "protocol desync, retry"
    (7) on `$?` alone.
    """

    exit_code = 7


class DeviceDisconnectedError(AK820Error):
    """The USB device node vanished mid-operation (errno ENODEV).

    §3.4: this is a *transient* mid-op failure, distinct from DeviceNotFoundError
    (give up, 3). Exit 75 = EX_TEMPFAIL so a systemd ``Restart=on-failure``
    unit (or a cron wrapper) re-runs the command once the keyboard is re-seated.
    """

    exit_code = 75


class DeviceNotFoundError(AK820Error):
    """No AK820 Pro HID interfaces were discovered (§3.4: give up, code 3).

    Raised by the CLI dispatch when sysfs discovery finds no control pipe
    (or no data pipe for screen commands) — a friendly message replaces
    the raw `sys.exit(str)` traceback-style exit of the pre-0.9.0 CLI.
    """

    exit_code = 3


class PermissionDeniedError(AK820Error):
    """The device node rejected the operation (errno EPERM/EACCES — the udev
    rule from ``udev/`` is probably not installed).

    v0.12.0 (D8): exit 4 per §3.4 ("run setup steps"), distinct from
    DeviceNotFoundError (3 = give up) so a CI wrapper can tell "cable unplugged"
    from "udev rule missing" without parsing stderr.
    """

    exit_code = 4


class FileMissingError(AK820Error):
    """The requested input file does not exist (Defect D11, v0.15.0).

    `upload <file>` and `--dry-run upload <file>` used to leak a raw
    `FileNotFoundError` traceback. Exit 8: a user-input error that will
    never succeed on retry — distinct from transient 75 (re-seat and
    re-run) and config 6 (fix events.json) — so wrappers can branch
    "fix the path" on `$?` alone without parsing stderr.
    """

    exit_code = 8


class UploadIncompleteError(AK820Error):
    """Raised when the device fails to ACK every 4096-byte data chunk (Defect D2).

    The upload is aborted *before* the SAVE (04 02) command so a partial payload
    is never flashed, and the payload digest is never recorded so the next run
    retries instead of believing the screen is already up to date.
    """

    exit_code = 5

    def __init__(self, message, acks=None, total=None):
        super().__init__(message)
        self.acks = acks
        self.total = total
