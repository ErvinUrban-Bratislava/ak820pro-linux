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
"""

import argparse
import datetime
import fcntl
import os
import select
import struct
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
CMD_DELAY = 0.050
IMG_CFG_SETUP_DELAY = 0.350
CHUNK_ACK_TIMEOUT_MS = 1000

# Linux hidraw ioctl constants for 65-byte buffer ([0x00 report_id] + 64-byte payload)
HIDIOCSFEATURE_65 = 0xC0000000 | (65 << 16) | (ord("H") << 8) | 0x06
HIDIOCGFEATURE_65 = 0xC0000000 | (65 << 16) | (ord("H") << 8) | 0x07


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


def load_frames_from_file(path, fit_mode="fill", max_frames=255):
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


def upload_to_screen(dev: AK820ProDevice, file_path: str, fit_mode: str = "fill"):
    """Execute the full START -> IMAGE_CFG -> DATA+ACK -> SAVE transaction."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    frames, delays = load_frames_from_file(file_path, fit_mode=fit_mode)
    num_frames = len(frames)
    header = _build_gif_header(num_frames, delays)

    payload = bytearray(header)
    for f in frames:
        payload.extend(f)

    total_chunks = (len(payload) + CHUNK_SIZE - 1) // CHUNK_SIZE
    payload.extend(b"\x00" * (total_chunks * CHUNK_SIZE - len(payload)))

    print("Aligning USB data pipe sub-packet counter...")
    aligned_pkts, acked = dev.align_data_pipe()
    if acked:
        print(f"  Aligned: flash ACK after {aligned_pkts} packets -> sub-packet counter locked to 0.")
    else:
        print(f"  WARN: alignment ACK not seen ({aligned_pkts} packets).")

    print(f"Uploading '{os.path.basename(file_path)}' ({num_frames} frame(s), {total_chunks} chunks)...")

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
        chunk = payload[idx * CHUNK_SIZE:(idx + 1) * CHUNK_SIZE]
        ack = dev.send_data_chunk(chunk, timeout_ms=CHUNK_ACK_TIMEOUT_MS)
        if ack is not None:
            acks += 1
        pct = int((idx + 1) / total_chunks * 100)
        print(f"\r  Progress: {pct:3d}% ({idx + 1}/{total_chunks} chunks, ACKs: {acks})", end="", flush=True)
    print()

    # Step 4: SAVE (04 02, no FINISH 0xF0)
    time.sleep(0.10)
    dev.send_feature(_cmd_packet(0x02, enable=False))
    dev.handshake(post_delay=0.10)
    dev.drain_data_acks()
    print("Upload and flash save complete!")


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


def main():
    parser = argparse.ArgumentParser(description="Ajazz AK820 Pro Linux Screen & Clock Controller")
    subparsers = parser.add_subparsers(dest="command", required=True)

    img_parser = subparsers.add_parser("upload", help="Upload image or animated GIF to TFT display")
    img_parser.add_argument("file", help="Path to PNG, JPG, BMP, or GIF file")
    img_parser.add_argument("--fit", choices=["fill", "contain", "stretch"], default="fill", help="Image fit mode")

    subparsers.add_parser("sync-clock", help="Sync system date & time to keyboard RTC")
    subparsers.add_parser("probe", help="Detect keyboard HID interfaces")

    args = parser.parse_args()
    tft_dev, ctrl_dev = find_hidraw_devices()

    if args.command == "probe":
        print(f"TFT Data Interface (0xFF68):    {tft_dev or 'NOT FOUND'}")
        print(f"Control/RTC Interface (0xFF13): {ctrl_dev or 'NOT FOUND'}")
        return

    if not ctrl_dev or (args.command == "upload" and not tft_dev):
        sys.exit("Error: AK820 Pro USB HID interfaces not found. Ensure the keyboard is connected via USB cable.")

    with AK820ProDevice(tft_dev, ctrl_dev) as dev:
        if args.command == "upload":
            upload_to_screen(dev, args.file, args.fit)
        elif args.command == "sync-clock":
            sync_clock(dev)


if __name__ == "__main__":
    main()
