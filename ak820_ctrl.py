#!/usr/bin/env python3
"""
Ajazz AK820 Pro Linux Screen & System Controller

Controls the 0.85" 128x128 TFT color display, static images, animated GIFs,
and RTC clock synchronization over USB HID on Linux without proprietary drivers.

Verified on Ubuntu Linux with Ajazz AK820 Pro (VID 0x0C45, PID 0x800A / 0x8009).
Protocol reverse-engineered from the official Windows DeviceDriver.exe (Ghidra + Wireshark):
  - Control pipe (Interface 3, Usage Page 0xFF13): 64-byte HID Feature Reports (Report ID 0x00)
  - Data pipe (Interface 2, Usage Page 0xFF68): 4096-byte HID Output Reports (prepended with 0x00
    to prevent Linux hidraw from stripping leading zero data bytes) + 64-byte Input ACK read-back
  - Upload sequence: START (04 18) -> IMAGE_CFG (04 72 03) -> 4096B chunks + ACK (01 5A 02) -> SAVE (04 02)

Exit codes: 0 = success, 5 = upload incomplete (device dropped chunk ACKs; nothing was flashed),
6 = invalid events config (malformed event date; nothing was saved or rendered).
"""

import argparse
import datetime
import fcntl
import os
import select
import struct
import subprocess
import sys
import time
from PIL import Image, ImageOps

VID = 0x0C45
PIDS = [0x800A, 0x8009, 0xFEFE]

USAGE_PAGE_CONTROL = 0xFF13  # Interface 3: feature report commands & clock sync
USAGE_PAGE_DATA = 0xFF68     # Interface 2: 4096-byte bulk image chunks + 64B ACK

SCREEN_WIDTH = 128
SCREEN_HEIGHT = 128
CHUNK_SIZE = 4096
# Sonix firmware GIF header stores the frame count in a single byte, so more
# than 255 frames silently wrap (& 0xFF) and the device animates garbage.
MAX_FRAMES = 255
CMD_DELAY = 0.050
IMG_CFG_SETUP_DELAY = 0.350
CHUNK_ACK_TIMEOUT_MS = 1000

# Linux hidraw ioctl constants for 65-byte buffer ([0x00 report_id] + 64-byte payload)
HIDIOCSFEATURE_65 = 0xC0000000 | (65 << 16) | (ord("H") << 8) | 0x06
HIDIOCGFEATURE_65 = 0xC0000000 | (65 << 16) | (ord("H") << 8) | 0x07


class UploadIncompleteError(Exception):
    """Raised when the device fails to ACK every 4096-byte data chunk (Defect D2).

    The upload is aborted *before* the SAVE (04 02) command so a partial payload
    is never flashed, and the payload digest is never recorded so the next run
    retries instead of believing the screen is already up to date.
    """

    exit_code = 5


def _parse_usage_page(rd_path):
    """Extract the top-level Usage Page from a HID report descriptor."""
    try:
        with open(rd_path, "rb") as f:
            desc = f.read()
    except OSError:
        return None

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


def find_hidraw_devices():
    """Locate the Data pipe (0xFF68) and Control pipe (0xFF13) for the AK820 Pro."""
    tft_dev = None
    ctrl_dev = None
    hidraw_base = "/sys/class/hidraw"
    if not os.path.isdir(hidraw_base):
        return None, None

    for entry in sorted(os.listdir(hidraw_base)):
        dev_dir = os.path.join(hidraw_base, entry, "device")
        uevent_path = os.path.join(dev_dir, "uevent")
        try:
            with open(uevent_path) as f:
                uevent = f.read()
        except OSError:
            continue

        hid_id_line = [l for l in uevent.splitlines() if l.startswith("HID_ID=")]
        if not hid_id_line:
            continue
        parts = hid_id_line[0].split("=")[1].split(":")
        if len(parts) != 3:
            continue

        dev_vid = int(parts[1], 16)
        dev_pid = int(parts[2], 16)
        if dev_vid != VID or dev_pid not in PIDS:
            continue

        usage_page = _parse_usage_page(os.path.join(dev_dir, "report_descriptor"))
        if usage_page == USAGE_PAGE_DATA:
            tft_dev = f"/dev/{entry}"
        elif usage_page == USAGE_PAGE_CONTROL:
            ctrl_dev = f"/dev/{entry}"

    return tft_dev, ctrl_dev


