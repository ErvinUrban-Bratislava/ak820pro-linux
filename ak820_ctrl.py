#!/usr/bin/env python3
"""
Ajazz AK820 Pro Linux Screen & System Controller

Controls the 0.85" 128x128 TFT color display, clock synchronization,
and RGB settings over USB HID on Linux without proprietary software.

Tested on Ubuntu Linux with Ajazz AK820 Pro (VID 0x0C45, PID 0x800A).
"""

import sys
import os
import time
import fcntl
import argparse
import datetime
import glob
from PIL import Image, ImageOps, ImageDraw, ImageFont

# Protocol Constants
TFT_WIDTH = 128
TFT_HEIGHT = 128
PIXELS_PER_FRAME = TFT_WIDTH * TFT_HEIGHT
FRAME_BYTES = PIXELS_PER_FRAME * 2
FRAME_HEADER_BYTES = 256
TFT_PAYLOAD_LEN = 4096
DELAY_UNIT_MS = 5
MAX_FRAMES_FOR_GIF = 30
MIN_FRAME_DELAY_MS = 40

# IOCTL codes for Linux HID feature reports
HIDIOCSFEATURE_65 = 0xc0414806
HIDIOCGFEATURE_65 = 0xc0414807

def find_hidraw_devices():
    """Locate the TFT channel (interface 2 / 0xFF68) and Control channel (interface 3 / 0xFF13)."""
    tft_dev = None
    ctrl_dev = None

    for h in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        name = os.path.basename(h)
        dev_path = f"/dev/{name}"
        desc_path = f"{h}/device/report_descriptor"
        if not os.path.exists(desc_path):
            continue
        try:
            with open(desc_path, "rb") as f:
                desc = f.read()
            # 0x06, 0x68, 0xFF = Vendor Usage Page 0xFF68 (TFT / 4096B output reports)
            if b"\x06\x68\xff" in desc and b"\x96\x00\x10" in desc:
                tft_dev = dev_path
            # 0x06, 0x13, 0xFF = Vendor Usage Page 0xFF13 (Control / Feature reports)
            if b"\x06\x13\xff" in desc:
                ctrl_dev = dev_path
        except Exception:
            continue

    return tft_dev, ctrl_dev

def quantize_rgb565(img):
    """Convert a 128x128 PIL Image (RGB) to little-endian RGB565 bytearray."""
    pixels = bytearray()
    img_rgb = img.convert("RGB")
    for y in range(TFT_HEIGHT):
        for x in range(TFT_WIDTH):
            r, g, b = img_rgb.getpixel((x, y))
            r5 = (r >> 3) & 0x1F
            g6 = (g >> 2) & 0x3F
            b5 = (b >> 3) & 0x1F
            val = (r5 << 11) | (g6 << 5) | b5
            pixels.append(val & 0xFF)
            pixels.append((val >> 8) & 0xFF)
    return pixels

def fit_image(img, mode="fill"):
    """Resize/crop PIL image to 128x128 according to fit mode."""
    if mode == "stretch":
        return img.resize((TFT_WIDTH, TFT_HEIGHT), Image.Resampling.LANCZOS)
    elif mode == "contain":
        img.thumbnail((TFT_WIDTH, TFT_HEIGHT), Image.Resampling.LANCZOS)
        out = Image.new("RGB", (TFT_WIDTH, TFT_HEIGHT), (0, 0, 0))
        offset_x = (TFT_WIDTH - img.width) // 2
        offset_y = (TFT_HEIGHT - img.height) // 2
        out.paste(img, (offset_x, offset_y))
        return out
    else:  # fill / crop
        return ImageOps.fit(img, (TFT_WIDTH, TFT_HEIGHT), method=Image.Resampling.LANCZOS)

def build_tft_header(cmd, chunk_idx, total_chunks):
    """Bespoke 8-byte chunk header decoded from the official online driver."""
    return bytearray([
        0xAA,
        cmd,
        chunk_idx & 0xFF,
        (chunk_idx >> 8) & 0xFF,
        total_chunks & 0xFF,
        (total_chunks >> 8) & 0xFF,
        0x50,
        0x06
    ])

