#!/usr/bin/env python3
"""
Ajazz AK820 Pro Linux Screen & System Controller — COMPATIBILITY SHIM (v0.9.0).

Controls the 0.85" 128x128 TFT color display, static images, animated GIFs,
and RTC clock synchronization over USB HID on Linux without proprietary drivers.

Verified on Ubuntu Linux with Ajazz AK820 Pro (VID 0x0C45, PID 0x800A / 0x8009).
Protocol reverse-engineered from the official Windows DeviceDriver.exe (Ghidra + Wireshark):
  - Control pipe (Interface 3, Usage Page 0xFF13): 64-byte HID Feature Reports (Report ID 0x00)
  - Data pipe (Interface 2, Usage Page 0xFF68): 4096-byte HID Output Reports (prepended with 0x00
    to prevent Linux hidraw from stripping leading zero data bytes) + 64-byte Input ACK read-back
  - Upload sequence: START (04 18) -> IMAGE_CFG (04 72 03) -> 4096B chunks + ACK (01 5A 02) -> SAVE (04 02)

Exit codes: 0 = success, 3 = device not found / permission / mid-op disconnect,
5 = upload incomplete (device dropped chunk ACKs; nothing was flashed),
6 = invalid events config (malformed event date; nothing was saved or rendered).

Since v0.9.0 every implementation lives in the `ak820/` package (protocol,
transport, device, screen, state, actions, backlight, status, config, cli).
This module re-exports every public name so existing `ak820_ctrl.<name>`
imports, pipx entry points, and user scripts keep working unchanged.
"""

import os  # noqa: F401  (kept: tests patch os.open/close/read through this module)
import select  # noqa: F401  (kept: tests patch select.select through this module)

from ak820 import state  # noqa: F401
from ak820.actions import (  # noqa: F401
    build_radar_frames,
    sync_clock,
    upload_frames_to_screen,
    upload_to_screen,
)
from ak820.backlight import (  # noqa: F401
    SHOWCASE_PRESETS,
    apply_idle_backlight,
    dispatch_backlight,
    get_idle_backlight_cfg,
    list_backlight_presets,
    save_idle_backlight_cfg,
    set_backlight,
)
from ak820.cli import build_parser, main, run_watch_loop  # noqa: F401
from ak820.device import AK820Pro, stream_payload  # noqa: F401
from ak820.errors import (  # noqa: F401
    AK820Error,
    DeviceDisconnectedError,
    DeviceNotFoundError,
    FileMissingError,
    InvalidFileError,
    PermissionDeniedError,
    ProtocolError,
    UploadIncompleteError,
)
from ak820.protocol import (  # noqa: F401
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
)
from ak820.screen import (  # noqa: F401
    RGB565_FRAME_BYTES,
    fit_image,
    image_to_rgb565,
    load_frames_from_file,
)
from ak820.state import (  # noqa: F401
    check_and_record_payload_hash,
    mark_pipe,
    pipe_known_clean,
    save_payload_hash,
)
from ak820.status import run_backlight_demo, set_status_backlight  # noqa: F401
from ak820.transport import (  # noqa: F401
    AK820ProDevice,
    LinuxHidrawTransport,
    find_hidraw_devices,
    find_keyboard_hidraw,
    wait_for_keypress,
)

if __name__ == "__main__":
    main()