class AK820ProDevice:
    """Manages low-level HID communication with the Ajazz AK820 Pro."""

    def __init__(self, tft_path, ctrl_path):
        self.tft_path = tft_path
        self.ctrl_path = ctrl_path
        self.ctrl_fd = None
        self.data_fd = None

    def __enter__(self):
        if self.ctrl_path:
            self.ctrl_fd = os.open(self.ctrl_path, os.O_RDWR)
        if self.tft_path:
            self.data_fd = os.open(self.tft_path, os.O_RDWR)
        return self

    def __exit__(self, *args):
        if self.data_fd is not None:
            os.close(self.data_fd)
            self.data_fd = None
        if self.ctrl_fd is not None:
            os.close(self.ctrl_fd)
            self.ctrl_fd = None

    def send_feature(self, data64):
        """Send a 64-byte feature report with Report ID 0x00."""
        buf = bytearray(65)
        buf[0] = 0x00  # Always report ID 0x00 so kernel strips it cleanly
        buf[1:1 + min(len(data64), 64)] = data64[:64]
        fcntl.ioctl(self.ctrl_fd, HIDIOCSFEATURE_65, bytes(buf))

    def get_feature(self):
        """Read back a 64-byte feature report (handshake ACK)."""
        buf = bytearray(65)
        buf[0] = 0x00
        fcntl.ioctl(self.ctrl_fd, HIDIOCGFEATURE_65, buf)
        return bytes(buf[1:])

    def handshake(self, post_delay=CMD_DELAY):
        """Wait for MCU main loop to process SET_FEATURE, then read GET_FEATURE ACK."""
        time.sleep(CMD_DELAY)
        try:
            resp = self.get_feature()
        except OSError:
            resp = None
        time.sleep(post_delay)
        return resp

    def drain_data_acks(self):
        """Drain any pending 64-byte ACKs from the data pipe."""
        while select.select([self.data_fd], [], [], 0.01)[0]:
            try:
                os.read(self.data_fd, 64)
            except OSError:
                break

    def send_data_chunk(self, chunk4096, timeout_ms=CHUNK_ACK_TIMEOUT_MS):
        """Write a 4096-byte chunk to Interface 2 and wait for the 64-byte ACK."""
        # Prepend 0x00 so Linux hidraw strips the report ID byte and always sends 4096 bytes
        os.write(self.data_fd, b"\x00" + bytes(chunk4096))
        r, _, _ = select.select([self.data_fd], [], [], timeout_ms / 1000.0)
        if r:
            try:
                return os.read(self.data_fd, 64)
            except OSError:
                return None
        return None

    def align_data_pipe(self):
        """
        Synchronize the keyboard's 64-packet (4096B) ring index (0x2000003A) to 0
        using a two-stage (8-packet coarse + 1-packet fine) probe that stays well
        under the firmware's 2.0s watchdog while waiting for full SPI sector erases.
        """
        self.drain_data_acks()
        self.send_feature(_cmd_packet(0x18, enable=False))
        self.handshake()

        img_cfg = bytearray(64)
        img_cfg[0] = 0x04
        img_cfg[1] = 0x72
        img_cfg[2] = 0x03
        img_cfg[8] = 0x03  # 3 chunks so 2 alignment ACKs never trigger final-chunk save
        img_cfg[9] = 0x00
        self.send_feature(img_cfg)
        self.handshake(post_delay=IMG_CFG_SETUP_DELAY)
        self.drain_data_acks()

        pkt64 = b"\x00" + (b"\x00" * 64)

        # Stage 1: Coarse 8-packet bursts (max 8 bursts * 200ms = 1.6s < 2.0s watchdog)
        coarse_burst = 0
        for b in range(1, 9):
            for _ in range(8):
                os.write(self.data_fd, pkt64)
            r, _, _ = select.select([self.data_fd], [], [], 0.22)
            if r:
                os.read(self.data_fd, 64)
                coarse_burst = b
                break

        # Stage 2: Advance 56 packets (putting index in 56..63), then step 1 packet at a time
        for _ in range(56):
            os.write(self.data_fd, pkt64)
        fine_pkt = 0
        for p in range(1, 9):
            os.write(self.data_fd, pkt64)
            r, _, _ = select.select([self.data_fd], [], [], 0.22)
            if r:
                os.read(self.data_fd, 64)
                fine_pkt = p
                break

        # Stage 3: Send the 3rd chunk (exact 4096B) + SAVE (0x02) so remaining_chunks hits 0
        # and usb_data_mode transitions cleanly 5 -> 3 -> 0 while keeping sub_packet_idx at 0.
        ack3 = self.send_data_chunk(b"\x00" * CHUNK_SIZE, timeout_ms=CHUNK_ACK_TIMEOUT_MS)
        time.sleep(0.10)
        self.send_feature(_cmd_packet(0x02, enable=False))
        self.handshake(post_delay=0.10)
        self.drain_data_acks()
        acked = (coarse_burst > 0 and fine_pkt > 0 and ack3 is not None)
        return (coarse_burst, fine_pkt), acked


def _cmd_packet(cmd_byte, sub_byte=0x00, enable=True):
    pkt = bytearray(64)
    pkt[0] = 0x04
    pkt[1] = cmd_byte
    pkt[2] = sub_byte
    if enable:
        pkt[8] = 0x01
    return pkt


def image_to_rgb565(img):
    """Convert a 128x128 PIL Image to RGB565 little-endian bytes (32768 bytes)."""
    img_rgb = img.convert("RGB")
    pixels = img_rgb.load()
    data = bytearray(SCREEN_WIDTH * SCREEN_HEIGHT * 2)
    for y in range(SCREEN_HEIGHT):
        for x in range(SCREEN_WIDTH):
            r, g, b = pixels[x, y]
            rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            offset = (y * SCREEN_WIDTH + x) * 2
            data[offset] = rgb565 & 0xFF
            data[offset + 1] = (rgb565 >> 8) & 0xFF
    return bytes(data)


def fit_image(img, mode="fill"):
    """Resize/crop PIL image to 128x128 according to fit mode."""
    if mode == "stretch":
        return img.resize((SCREEN_WIDTH, SCREEN_HEIGHT), Image.Resampling.LANCZOS)
    elif mode == "contain":
        copy = img.copy()
        copy.thumbnail((SCREEN_WIDTH, SCREEN_HEIGHT), Image.Resampling.LANCZOS)
        out = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), (0, 0, 0))
        ox = (SCREEN_WIDTH - copy.width) // 2
        oy = (SCREEN_HEIGHT - copy.height) // 2
        out.paste(copy, (ox, oy))
        return out
    else:  # fill / center-crop
        return ImageOps.fit(img, (SCREEN_WIDTH, SCREEN_HEIGHT), method=Image.Resampling.LANCZOS)


def load_frames_from_file(path, fit_mode="fill", max_frames=MAX_FRAMES):
    """Load static image or multi-frame GIF into RGB565 frames and delays (ms)."""
    img = Image.open(path)
    n_frames = getattr(img, "n_frames", 1)

    if n_frames <= 1:
        fitted = fit_image(img.convert("RGB"), fit_mode)
        return [image_to_rgb565(fitted)], None

    frames = []
    delays = []
    canvas = Image.new("RGBA", img.size, (0, 0, 0, 255))

    try:
        while len(frames) < max_frames:
            previous_canvas = canvas.copy()
            frame_rgba = img.copy().convert("RGBA")
            canvas.paste(frame_rgba, (0, 0), frame_rgba)

            output = fit_image(canvas.convert("RGB"), fit_mode)
            frames.append(image_to_rgb565(output))

            delay = img.info.get("duration", 100) or 100
            delays.append(max(int(delay), 20))

            disposal = getattr(img, "disposal_method", 0)
            if disposal == 2:
                canvas = Image.new("RGBA", img.size, (0, 0, 0, 255))
            elif disposal == 3:
                canvas = previous_canvas

            img.seek(img.tell() + 1)
    except EOFError:
        pass

    return frames, delays


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


