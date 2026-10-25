"""Sonix SN32F290 protocol constants and pure framing helpers (v0.7.0).

No OS-specific imports here: this module is pure Python + stdlib, so every
framing rule reverse-engineered in FINDINGS.md is unit-testable offline.
Firmware addresses are cited in comments per the FINDINGS.md convention.
"""

import struct

VID = 0x0C45
# m11 (closed v0.12.0): exactly the PIDs the udev rule grants access to —
# 0xFEFE was a reverse-engineering test fixture, never a shipping product,
# and discovery matching it could never have worked without a udev rule anyway.
PIDS = [0x800A, 0x8009]

USAGE_PAGE_CONTROL = 0xFF13  # Interface 3: feature report commands & clock sync
USAGE_PAGE_DATA = 0xFF68  # Interface 2: 4096-byte bulk image chunks + 64B ACK

SCREEN_WIDTH = 128
SCREEN_HEIGHT = 128
CHUNK_SIZE = 4096
# Sonix firmware GIF header stores the frame count in a single byte, so more
# than 255 frames silently wrap (& 0xFF) and the device animates garbage.
MAX_FRAMES = 255
CMD_DELAY = 0.050
IMG_CFG_SETUP_DELAY = 0.350
CHUNK_ACK_TIMEOUT_MS = 1000

# v0.17.0 timeout guards (§16): every bounded wait has a bound.
# A chattering data pipe must not spin drain_data_acks forever, and a device
# that ACKs just under the per-chunk timeout must not keep an upload alive
# indefinitely — the whole-transfer deadline is UPLOAD_BASE_DEADLINE_S +
# total_chunks * (CHUNK_ACK_TIMEOUT_MS/1000) * CHUNK_DEADLINE_FACTOR.
DRAIN_MAX_READS = 64
UPLOAD_BASE_DEADLINE_S = 30.0
CHUNK_DEADLINE_FACTOR = 2.0
# write-all loop: consecutive zero-progress os.write() attempts tolerated
# before the data pipe is declared stalled (ProtocolError).
MAX_WRITE_RETRIES = 64

# Per-chunk ACK on the data pipe starts with this magic (FINDINGS.md).
ACK_MAGIC = b"\x01\x5a\x02"

# Linux hidraw ioctl constants for 65-byte buffer ([0x00 report_id] + 64-byte payload)
HIDIOCSFEATURE_65 = 0xC0000000 | (65 << 16) | (ord("H") << 8) | 0x06
HIDIOCGFEATURE_65 = 0xC0000000 | (65 << 16) | (ord("H") << 8) | 0x07


def _cmd_packet(cmd_byte, sub_byte=0x00, enable=True):
    pkt = bytearray(64)
    pkt[0] = 0x04
    pkt[1] = cmd_byte
    pkt[2] = sub_byte
    if enable:
        pkt[8] = 0x01
    return pkt


def _build_gif_header(num_frames, delays_ms=None):
    """Build the 256-byte GIF header required by the Sonix firmware."""
    header = bytearray(256)
    header[0] = num_frames & 0xFF
    if delays_ms:
        for i, delay in enumerate(delays_ms):
            if 1 + i < 256:
                header[1 + i] = min(max(16, delay // 5), 255)
    else:
        header[1] = 255
    return bytes(header)


def build_payload(frames, delays_ms=None):
    """Frame-count guard (D3) + GIF header + zero padding to whole chunks.

    Returns ``(payload_bytes, num_frames, total_chunks)``. Raises ValueError
    *before any USB packet is produced* when the animation exceeds the
    firmware's single-byte frame counter (Defect D3).
    """
    if len(frames) > MAX_FRAMES:
        raise ValueError(
            f"Cannot upload {len(frames)} frames: the firmware GIF header stores "
            f"the frame count in a single byte (max {MAX_FRAMES}). "
            "Shorten the animation or reduce its loop count."
        )
    payload = bytearray(_build_gif_header(len(frames), delays_ms))
    for f in frames:
        payload.extend(f)
    total_chunks = (len(payload) + CHUNK_SIZE - 1) // CHUNK_SIZE
    payload.extend(b"\x00" * (total_chunks * CHUNK_SIZE - len(payload)))
    return bytes(payload), len(frames), total_chunks


LIGHT_MODES = {
    "off": 0,
    "static": 1,
    "single-on": 2,
    "single-off": 3,
    "glittering": 4,
    "falling": 5,
    "colourful": 6,
    "breath": 7,
    "spectrum": 8,
    "outward": 9,
    "scrolling": 10,
    "rolling": 11,
    "rotating": 12,
    "explode": 13,
    "launch": 14,
    "ripples": 15,
    "flowing": 16,
    "pulsating": 17,
    "tilt": 18,
    "shuttle": 19,
}

MAX_LIGHT_MODE = max(LIGHT_MODES.values())

# MODE_DATA byte bounds (m23, v0.30.0): brightness and speed are levels 0..5,
# direction is 0..3 (FINDINGS.md byte layout). `set_backlight` clamps/masks
# them silently (9 -> 5, 7 -> 3); `configvalues` now names out-of-range config
# values instead, and `parser.py` builds its argparse choices from these same
# constants — CLI surface and config loader can never drift (the validate_note
# single-home pattern applied to bounds).
MAX_BRIGHTNESS = 5
MAX_SPEED = 5
MAX_DIRECTION = 3


def parse_mode(value: str) -> int:
    """Resolve a lighting mode spec (name or number) to an int in 0..19.

    Mode names are matched case-insensitively; numeric values are validated
    against the firmware range 0..MAX_LIGHT_MODE. Raises ValueError with a
    friendly message for unknown names or out-of-range numbers (Defect D1:
    the old eager `int(args.mode)` default crashed on any mode name).
    """
    if value is None:
        raise ValueError("No lighting mode given.")
    text = str(value).strip()
    if not text:
        raise ValueError("No lighting mode given.")
    key = text.lower()
    if key in LIGHT_MODES:
        return LIGHT_MODES[key]
    try:
        num = int(text)
    except ValueError:
        raise ValueError(
            f"Unknown lighting mode {value!r}. Use a mode name "
            f"({', '.join(LIGHT_MODES)}) or an integer 0..{MAX_LIGHT_MODE}."
        ) from None
    if not 0 <= num <= MAX_LIGHT_MODE:
        raise ValueError(f"Lighting mode {num} is out of range 0..{MAX_LIGHT_MODE}.")
    return num


def parse_hex_color(hex_str: str) -> tuple:
    h = hex_str.lstrip("#")
    if len(h) == 3:
        h = "".join([c * 2 for c in h])
    if len(h) != 6:
        raise ValueError(f"Invalid hex color: '{hex_str}' (expected #RRGGBB or #RGB)")
    try:  # m10 root cause: "banana" is 6 chars but not hex — int('na', 16) must not leak
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        raise ValueError(f"Invalid hex color: '{hex_str}' (expected #RRGGBB or #RGB)") from None


def parse_usage_page(desc: bytes):
    """Extract the top-level Usage Page from raw HID report descriptor bytes."""
    i = 0
    while i < len(desc):
        byte = desc[i]
        size = byte & 0x03
        if size == 3:
            size = 4
        tag = byte & 0xFC
        if i + 1 + size > len(desc):
            break
        if tag == 0x04:  # Usage Page
            if size == 1:
                return desc[i + 1]
            elif size == 2:
                return desc[i + 1] | (desc[i + 2] << 8)
            elif size == 4:
                return struct.unpack_from("<I", desc, i + 1)[0]
        i += 1 + size
    return None
