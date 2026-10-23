"""ak820 — modular Ajazz AK820 Pro Linux driver package (Phase 2).

v0.7.0 shipped the testability seam: `protocol` (pure framing), `transport`
(the only fd/ioctl/sysfs module), `device` (fd-free state machine) and
`errors` (exit-code taxonomy). v0.8.0 adds `screen` (RGB565 pipeline +
render_common) and `state` (payload dedup persistence). `cli` lands in v0.9.0.
"""

__version__ = "0.8.0"

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
from .screen import (
    FONT_BOLD_PATHS,
    FONT_MONO_PATHS,
    FONT_NARROW_PATHS,
    FONT_REGULAR_PATHS,
    RGB565_FRAME_BYTES,
    SCREEN_H,
    SCREEN_W,
    fit_image,
    image_to_rgb565,
    load_frames_from_file,
)
from .state import (
    DEFAULT_STATE_PATH,
    check_and_record_payload_hash,
    get_state_path,
    save_payload_hash,
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
    "DEFAULT_STATE_PATH",
    "DeviceDisconnectedError",
    "FONT_BOLD_PATHS",
    "FONT_MONO_PATHS",
    "FONT_NARROW_PATHS",
    "FONT_REGULAR_PATHS",
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
    "RGB565_FRAME_BYTES",
    "SCREEN_H",
    "SCREEN_HEIGHT",
    "SCREEN_W",
    "SCREEN_WIDTH",
    "USAGE_PAGE_CONTROL",
    "USAGE_PAGE_DATA",
    "UploadIncompleteError",
    "VID",
    "_build_gif_header",
    "_cmd_packet",
    "build_payload",
    "check_and_record_payload_hash",
    "fit_image",
    "find_hidraw_devices",
    "find_keyboard_hidraw",
    "get_state_path",
    "image_to_rgb565",
    "load_frames_from_file",
    "parse_hex_color",
    "parse_mode",
    "parse_usage_page",
    "save_payload_hash",
    "stream_payload",
]