def upload_frames_to_screen(
    dev: AK820ProDevice,
    frames: list,
    delays_ms: list = None,
    label: str = "screen",
    force: bool = True,
):
    """Execute START -> IMAGE_CFG -> DATA+ACK -> SAVE for a list of RGB565 frames."""
    # Defect D3: the GIF header encodes the frame count in one byte, so >255
    # frames silently wrap (& 0xFF). Reject the upload outright *before* any
    # USB packet (alignment, START, IMAGE_CFG) reaches the device.
    if len(frames) > MAX_FRAMES:
        raise ValueError(
            f"Cannot upload {len(frames)} frames: the firmware GIF header stores "
            f"the frame count in a single byte (max {MAX_FRAMES}). "
            "Shorten the animation or reduce its loop count."
        )
    num_frames = len(frames)
    header = _build_gif_header(num_frames, delays_ms)

    payload = bytearray(header)
    for f in frames:
        payload.extend(f)

    total_chunks = (len(payload) + CHUNK_SIZE - 1) // CHUNK_SIZE
    payload.extend(b"\x00" * (total_chunks * CHUNK_SIZE - len(payload)))
    payload_bytes = bytes(payload)

    import milestone_radar as mr
    should_upload, digest = mr.check_and_record_payload_hash(payload_bytes, label, force=force)
    if not should_upload:
        print(f"Screen already displays '{label}' (sha256={digest[:10]}...). Skipping USB upload (use --force to override).")
        return False

    print("Aligning USB data pipe sub-packet counter...")
    aligned_pkts, acked = dev.align_data_pipe()
    if acked:
        print(f"  Aligned: flash ACK after {aligned_pkts} packets -> sub-packet counter locked to 0.")
    else:
        print(f"  WARN: alignment ACK not seen ({aligned_pkts} packets).")

    print(f"Uploading '{label}' ({num_frames} frame(s), {total_chunks} chunks)...")

    # Step 1: START (enable=False)
    dev.send_feature(_cmd_packet(0x18, enable=False))
    dev.handshake()

    # Step 2: IMAGE_CFG (04 72 03 ... chunk_count_le16)
    img_cfg = bytearray(64)
    img_cfg[0] = 0x04
    img_cfg[1] = 0x72
    img_cfg[2] = 0x03
    img_cfg[8] = total_chunks & 0xFF
    img_cfg[9] = (total_chunks >> 8) & 0xFF
    dev.send_feature(img_cfg)
    dev.handshake(post_delay=IMG_CFG_SETUP_DELAY)
    dev.drain_data_acks()

    # Step 3: Stream 4096-byte chunks and strictly await per-chunk ACK
    acks = 0
    for idx in range(total_chunks):
        chunk = payload_bytes[idx * CHUNK_SIZE:(idx + 1) * CHUNK_SIZE]
        ack = dev.send_data_chunk(chunk, timeout_ms=CHUNK_ACK_TIMEOUT_MS)
        if ack is not None:
            acks += 1
        pct = int((idx + 1) / total_chunks * 100)
        print(f"\r  Progress: {pct:3d}% ({idx + 1}/{total_chunks} chunks, ACKs: {acks})", end="", flush=True)
    print()

    # Defect D2: never report success on dropped ACKs. Abort *before* SAVE (04 02)
    # so a partial payload is never flashed, and never record the digest so the
    # next run retries instead of skipping the upload as "already on screen".
    if acks != total_chunks:
        raise UploadIncompleteError(
            f"Upload of '{label}' incomplete: device ACKed {acks}/{total_chunks} chunks. "
            "Flash save aborted; payload digest not recorded. Retry the upload."
        )

    # Step 4: SAVE (04 02, no FINISH 0xF0) — only reached on a fully ACKed transfer
    time.sleep(0.10)
    dev.send_feature(_cmd_packet(0x02, enable=False))
    dev.handshake(post_delay=0.10)
    dev.drain_data_acks()
    mr.save_payload_hash(digest, label)
    print("Upload and flash save complete!")
    return True


def upload_to_screen(dev: AK820ProDevice, file_path: str, fit_mode: str = "fill", force: bool = True):
    """Load an image or GIF from disk and upload it to the TFT display."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    frames, delays = load_frames_from_file(file_path, fit_mode=fit_mode)
    return upload_frames_to_screen(dev, frames, delays, label=os.path.basename(file_path), force=force)


def sync_clock(dev: AK820ProDevice):
    """Synchronize system clock to the keyboard's internal RTC."""
    now = datetime.datetime.now()
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    print(f"Synchronizing clock to {ts}...")

    # 1. START
    dev.send_feature(_cmd_packet(0x18, enable=True))
    time.sleep(CMD_DELAY)

    # 2. TIME_CFG
    dev.send_feature(_cmd_packet(0x28, enable=True))
    dev.handshake()

    # 3. TIME_DATA
    data = bytearray(64)
    data[0] = 0x00
    data[1] = 0x01
    data[2] = 0x5A
    data[3] = now.year - 2000
    data[4] = now.month
    data[5] = now.day
    data[6] = now.hour
    data[7] = now.minute
    data[8] = now.second
    data[9] = 0x00
    data[10] = 0x04
    data[62] = 0xAA
    data[63] = 0x55
    dev.send_feature(data)
    time.sleep(CMD_DELAY)

    # 4. SAVE
    dev.send_feature(_cmd_packet(0x02, enable=False))
    dev.handshake()
    print("Clock synced successfully!")


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

