"""ak820 — modular Ajazz AK820 Pro Linux driver package (Phase 3 complete).

v0.7.0 shipped the testability seam: `protocol` (pure framing), `transport`
(the only fd/ioctl/sysfs module), `device` (fd-free state machine) and
`errors` (exit-code taxonomy). v0.8.0 adds `screen` (RGB565 pipeline +
render_common) and `state` (payload dedup persistence). v0.9.0 completes the
split: `actions` (upload/RTC/radar frames), `backlight` + `status` (lighting),
`config` (device-free events/preview handlers), and `cli` (argparse tree +
HANDLERS dispatch + §3.4 exit-code main()). `ak820_ctrl.py` is a pure shim.
"""

__version__ = "0.41.0"  # kept in lockstep with pyproject [project] version

from .actions import (
    build_radar_frames,
    sync_clock,
    upload_frames_to_screen,
    upload_to_screen,
)
from .backlight import (
    SHOWCASE_PRESETS,
    apply_idle_backlight,
    dispatch_backlight,
    get_idle_backlight_cfg,
    list_backlight_presets,
    save_idle_backlight_cfg,
    set_backlight,
)
from .cli import build_parser, main, run_watch_loop
from .device import AK820Pro, stream_payload
from .discovery import (  # v0.19.0: sysfs discovery split out of transport (§18)
    find_hidraw_devices,
    find_keyboard_hidraw,
)

# --- v0.9.0: command layer (imports last: cli pulls in the whole stack) -------
from .errors import (
    AK820Error,
    ConfigError,  # v0.25.0: events-config error joined the taxonomy (§10)
    DeviceDisconnectedError,
    DeviceNotFoundError,
    FileMissingError,
    FontMissingError,  # v0.38.0: no usable catalog font (exit 10)
    InvalidFileError,
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
    MAX_BRIGHTNESS,
    MAX_DIRECTION,
    MAX_FRAMES,
    MAX_LIGHT_MODE,
    MAX_SPEED,
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
from .status import run_backlight_demo, set_status_backlight
from .transport import (
    AK820ProDevice,
    HidTransport,
    LinuxHidrawTransport,
    wait_for_keypress,
)

__all__ = [
    "ACK_MAGIC",
    "CHUNK_ACK_TIMEOUT_MS",
    "CHUNK_SIZE",
    "CMD_DELAY",
    "DEFAULT_STATE_PATH",
    "FONT_BOLD_PATHS",
    "FONT_MONO_PATHS",
    "FONT_NARROW_PATHS",
    "FONT_REGULAR_PATHS",
    "HIDIOCGFEATURE_65",
    "HIDIOCSFEATURE_65",
    "IMG_CFG_SETUP_DELAY",
    "LIGHT_MODES",
    "MAX_BRIGHTNESS",
    "MAX_DIRECTION",
    "MAX_FRAMES",
    "MAX_LIGHT_MODE",
    "MAX_SPEED",
    "PIDS",
    "RGB565_FRAME_BYTES",
    "SCREEN_H",
    "SCREEN_HEIGHT",
    "SCREEN_W",
    "SCREEN_WIDTH",
    "SHOWCASE_PRESETS",
    "USAGE_PAGE_CONTROL",
    "USAGE_PAGE_DATA",
    "VID",
    "AK820Error",
    "AK820Pro",
    "AK820ProDevice",
    "ConfigError",
    "DeviceDisconnectedError",
    "DeviceNotFoundError",
    "FileMissingError",
    "FontMissingError",
    "HidTransport",
    "InvalidFileError",
    "LinuxHidrawTransport",
    "PermissionDeniedError",
    "ProtocolError",
    "UploadIncompleteError",
    "__version__",
    "_build_gif_header",
    "_cmd_packet",
    "apply_idle_backlight",
    "build_parser",
    "build_payload",
    "build_radar_frames",
    "check_and_record_payload_hash",
    "dispatch_backlight",
    "find_hidraw_devices",
    "find_keyboard_hidraw",
    "fit_image",
    "get_idle_backlight_cfg",
    "get_state_path",
    "image_to_rgb565",
    "list_backlight_presets",
    "load_frames_from_file",
    "main",
    "parse_hex_color",
    "parse_mode",
    "parse_usage_page",
    "run_backlight_demo",
    "run_watch_loop",
    "save_idle_backlight_cfg",
    "save_payload_hash",
    "set_backlight",
    "set_status_backlight",
    "stream_payload",
    "sync_clock",
    "upload_frames_to_screen",
    "upload_to_screen",
    "wait_for_keypress",
]