def upload_animation(tft_dev, frames, delays):
    """
    Upload a sequence of frames and delays to the TFT display.
    frames: list of RGB565 bytearrays (each 32768 bytes)
    delays: list of delay_ms integers
    """
    n = len(frames)
    if n == 0:
        raise ValueError("Cannot upload 0 frames")
    if n > 255:
        raise ValueError("Too many frames")

    # Build 256-byte header
    hdr = bytearray([0xFF] * FRAME_HEADER_BYTES)
    hdr[0] = n
    for i in range(max(0, n - 1)):
        d_scaled = min(255, delays[i] // DELAY_UNIT_MS)
        hdr[i + 1] = d_scaled
    hdr[n] = 0x00  # terminator

    # Concatenate header + all frame pixels
    payload = hdr
    for f in frames:
        payload += f

    total_chunks = (len(payload) + TFT_PAYLOAD_LEN - 1) // TFT_PAYLOAD_LEN
    print(f"Uploading {n} frame(s) ({len(payload)} bytes) across {total_chunks} chunk(s) to {tft_dev}...")

    fd = os.open(tft_dev, os.O_RDWR)
    try:
        for i in range(total_chunks):
            start = i * TFT_PAYLOAD_LEN
            end = min(start + TFT_PAYLOAD_LEN, len(payload))
            chunk_slice = payload[start:end]

            # 4104 bytes: 8 bytes bespoke header + up to 4096 bytes chunk payload
            report = bytearray(4104)
            report[0:8] = build_tft_header(80, i, total_chunks)
            report[8:8 + len(chunk_slice)] = chunk_slice

            os.write(fd, report)
            time.sleep(0.04)
        print("Upload completed successfully!")
    finally:
        os.close(fd)

def sync_clock(ctrl_dev):
    """Sync system date & time to the keyboard's internal RTC & display."""
    print(f"Synchronizing clock on {ctrl_dev}...")
    fd = os.open(ctrl_dev, os.O_RDWR)

    def send_feature(buf64):
        report = bytearray(65)
        report[0] = 0x04
        report[1:1 + len(buf64)] = buf64
        fcntl.ioctl(fd, HIDIOCSFEATURE_65, report)
        time.sleep(0.04)

    def get_feature():
        report = bytearray(65)
        report[0] = 0x04
        try:
            fcntl.ioctl(fd, HIDIOCGFEATURE_65, report)
        except Exception:
            pass
        time.sleep(0.04)

    try:
        # Reset packet
        pkt_reset = bytearray(64)
        pkt_reset[0] = 0x18
        send_feature(pkt_reset)

        # Time configuration preamble
        pkt_conf = bytearray(64)
        pkt_conf[0] = 0x28
        pkt_conf[7] = 0x01
        send_feature(pkt_conf)
        get_feature()

        # Update time packet
        now = datetime.datetime.now()
        pkt_time = bytearray(64)
        pkt_time[0] = 0x01
        pkt_time[1] = 0x5a
        pkt_time[2] = now.year - 2000
        pkt_time[3] = now.month
        pkt_time[4] = now.day
        pkt_time[5] = now.hour
        pkt_time[6] = now.minute
        pkt_time[7] = now.second
        pkt_time[8] = 0x00
        pkt_time[9] = 0x04
        pkt_time[61] = 0xaa
        pkt_time[62] = 0x55
        send_feature(pkt_time)

        # Save packet
        pkt_save = bytearray(64)
        pkt_save[0] = 0x02
        send_feature(pkt_save)
        get_feature()

        print(f"Clock synced to: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    finally:
        os.close(fd)

def select_display_index(tft_dev, index):
    """Switch active TFT animation page."""
    print(f"Switching TFT playback index to {index}...")
    pkt = bytearray(64)
    pkt[0] = 0xAA
    pkt[1] = 81  # SET_TFT_BUILT_IN_INDEX
    pkt[2] = 1   # length
    pkt[3] = 0
    pkt[4] = 0
    pkt[6] = 1   # last
    pkt[8] = index

    fd = os.open(tft_dev, os.O_RDWR)
    try:
        os.write(fd, pkt)
        print("TFT playback index updated.")
    finally:
        os.close(fd)

def upload_file(tft_dev, file_path, fit_mode="fill"):
    """Process an image or animated GIF file and push to TFT."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    img = Image.open(file_path)
    frames = []
    delays = []

    try:
        # Check if animated
        frame_idx = 0
        while True:
            img.seek(frame_idx)
            current_frame = img.copy()
            fitted = fit_image(current_frame, fit_mode)
            frames.append(quantize_rgb565(fitted))

            d = current_frame.info.get("duration", 100)
            if not d or d < MIN_FRAME_DELAY_MS:
                d = MIN_FRAME_DELAY_MS
            delays.append(d)

            frame_idx += 1
            if frame_idx >= MAX_FRAMES_FOR_GIF:
                print(f"Note: Clamping GIF to {MAX_FRAMES_FOR_GIF} frames.")
                break
    except EOFError:
        pass

    if not frames:
        fitted = fit_image(img, fit_mode)
        frames.append(quantize_rgb565(fitted))
        delays.append(200)

    upload_animation(tft_dev, frames, delays)

def main():
    parser = argparse.ArgumentParser(description="Ajazz AK820 Pro Screen & Clock Controller for Linux")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # image upload
    img_parser = subparsers.add_parser("upload", help="Upload image or GIF to keyboard TFT display")
    img_parser.add_argument("file", help="Path to image or GIF file")
    img_parser.add_argument("--fit", choices=["fill", "contain", "stretch"], default="fill", help="Fit mode")

    # clock sync
    subparsers.add_parser("sync-clock", help="Sync system clock to keyboard screen RTC")

    # select index
    idx_parser = subparsers.add_parser("select-index", help="Select TFT display animation index")
    idx_parser.add_argument("index", type=int, help="Index number (0, 1, ...)")

    # probe
    subparsers.add_parser("probe", help="Probe keyboard HID interfaces")

    args = parser.parse_args()

    tft_dev, ctrl_dev = find_hidraw_devices()

    if args.command == "probe":
        print(f"TFT Display Interface:   {tft_dev if tft_dev else 'NOT FOUND'}")
        print(f"Control/Clock Interface: {ctrl_dev if ctrl_dev else 'NOT FOUND'}")
        return

    if not tft_dev and args.command in ["upload", "select-index"]:
        sys.exit("Error: Could not locate AK820 Pro TFT interface (0xFF68). Make sure keyboard is connected via USB cable.")

    if not ctrl_dev and args.command == "sync-clock":
        sys.exit("Error: Could not locate AK820 Pro Control interface (0xFF13). Make sure keyboard is connected via USB cable.")

    if args.command == "upload":
        upload_file(tft_dev, args.file, args.fit)
    elif args.command == "sync-clock":
        sync_clock(ctrl_dev)
    elif args.command == "select-index":
        select_display_index(tft_dev, args.index)

if __name__ == "__main__":
    main()