SHOWCASE_PRESETS = [
    {
        "key": "warm-white",
        "name": "Warm White",
        "mode": 1,
        "color": "#FFE0B2",
        "rainbow": False,
        "brightness": 4,
        "speed": 3,
        "desc": "Subtle incandescent glow, easy on the eyes for coding & reading",
    },
    {
        "key": "amber-retro",
        "name": "Amber Terminal",
        "mode": 1,
        "color": "#FF8C00",
        "rainbow": False,
        "brightness": 4,
        "speed": 3,
        "desc": "Warm retro amber terminal glow",
    },
    {
        "key": "cyber-cyan",
        "name": "Cyber Cyan",
        "mode": 1,
        "color": "#00E5FF",
        "rainbow": False,
        "brightness": 4,
        "speed": 3,
        "desc": "Crisp modern cyberpunk cyan highlight",
    },
    {
        "key": "spectrum",
        "name": "Spectrum Wave",
        "mode": 8,
        "color": "#000000",
        "rainbow": True,
        "brightness": 4,
        "speed": 3,
        "desc": "Continuous smooth full-spectrum rainbow color cycle",
    },
    {
        "key": "single-on",
        "name": "Reactive Typing",
        "mode": 2,
        "color": "#00E5FF",
        "rainbow": False,
        "brightness": 5,
        "speed": 3,
        "desc": "Stealth ambient: keys illuminate under your fingers as you type",
    },
    {
        "key": "breath-lavender",
        "name": "Breathing Lavender",
        "mode": 7,
        "color": "#BA68C8",
        "rainbow": False,
        "brightness": 4,
        "speed": 2,
        "desc": "Slow, relaxing lavender breathing pulsation",
    },
    {
        "key": "flowing",
        "name": "Flowing Stream",
        "mode": 16,
        "color": "#000000",
        "rainbow": True,
        "brightness": 4,
        "speed": 3,
        "desc": "Dynamic gradient flowing sideways across the keyboard",
    },
    {
        "key": "off",
        "name": "Stealth (Off)",
        "mode": 0,
        "color": "#000000",
        "rainbow": False,
        "brightness": 0,
        "speed": 0,
        "desc": "Zero LEDs, completely dark",
    },
]


def parse_hex_color(hex_str: str) -> tuple:
    h = hex_str.lstrip("#")
    if len(h) == 3:
        h = "".join([c * 2 for c in h])
    if len(h) != 6:
        raise ValueError(f"Invalid hex color: '{hex_str}' (expected #RRGGBB or #RGB)")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def set_backlight(
    dev: AK820ProDevice,
    mode: int = 1,
    r: int = 255,
    g: int = 255,
    b: int = 255,
    rainbow: bool = False,
    brightness: int = 4,
    speed: int = 3,
    direction: int = 0,
    save: bool = False,
):
    """Send lighting mode configuration to Interface 3."""
    # 1. START (0x18)
    dev.send_feature(_cmd_packet(0x18, enable=True))
    time.sleep(CMD_DELAY)
    dev.handshake()

    # 2. START_MODE (0x13)
    dev.send_feature(_cmd_packet(0x13, enable=True))
    dev.handshake()

    # 3. MODE_DATA
    data = bytearray(64)
    data[0] = mode & 0xFF
    data[1] = r & 0xFF
    data[2] = g & 0xFF
    data[3] = b & 0xFF
    data[8] = 1 if rainbow else 0
    data[9] = max(0, min(5, brightness))
    data[10] = max(0, min(5, speed))
    data[11] = direction & 0x03
    data[14] = 0x55
    data[15] = 0xAA
    dev.send_feature(data)
    time.sleep(CMD_DELAY)
    dev.handshake()

    # 4. FINISH (0xF0) or SAVE (0x02)
    if save:
        dev.send_feature(_cmd_packet(0x02, enable=False))
    else:
        dev.send_feature(_cmd_packet(0xF0, enable=True))
    dev.handshake()


def wait_for_keypress(timeout: float = None) -> bool:
    """Wait for user input on the AK820 Pro keyboard (keypress or knob event)."""
    dev_paths = ["/dev/hidraw0", "/dev/hidraw1"]
    fds = []
    for p in dev_paths:
        if os.path.exists(p):
            try:
                fds.append(os.open(p, os.O_RDONLY | os.O_NONBLOCK))
            except OSError:
                pass
    if not fds:
        # Fallback to stdin wait if hidraw0/1 not accessible
        r, _, _ = select.select([sys.stdin], [], [], timeout)
        return bool(r)
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
                try:
                    os.read(fd, 64)
                except OSError:
                    pass
            return True
        return False
    finally:
        for fd in fds:
            os.close(fd)


def get_idle_backlight_cfg() -> dict:
    import milestone_radar as mr
    cfg = mr.load_events_config()
    bl = cfg.get("backlight", {})
    return {
        "preset": bl.get("idle_preset", "warm-white"),
        "mode": bl.get("idle_mode", 1),
        "color": bl.get("idle_color", "#FFE0B2"),
        "rainbow": bl.get("idle_rainbow", False),
        "brightness": bl.get("idle_brightness", 4),
        "speed": bl.get("idle_speed", 3),
        "direction": bl.get("idle_direction", 0),
    }


def save_idle_backlight_cfg(
    idle_preset: str = "warm-white",
    mode: int = 1,
    color: str = "#FFE0B2",
    rainbow: bool = False,
    brightness: int = 4,
    speed: int = 3,
    direction: int = 0,
):
    import milestone_radar as mr
    cfg = mr.load_events_config()
    cfg["backlight"] = {
        "idle_preset": idle_preset,
        "idle_mode": mode,
        "idle_color": color,
        "idle_rainbow": rainbow,
        "idle_brightness": brightness,
        "idle_speed": speed,
        "idle_direction": direction,
    }
    mr.save_events_config(cfg)


