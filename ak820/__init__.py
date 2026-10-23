"""ak820 — modular Ajazz AK820 Pro Linux driver package (Phase 2).

v0.7.0 ships the testability seam: `protocol` (pure framing), `transport`
(the only fd/ioctl/sysfs module), `device` (fd-free state machine) and
`errors` (exit-code taxonomy). `screen`/`state`/`cli` land in v0.8.0/v0.9.0.
"""

__version__ = "0.7.0"

from .device import AK820Pro, stream_payload
from .errors import (
    AK820Error,
    DeviceDisconnectedError,
    PermissionDeniedError,
    ProtocolError,
    UploadIncompleteError,
)
from .protocol import (
    ACK_MAGIC,
    CHUNK_ACK_TIMEOUT_MS,
    CHUNK_SIZE,
    CMD_DELAY,
    HIDIOCGFEATURE_65,
    HIDIOCSFEATURE_65,
    IMG_CFG_SETUP_DELAY,
    LIGHT_MODES,
    MAX_FRAMES,
    MAX_LIGHT_MODE,
    PIDS,
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    USAGE_PAGE_CONTROL,
    USAGE_PAGE_DATA,
    VID,
    _build_gif_header,
    _cmd_packet,
    build_payload,
    parse_hex_color,
    parse_mode,
    parse_usage_page,
)
from .transport import (
    HidTransport,
    LinuxHidrawTransport,
    find_hidraw_devices,
    find_keyboard_hidraw,
)

__all__ = [
    "__version__",
    "AK820Error",
    "AK820Pro",
    "ACK_MAGIC",
    "CHUNK_ACK_TIMEOUT_MS",
    "CHUNK_SIZE",
    "CMD_DELAY",
    "DeviceDisconnectedError",
    "HIDIOCGFEATURE_65",
    "HIDIOCSFEATURE_65",
    "HidTransport",
    "IMG_CFG_SETUP_DELAY",
    "LIGHT_MODES",
    "LinuxHidrawTransport",
    "MAX_FRAMES",
    "MAX_LIGHT_MODE",
    "PIDS",
    "PermissionDeniedError",
    "ProtocolError",
    "SCREEN_HEIGHT",
    "SCREEN_WIDTH",
    "USAGE_PAGE_CONTROL",
    "USAGE_PAGE_DATA",
    "UploadIncompleteError",
    "VID",
    "_build_gif_header",
    "_cmd_packet",
    "build_payload",
    "find_hidraw_devices",
    "find_keyboard_hidraw",
    "parse_hex_color",
    "parse_mode",
    "parse_usage_page",
    "stream_payload",
]