def apply_idle_backlight(dev: AK820ProDevice):
    cfg = get_idle_backlight_cfg()
    preset_key = cfg.get("preset")
    preset = next((p for p in SHOWCASE_PRESETS if p["key"] == preset_key), None)
    if preset:
        r, g, b = parse_hex_color(preset["color"])
        set_backlight(dev, preset["mode"], r, g, b, preset["rainbow"], preset["brightness"], preset["speed"])
    else:
        r, g, b = parse_hex_color(cfg["color"])
        set_backlight(dev, cfg["mode"], r, g, b, cfg["rainbow"], cfg["brightness"], cfg["speed"], cfg["direction"])


def set_status_backlight(dev: AK820ProDevice, status_name: str, label: str = None, wait_key: bool = True):
    status_name = status_name.lower().strip()
    if status_name in ("in-progress", "busy", "progress", "work", "running"):
        tag = f" ({label})" if label else ""
        print(f"▶ Status: IN-PROGRESS{tag} -> Pulsing Amber backlight")
        set_backlight(dev, mode=LIGHT_MODES["breath"], r=255, g=150, b=0, rainbow=False, brightness=4, speed=3)
    elif status_name in ("success", "done", "ok", "passed"):
        tag = f" ({label})" if label else ""
        print(f"✔ Status: SUCCESS{tag} -> Emerald Green backlight")
        set_backlight(dev, mode=LIGHT_MODES["breath"], r=0, g=230, b=70, rainbow=False, brightness=5, speed=2)
        if wait_key:
            print("⏳ Awaiting your next keypress on the keyboard to restore ambient lighting...")
            wait_for_keypress()
            print("⌨ Keypress detected! Restoring ambient idle backlight...")
            apply_idle_backlight(dev)
    elif status_name in ("failed", "error", "fail", "err"):
        tag = f" ({label})" if label else ""
        print(f"✖ Status: FAILED{tag} -> Urgent Red backlight")
        set_backlight(dev, mode=LIGHT_MODES["breath"], r=255, g=20, b=20, rainbow=False, brightness=5, speed=4)
        if wait_key:
            print("⏳ Awaiting your next keypress on the keyboard to acknowledge error...")
            wait_for_keypress()
            print("⌨ Keypress detected! Restoring ambient idle backlight...")
            apply_idle_backlight(dev)
    elif status_name in ("idle", "normal", "reset", "clear"):
        print("Restoring ambient idle backlight...")
        apply_idle_backlight(dev)
    else:
        print(f"Unknown status '{status_name}'. Valid: in-progress, success, failed, idle")


def run_backlight_demo(dev: AK820ProDevice, dwell_seconds: float = 6.0):
    print("=" * 64)
    print("🌟 Ajazz AK820 Pro Ambient Backlight Showcase")
    print(f"Cycling through {len(SHOWCASE_PRESETS)} curated presets ({dwell_seconds}s each).")
    print("Watch the keyboard on your desk to pick your favorite!")
    print("Press Ctrl+C anytime to stop.")
    print("=" * 64)
    try:
        for idx, p in enumerate(SHOWCASE_PRESETS, start=1):
            r, g, b = parse_hex_color(p["color"])
            print(f"\n[{idx}/{len(SHOWCASE_PRESETS)}] {p['name'].upper()} (`{p['key']}`)")
            print(f"   Mode: {p['mode']} | Color: {p['color']} | Brightness: {p['brightness']}")
            print(f"   Vibe: {p['desc']}")
            set_backlight(
                dev,
                mode=p["mode"],
                r=r,
                g=g,
                b=b,
                rainbow=p["rainbow"],
                brightness=p["brightness"],
                speed=p["speed"],
            )
            end_t = time.time() + dwell_seconds
            while time.time() < end_t:
                time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nDemo interrupted by user.")
    print("\n" + "=" * 64)
    print("Done! To set and save your favorite preset as your default idle backlight:")
    print("   python3 ak820_ctrl.py backlight set --preset <key> --save")
    print("Example: python3 ak820_ctrl.py backlight set --preset warm-white --save")
    print("=" * 64)


def build_radar_frames(view: str, upcoming: list, today: datetime.date, slide_seconds: float = 5.0):
    """
    Build RGB565 frames and frame delays (ms) for the requested radar view.
    In SN32F290 firmware (0x16304), each frame's delay byte (max 255) is counted
    in 1ms SysTick ticks (~300ms total per frame including SPI->LCD blit).
    Repeating a static slide N times holds it steady on screen for N * ~0.30s.
    """
    import milestone_radar as mr

    hero_rgb = image_to_rgb565(mr.render_hero_card(upcoming, today=today))
    board_rgb = image_to_rgb565(mr.render_board_card(upcoming, today=today))
    frames_per_slide = max(1, min(60, int(round(slide_seconds / 0.30))))

    if view == "hero":
        return [hero_rgb], None
    elif view == "board":
        return [board_rgb], None
    elif view == "hybrid":
        bongo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "presets", "bongo_cat.gif")
        frames, delays = [], []
        if os.path.exists(bongo_path):
            b_frames, b_delays = load_frames_from_file(bongo_path, fit_mode="fill")
            # 4 loops of Bongo Cat (~4 seconds)
            for _ in range(4):
                frames.extend(b_frames)
                delays.extend(b_delays or [125] * len(b_frames))
        frames.extend([hero_rgb] * frames_per_slide + [board_rgb] * frames_per_slide)
        delays.extend([1275] * (frames_per_slide * 2))
        # Defect D3: clamp the hybrid total (Bongo loops + event slides) so the
        # frame count can never silently wrap & 0xFF in the GIF header.
        if len(frames) > MAX_FRAMES:
            frames = frames[:MAX_FRAMES]
            delays = delays[:MAX_FRAMES]
        return frames, delays
    else:  # "cycle": Hero slide (~5s) + Board slide (~5s)
        frames = [hero_rgb] * frames_per_slide + [board_rgb] * frames_per_slide
        delays = [1275] * len(frames)
        return frames, delays


def _main_impl():
    import json
    import milestone_radar as mr

    parser = argparse.ArgumentParser(description="Ajazz AK820 Pro Linux Screen, Clock & Milestone Radar Controller")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. upload
    img_parser = subparsers.add_parser("upload", help="Upload image or animated GIF to TFT display")
    img_parser.add_argument("file", help="Path to PNG, JPG, BMP, or GIF file")
    img_parser.add_argument("--fit", choices=["fill", "contain", "stretch"], default="fill", help="Image fit mode")
    img_parser.add_argument("--force", action="store_true", help="Upload even if identical payload is already active")

    # 2. radar (Family Milestone Radar)
    radar_parser = subparsers.add_parser("radar", help="Render and upload Family Milestone Radar to the screen")
    radar_parser.add_argument(
        "--view",
        choices=["cycle", "hero", "board", "hybrid"],
        default="cycle",
        help="cycle=Hero+Board loop (default), hero=closest event only, board=3-row list, hybrid=Bongo Cat+Radar",
    )
    radar_parser.add_argument(
        "--seconds",
        type=float,
        default=5.0,
        help="Dwell time in seconds per slide in cycle/hybrid mode (default: 5.0)",
    )
    radar_parser.add_argument("--date", help="Override today's date (YYYY-MM-DD) for testing/preview")
    radar_parser.add_argument("--save-preview", help="Save rendered PNG preview(s) to directory instead of uploading")
    radar_parser.add_argument("--config", help="Path to events JSON config (default: ~/.config/ak820/events.json or $AK820_EVENTS_PATH)")
    radar_parser.add_argument("--sync-clock", action="store_true", help="Also synchronize the keyboard RTC clock")
    radar_parser.add_argument("--force", action="store_true", help="Upload even if identical payload is already on screen")

    # 3. card (AI-ready custom sticky note / status card)
    card_parser = subparsers.add_parser("card", help="Render and upload a custom AI/status card to the screen")
    card_parser.add_argument("--title", required=True, help="Top header banner text (e.g. REMINDER, KODI, AI)")
    card_parser.add_argument("--value", required=True, help="Big center headline text (e.g. 27 DAYS, READY, DONE)")
    card_parser.add_argument("--sub", default="", help="Subtitle line below center box")
    card_parser.add_argument("--footer", default="", help="Bottom footer ticker line")
    card_parser.add_argument(
        "--theme",
        choices=["soon", "warn", "urgent", "today", "calm", "green"],
        default="soon",
        help="Color theme (soon=cyan, warn=gold, urgent=red, today=magenta/gold, calm=blue, green=emerald)",
    )
    card_parser.add_argument("--force", action="store_true", help="Upload even if identical payload is already on screen")

    # 4. hud / stats (Live PC Hardware HUD)
    hud_parser = subparsers.add_parser("hud", aliases=["stats"], help="Render and upload live PC system stats HUD (CPU, RAM, Temp, Uptime)")
    hud_parser.add_argument("--save-preview", help="Save rendered PNG preview to file instead of uploading")
    hud_parser.add_argument("--force", action="store_true", help="Upload even if identical payload is already on screen")
    hud_parser.add_argument("--watch", type=float, default=0, help="Continuously refresh every N seconds (e.g. --watch 15)")

    # 5. media (Now Playing Media Card)
    media_parser = subparsers.add_parser("media", help="Render and upload Now Playing music/media card (Spotify, browser, VLC)")
    media_parser.add_argument("--save-preview", help="Save rendered PNG preview to file instead of uploading")
    media_parser.add_argument("--force", action="store_true", help="Upload even if identical payload is already on screen")
    media_parser.add_argument("--watch", type=float, default=0, help="Continuously refresh every N seconds (e.g. --watch 10)")

    # 6. events (Manage milestones configuration)
    ev_parser = subparsers.add_parser("events", help="List, add, or remove family milestones")
    ev_parser.add_argument("--config", help="Path to events JSON config (default: ~/.config/ak820/events.json or $AK820_EVENTS_PATH)")
    ev_sub = ev_parser.add_subparsers(dest="events_action", required=False)
    ev_list = ev_sub.add_parser("list", help="List upcoming milestones sorted by days left")
    ev_list.add_argument("--json", action="store_true", help="Output JSON for AI tools")

    ev_add = ev_sub.add_parser("add", help="Add or update a milestone in ak820_events.json")
    ev_add.add_argument("--title", required=True, help="Full event title (e.g. \"Daughter's Birthday\")")
    ev_add.add_argument("--short", required=True, help="Short label for 128x128 screen (max ~10 chars, e.g. DAUGHTER)")
    ev_add.add_argument("--date", required=True, help="Date in YYYY-MM-DD or MM-DD format")
    ev_add.add_argument("--category", choices=["birthday", "anniversary", "milestone"], default="milestone")

    ev_rm = ev_sub.add_parser("remove", help="Remove a milestone by id or title")
    ev_rm.add_argument("target", help="Event id or title to remove")

    # 5. sync-clock & probe
    subparsers.add_parser("sync-clock", help="Sync system date & time to keyboard RTC")
    subparsers.add_parser("probe", help="Detect keyboard HID interfaces")

    # 6. backlight
    bl_parser = subparsers.add_parser("backlight", help="Control RGB keyboard backlighting (modes, colors, demo)")
    bl_sub = bl_parser.add_subparsers(dest="bl_action", required=False)

    bl_demo = bl_sub.add_parser("demo", help="Showcase curated ambient lighting presets live on your keyboard")
    bl_demo.add_argument("--dwell", type=float, default=6.0, help="Seconds to display each preset (default: 6.0)")

    bl_sub.add_parser("list", help="List all lighting modes and showcase presets")

    bl_set = bl_sub.add_parser("set", help="Set lighting mode, color, brightness or preset")
    bl_set.add_argument("--preset", choices=[p["key"] for p in SHOWCASE_PRESETS], help="Apply a named showcase preset")
    bl_set.add_argument("--mode", help="Lighting mode name or integer 0..19 (e.g. static, breath, spectrum)")
    bl_set.add_argument("--color", default="#FFE0B2", help="Hex color (e.g. #FF8C00, #00E5FF, #FFFFFF)")
    bl_set.add_argument("--rainbow", action="store_true", help="Enable multi-color rainbow mode")
    bl_set.add_argument("--brightness", type=int, default=4, choices=range(0, 6), help="Brightness level 0..5 (default: 4)")
    bl_set.add_argument("--speed", type=int, default=3, choices=range(0, 6), help="Animation speed 0..5 (default: 3)")
    bl_set.add_argument("--direction", type=int, default=0, choices=[0, 1, 2, 3], help="Animation direction (0=Left, 1=Up, 2=Down, 3=Right)")
    bl_set.add_argument("--save", action="store_true", help="Save as default idle ambient preset in events config & device flash")

    bl_off = bl_sub.add_parser("off", help="Turn keyboard backlighting off")
    bl_off.add_argument("--save", action="store_true", help="Save off state as default in device flash")

    # 7. status
    status_parser = subparsers.add_parser("status", help="Set workflow status lighting (in-progress, success, failed, idle)")
    status_parser.add_argument("state", choices=["in-progress", "success", "failed", "idle"], help="Target workflow status state")
    status_parser.add_argument("--label", default=None, help="Optional text description of task")
    status_parser.add_argument("--no-wait", action="store_true", help="Do not wait for keypress to restore idle (exit immediately)")

    # 8. run
    run_parser = subparsers.add_parser("run", help="Wrap a shell command with live status lighting (in-progress -> success/failed -> keypress wait)")
    run_parser.add_argument("--label", default=None, help="Optional text label for command")
    run_parser.add_argument("--no-wait", action="store_true", help="Do not wait for keypress on completion")
    run_parser.add_argument("cmd", nargs=argparse.REMAINDER, help="Command to execute (e.g. -- make, -- ./build.sh)")

    args = parser.parse_args()

    # Handle offline 'events' subcommands without requiring USB hidraw access
    if args.command == "events":
        cfg_path = getattr(args, "config", None)
        cfg = mr.load_events_config(cfg_path)
        action = args.events_action or "list"
        if action == "list":
            upcoming = mr.compute_upcoming_events(cfg)
            if getattr(args, "json", False):
                for u in upcoming:
                    u["next_date"] = u["next_date"].isoformat()
                print(json.dumps(upcoming, indent=2))
            else:
                print(f"{'EVENT':<22} {'NEXT DATE':<12} {'LEFT':<8} {'MILESTONE':<12} {'URGENCY'}")
                print("-" * 68)
                for u in upcoming:
                    print(f"{u['title']:<22} {str(u['next_date']):<12} {str(u['days_left']) + 'd':<8} {u['milestone_str']:<12} {u['theme_key']}")
            return
        elif action == "add":
            mr.parse_event_date(args.date)  # D4: fail fast at the CLI, never store a bad date
            ev_id = args.short.lower().replace(" ", "-")
            events = [e for e in cfg.get("events", []) if e.get("id") != ev_id and e.get("title") != args.title]
            events.append({
                "id": ev_id,
                "title": args.title,
                "short_title": args.short.upper(),
                "date": args.date,
                "category": args.category,
            })
            cfg["events"] = events
            mr.save_events_config(cfg, cfg_path)
            target_path = cfg_path or mr.get_default_events_path()
            print(f"Saved event '{args.title}' ({args.date}) to {target_path}")
            return
        elif action == "remove":
            before = len(cfg.get("events", []))
            cfg["events"] = [
                e for e in cfg.get("events", [])
                if e.get("id") != args.target and e.get("title", "").lower() != args.target.lower()
            ]
            mr.save_events_config(cfg, cfg_path)
            print(f"Removed {before - len(cfg['events'])} event(s).")
            return

    today = datetime.date.fromisoformat(args.date) if getattr(args, "date", None) else datetime.date.today()

    if args.command == "radar" and args.save_preview:
        cfg_path = getattr(args, "config", None)
        cfg = mr.load_events_config(cfg_path)
        upcoming = mr.compute_upcoming_events(cfg, today=today)
        os.makedirs(args.save_preview, exist_ok=True)
        p1 = os.path.join(args.save_preview, "radar_hero.png")
        p2 = os.path.join(args.save_preview, "radar_board.png")
        mr.render_hero_card(upcoming, today=today).save(p1)
        mr.render_board_card(upcoming, today=today).save(p2)
        print(f"Saved previews to {p1} and {p2}")
        return

    if args.command in ("hud", "stats") and getattr(args, "save_preview", None):
        import widgets
        img = widgets.render_hud_card()
        img.save(args.save_preview)
        print(f"Saved HUD preview to {args.save_preview}")
        return

    if args.command == "media" and getattr(args, "save_preview", None):
        import widgets
        img = widgets.render_media_card()
        img.save(args.save_preview)
        print(f"Saved media preview to {args.save_preview}")
        return

    tft_dev, ctrl_dev = find_hidraw_devices()

    if args.command == "probe":
        print(f"TFT Data Interface (0xFF68):    {tft_dev or 'NOT FOUND'}")
        print(f"Control/RTC Interface (0xFF13): {ctrl_dev or 'NOT FOUND'}")
        return

    need_tft = args.command in ("upload", "radar", "card", "hud", "stats", "media")
    if not ctrl_dev or (need_tft and not tft_dev):
        sys.exit("Error: AK820 Pro USB HID interfaces not found. Ensure the keyboard is connected via USB cable.")

    with AK820ProDevice(tft_dev, ctrl_dev) as dev:
        if args.command == "upload":
            upload_to_screen(dev, args.file, args.fit, force=args.force)
        elif args.command == "sync-clock":
            sync_clock(dev)
        elif args.command == "radar":
            if args.sync_clock:
                sync_clock(dev)
            cfg = mr.load_events_config(getattr(args, "config", None))
            upcoming = mr.compute_upcoming_events(cfg, today=today)
            frames, delays = build_radar_frames(args.view, upcoming, today, slide_seconds=args.seconds)
            label = f"radar:{args.view}:{args.seconds}s:{today.isoformat()}"
            upload_frames_to_screen(dev, frames, delays, label=label, force=args.force)
        elif args.command == "card":
            img = mr.render_custom_card(args.title, args.value, args.sub, args.footer, args.theme)
            label = f"card:{args.title}:{args.value}:{args.sub}:{args.footer}:{args.theme}"
            upload_frames_to_screen(dev, [image_to_rgb565(img)], None, label=label, force=args.force)
        elif args.command in ("hud", "stats"):
            import widgets
            def push_hud():
                stats = widgets.get_system_stats()
                img = widgets.render_hud_card(stats)
                label = f"hud:{stats['cpu_pct']}:{stats['ram_pct']}:{stats['temp_c']}:{stats['time']}"
                upload_frames_to_screen(dev, [image_to_rgb565(img)], None, label=label, force=args.force)

            push_hud()
            if getattr(args, "watch", 0) > 0:
                print(f"Watching system HUD (refreshing every {args.watch}s, Ctrl+C to stop)...")
                try:
                    while True:
                        time.sleep(args.watch)
                        push_hud()
                except KeyboardInterrupt:
                    print("\nStopped.")
        elif args.command == "media":
            import widgets
            def push_media():
                media = widgets.get_media_status()
                img = widgets.render_media_card(media)
                label = f"media:{media['status']}:{media['title']}:{media['artist']}"
                upload_frames_to_screen(dev, [image_to_rgb565(img)], None, label=label, force=args.force)

            push_media()
            if getattr(args, "watch", 0) > 0:
                print(f"Watching media player (refreshing every {args.watch}s, Ctrl+C to stop)...")
                try:
                    while True:
                        time.sleep(args.watch)
                        push_media()
                except KeyboardInterrupt:
                    print("\nStopped.")
        elif args.command == "backlight":
            action = args.bl_action or "set"
            if action == "demo":
                run_backlight_demo(dev, dwell_seconds=args.dwell)
            elif action == "list":
                print(f"{'PRESET KEY':<18} {'NAME':<20} {'MODE':<6} {'COLOR':<10} {'DESCRIPTION'}")
                print("-" * 80)
                for p in SHOWCASE_PRESETS:
                    print(f"{p['key']:<18} {p['name']:<20} {str(p['mode']):<6} {p['color']:<10} {p['desc']}")
                print("\nSupported mode names:", ", ".join(LIGHT_MODES.keys()))
            elif action == "off":
                set_backlight(dev, mode=0, brightness=0, save=args.save)
                if args.save:
                    save_idle_backlight_cfg(idle_preset="off", mode=0, brightness=0)
                print("Backlight turned OFF.")
            elif action == "set":
                if args.preset:
                    p = next(x for x in SHOWCASE_PRESETS if x["key"] == args.preset)
                    r, g, b = parse_hex_color(p["color"])
                    set_backlight(
                        dev,
                        mode=p["mode"],
                        r=r,
                        g=g,
                        b=b,
                        rainbow=p["rainbow"],
                        brightness=p["brightness"],
                        speed=p["speed"],
                        save=args.save,
                    )
                    if args.save:
                        save_idle_backlight_cfg(
                            idle_preset=p["key"],
                            mode=p["mode"],
                            color=p["color"],
                            rainbow=p["rainbow"],
                            brightness=p["brightness"],
                            speed=p["speed"],
                        )
                    print(f"Applied preset '{p['name']}' (`{p['key']}`).")
                else:
                    try:
                        mode_val = parse_mode(args.mode) if args.mode else 1
                    except ValueError as exc:
                        sys.exit(f"Error: {exc}")
                    r, g, b = parse_hex_color(args.color)
                    set_backlight(
                        dev,
                        mode=mode_val,
                        r=r,
                        g=g,
                        b=b,
                        rainbow=args.rainbow,
                        brightness=args.brightness,
                        speed=args.speed,
                        direction=args.direction,
                        save=args.save,
                    )
                    if args.save:
                        save_idle_backlight_cfg(
                            idle_preset="custom",
                            mode=mode_val,
                            color=args.color,
                            rainbow=args.rainbow,
                            brightness=args.brightness,
                            speed=args.speed,
                            direction=args.direction,
                        )
                    print(f"Applied lighting (mode={mode_val}, color={args.color}, brightness={args.brightness}).")
        elif args.command == "status":
            set_status_backlight(dev, args.state, label=args.label, wait_key=not args.no_wait)
        elif args.command == "run":
            cmd = args.cmd
            if cmd and cmd[0] == "--":
                cmd = cmd[1:]
            if not cmd:
                sys.exit("Error: No command specified to run.")
            set_status_backlight(dev, "in-progress", label=args.label)
            res = subprocess.run(cmd)
            if res.returncode == 0:
                set_status_backlight(dev, "success", label=args.label, wait_key=not args.no_wait)
            else:
                set_status_backlight(dev, "failed", label=args.label, wait_key=not args.no_wait)
                sys.exit(res.returncode)


def main():
    """Console entry point (ak820 / ak820-ctrl scripts).

    Maps UploadIncompleteError (Defect D2) to exit code 5 and ConfigError
    (Defects D4/D5, invalid event dates) to exit code 6 so scripts and
    systemd units get a friendly message instead of a raw traceback.
    """
    import milestone_radar as mr

    try:
        _main_impl()
    except UploadIncompleteError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(UploadIncompleteError.exit_code)
    except mr.ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(mr.ConfigError.exit_code)


if __name__ == "__main__":
    main()

