#!/usr/bin/env python3
"""
widgets.py — Standalone System HUD and Media Player card renderers for 128x128 TFT.
Zero external runtime dependencies beyond Pillow and Linux sysfs / D-Bus.
"""

from __future__ import annotations

import datetime
import glob
import os
import platform
import shutil
import subprocess
import time
from typing import Dict, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont

SCREEN_W = 128
SCREEN_H = 128

FONT_BOLD_PATHS = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
FONT_MONO_PATHS = [
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
]
FONT_REGULAR_PATHS = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _load_font(paths: list[str], size: int) -> ImageFont.FreeTypeFont:
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def get_system_stats() -> Dict:
    """Collect CPU %, RAM %, Temperature, Hostname, and Uptime from Linux sysfs/proc."""
    # 1. CPU %
    cpu_pct = 0.0
    try:
        def read_stat():
            with open("/proc/stat") as f:
                line = f.readline()
            parts = [float(x) for x in line.split()[1:8]]
            idle = parts[3] + parts[4]
            total = sum(parts)
            return idle, total

        i1, t1 = read_stat()
        time.sleep(0.08)
        i2, t2 = read_stat()
        dt = t2 - t1
        di = i2 - i1
        if dt > 0:
            cpu_pct = max(0.0, min(100.0, round(((dt - di) / dt) * 100.0, 1)))
    except Exception:
        try:
            cpu_pct = round(min(os.getloadavg()[0] / (os.cpu_count() or 1) * 100.0, 100.0), 1)
        except Exception:
            cpu_pct = 0.0

    # 2. RAM
    ram_pct = 0.0
    ram_used_gb = 0.0
    ram_total_gb = 0.0
    try:
        with open("/proc/meminfo") as f:
            mem = dict(l.split(":", 1) for l in f.read().splitlines() if ":" in l)
        total_kb = float(mem["MemTotal"].split()[0])
        avail_kb = float(mem.get("MemAvailable", mem.get("MemFree", "0")).split()[0])
        used_kb = total_kb - avail_kb
        ram_pct = round((used_kb / total_kb) * 100.0, 1) if total_kb > 0 else 0.0
        ram_used_gb = round(used_kb / (1024 * 1024), 1)
        ram_total_gb = round(total_kb / (1024 * 1024), 1)
    except Exception:
        pass

    # 3. Temp
    temp_c = None
    for pattern in ["/sys/class/hwmon/hwmon*/temp*_input", "/sys/class/thermal/thermal_zone*/temp"]:
        for path in sorted(glob.glob(pattern)):
            try:
                with open(path) as f:
                    v = int(f.read().strip())
                if 20000 < v < 115000:
                    temp_c = round(v / 1000.0, 1)
                    break
            except Exception:
                continue
        if temp_c is not None:
            break

    # 4. Uptime
    uptime_str = "0m"
    try:
        with open("/proc/uptime") as f:
            sec = float(f.readline().split()[0])
        days = int(sec // 86400)
        hours = int((sec % 86400) // 3600)
        mins = int((sec % 3600) // 60)
        if days > 0:
            uptime_str = f"{days}d {hours}h"
        elif hours > 0:
            uptime_str = f"{hours}h {mins}m"
        else:
            uptime_str = f"{mins}m"
    except Exception:
        pass

    hostname = platform.node().split(".")[0].upper()[:10]

    return {
        "cpu_pct": cpu_pct,
        "ram_pct": ram_pct,
        "ram_used_gb": ram_used_gb,
        "ram_total_gb": ram_total_gb,
        "temp_c": temp_c,
        "uptime": uptime_str,
        "hostname": hostname,
        "time": datetime.datetime.now().strftime("%H:%M"),
    }


def render_hud_card(stats: Optional[Dict] = None) -> Image.Image:
    """Render a 128x128 cyberpunk PC status HUD card."""
    if stats is None:
        stats = get_system_stats()

    img = Image.new("RGB", (SCREEN_W, SCREEN_H), (10, 14, 22))
    draw = ImageDraw.Draw(img)

    # Fonts
    font_mono_s = _load_font(FONT_MONO_PATHS, 9)
    font_mono_xs = _load_font(FONT_MONO_PATHS, 8)
    font_bold_s = _load_font(FONT_BOLD_PATHS, 10)
    font_bold_m = _load_font(FONT_BOLD_PATHS, 13)

    # 1. Header Bar: Hostname + Clock
    draw.rectangle([0, 0, SCREEN_W - 1, 17], fill=(16, 24, 38))
    draw.line([0, 17, SCREEN_W - 1, 17], fill=(0, 229, 255))
    draw.text((4, 3), stats["hostname"], fill=(0, 229, 255), font=font_bold_s)
    clock_str = stats.get("time", "")
    c_bbox = draw.textbbox((0, 0), clock_str, font=font_mono_s)
    draw.text((SCREEN_W - 4 - (c_bbox[2] - c_bbox[0]), 4), clock_str, fill=(240, 246, 252), font=font_mono_s)

    # 2. CPU Metric & Gauge (y: 22..46)
    cpu = stats["cpu_pct"]
    draw.text((6, 22), "CPU", fill=(148, 163, 184), font=font_mono_xs)
    cpu_str = f"{cpu:.0f}%"
    val_box = draw.textbbox((0, 0), cpu_str, font=font_bold_s)
    draw.text((SCREEN_W - 6 - (val_box[2] - val_box[0]), 21), cpu_str, fill=(56, 189, 248), font=font_bold_s)

    # CPU bar
    draw.rectangle([6, 35, SCREEN_W - 7, 40], fill=(22, 32, 48), outline=(30, 48, 72))
    fill_w = int((SCREEN_W - 14) * (min(cpu, 100.0) / 100.0))
    if fill_w > 0:
        bar_color = (239, 68, 68) if cpu >= 85 else (245, 158, 11) if cpu >= 60 else (56, 189, 248)
        draw.rectangle([7, 36, 6 + fill_w, 39], fill=bar_color)

    # 3. RAM Metric & Gauge (y: 47..72)
    ram = stats["ram_pct"]
    draw.text((6, 47), "RAM", fill=(148, 163, 184), font=font_mono_xs)
    ram_str = f"{stats['ram_used_gb']:.0f}/{stats['ram_total_gb']:.0f}G ({ram:.0f}%)"
    r_box = draw.textbbox((0, 0), ram_str, font=font_mono_xs)
    draw.text((SCREEN_W - 6 - (r_box[2] - r_box[0]), 47), ram_str, fill=(192, 132, 252), font=font_mono_xs)

    # RAM bar
    draw.rectangle([6, 59, SCREEN_W - 7, 64], fill=(22, 32, 48), outline=(30, 48, 72))
    r_fill_w = int((SCREEN_W - 14) * (min(ram, 100.0) / 100.0))
    if r_fill_w > 0:
        r_bar_color = (239, 68, 68) if ram >= 90 else (192, 132, 252)
        draw.rectangle([7, 60, 6 + r_fill_w, 63], fill=r_bar_color)

    # 4. Split Cards: TEMP (left) & UPTIME (right) (y: 71..112)
    # Left Card: Temp
    draw.rectangle([6, 71, 60, 111], fill=(16, 24, 38), outline=(30, 48, 72))
    draw.text((10, 75), "TEMP", fill=(148, 163, 184), font=font_mono_xs)
    if stats["temp_c"] is not None:
        t_val = stats["temp_c"]
        t_color = (239, 68, 68) if t_val >= 80 else (245, 158, 11) if t_val >= 65 else (52, 211, 153)
        t_str = f"{t_val:.0f}°C"
        draw.text((10, 89), t_str, fill=t_color, font=font_bold_m)
    else:
        draw.text((10, 89), "N/A", fill=(148, 163, 184), font=font_bold_m)

    # Right Card: Uptime
    draw.rectangle([66, 71, SCREEN_W - 7, 111], fill=(16, 24, 38), outline=(30, 48, 72))
    draw.text((71, 75), "UPTIME", fill=(148, 163, 184), font=font_mono_xs)
    draw.text((71, 91), stats["uptime"], fill=(251, 191, 36), font=font_bold_s)

    # 5. Bottom Frame Border
    draw.rectangle([0, 0, SCREEN_W - 1, SCREEN_H - 1], outline=(30, 48, 72))
    draw.rectangle([0, SCREEN_H - 14, SCREEN_W - 1, SCREEN_H - 1], fill=(14, 20, 32))
    draw.text((6, SCREEN_H - 11), "AJAZZ AK820 PRO", fill=(71, 85, 105), font=font_mono_xs)

    return img


def get_media_status() -> Dict:
    """Query currently playing media via playerctl or D-Bus MPRIS."""
    # 1. Try playerctl if installed
    if shutil.which("playerctl"):
        try:
            status = subprocess.check_output(
                ["playerctl", "status"], text=True, stderr=subprocess.DEVNULL, timeout=1.0
            ).strip()
            title = subprocess.check_output(
                ["playerctl", "metadata", "title"], text=True, stderr=subprocess.DEVNULL, timeout=1.0
            ).strip()
            artist = subprocess.check_output(
                ["playerctl", "metadata", "artist"], text=True, stderr=subprocess.DEVNULL, timeout=1.0
            ).strip()
            album = subprocess.check_output(
                ["playerctl", "metadata", "album"], text=True, stderr=subprocess.DEVNULL, timeout=1.0
            ).strip()
            return {
                "active": True,
                "status": status,
                "title": title or "Unknown Title",
                "artist": artist or "Unknown Artist",
                "album": album or "",
            }
        except Exception:
            pass

    # 2. Try native dbus-send
    if shutil.which("dbus-send"):
        try:
            names_raw = subprocess.check_output(
                ["dbus-send", "--session", "--dest=org.freedesktop.DBus",
                 "--type=method_call", "--print-reply", "/org/freedesktop/DBus",
                 "org.freedesktop.DBus.ListNames"],
                text=True, stderr=subprocess.DEVNULL, timeout=1.0
            )
            mpris_names = [
                line.split('"')[1] for line in names_raw.splitlines()
                if 'string "org.mpris.MediaPlayer2.' in line
            ]
            for dest in mpris_names:
                status_raw = subprocess.check_output(
                    ["dbus-send", "--session", f"--dest={dest}", "--type=method_call",
                     "--print-reply", "/org/mpris/MediaPlayer2",
                     "org.freedesktop.DBus.Properties.Get",
                     "string:org.mpris.MediaPlayer2.Player", "string:PlaybackStatus"],
                    text=True, stderr=subprocess.DEVNULL, timeout=1.0
                )
                status = "Stopped"
                if "Playing" in status_raw:
                    status = "Playing"
                elif "Paused" in status_raw:
                    status = "Paused"

                meta_raw = subprocess.check_output(
                    ["dbus-send", "--session", f"--dest={dest}", "--type=method_call",
                     "--print-reply", "/org/mpris/MediaPlayer2",
                     "org.freedesktop.DBus.Properties.Get",
                     "string:org.mpris.MediaPlayer2.Player", "string:Metadata"],
                    text=True, stderr=subprocess.DEVNULL, timeout=1.0
                )
                title = ""
                artist = ""
                album = ""
                lines = meta_raw.splitlines()
                for i, l in enumerate(lines):
                    if "xesam:title" in l and i + 1 < len(lines):
                        title = lines[i + 1].split('"')[-2] if '"' in lines[i + 1] else ""
                    elif "xesam:artist" in l and i + 2 < len(lines):
                        for j in range(i + 1, min(i + 5, len(lines))):
                            if '"' in lines[j]:
                                artist = lines[j].split('"')[-2]
                                break
                    elif "xesam:album" in l and i + 1 < len(lines):
                        album = lines[i + 1].split('"')[-2] if '"' in lines[i + 1] else ""

                if title or status in ("Playing", "Paused"):
                    return {
                        "active": True,
                        "status": status,
                        "title": title or "Media Playing",
                        "artist": artist or dest.split(".")[-1],
                        "album": album,
                    }
        except Exception:
            pass

    return {
        "active": False,
        "status": "Stopped",
        "title": "No Media Playing",
        "artist": "Launch Spotify / Browser",
        "album": "",
    }


def render_media_card(media: Optional[Dict] = None) -> Image.Image:
    """Render a 128x128 Now Playing music/media card."""
    if media is None:
        media = get_media_status()

    img = Image.new("RGB", (SCREEN_W, SCREEN_H), (12, 10, 18))
    draw = ImageDraw.Draw(img)

    font_bold_m = _load_font(FONT_BOLD_PATHS, 12)
    font_bold_s = _load_font(FONT_BOLD_PATHS, 10)
    font_mono_xs = _load_font(FONT_MONO_PATHS, 8)
    font_reg_s = _load_font(FONT_REGULAR_PATHS, 9)

    is_playing = (media.get("status") == "Playing")
    is_paused = (media.get("status") == "Paused")

    # Header Bar
    if is_playing:
        header_bg = (16, 185, 129)
        header_fg = (6, 24, 16)
        tag = "▶ PLAYING"
    elif is_paused:
        header_bg = (245, 158, 11)
        header_fg = (24, 16, 6)
        tag = "⏸ PAUSED"
    else:
        header_bg = (30, 41, 59)
        header_fg = (148, 163, 184)
        tag = "♫ MEDIA"

    draw.rectangle([0, 0, SCREEN_W - 1, 18], fill=header_bg)
    draw.text((6, 3), tag, fill=header_fg, font=font_bold_s)

    # Title box (y: 26..70)
    title = media.get("title", "No Track")
    # Wrap title if long
    if len(title) > 16:
        # 2 lines
        words = title.split()
        l1, l2 = [], []
        for w in words:
            if len(" ".join(l1 + [w])) <= 14:
                l1.append(w)
            else:
                l2.append(w)
        line1 = " ".join(l1)[:16]
        line2 = " ".join(l2)[:16]
        draw.text((8, 28), line1, fill=(255, 255, 255), font=font_bold_m)
        draw.text((8, 46), line2, fill=(255, 255, 255), font=font_bold_m)
    else:
        draw.text((8, 36), title, fill=(255, 255, 255), font=font_bold_m)

    # Artist line (y: 72..88)
    artist = media.get("artist", "")
    draw.text((8, 72), artist[:20], fill=(0, 229, 255), font=font_reg_s)

    # Album / Progress line (y: 90..106)
    album = media.get("album", "")
    if album:
        draw.text((8, 88), album[:22], fill=(148, 163, 184), font=font_mono_xs)
    else:
        draw.text((8, 88), "AK820 Pro Display", fill=(71, 85, 105), font=font_mono_xs)

    # Bottom status bar
    draw.rectangle([0, SCREEN_H - 14, SCREEN_W - 1, SCREEN_H - 1], fill=(18, 14, 28))
    draw.line([0, SCREEN_H - 14, SCREEN_W - 1, SCREEN_H - 14], fill=(45, 30, 60))
    state_label = "NOW PLAYING" if is_playing else "PAUSED" if is_paused else "IDLE"
    draw.text((8, SCREEN_H - 11), state_label, fill=(148, 163, 184), font=font_mono_xs)

    # Outer border
    draw.rectangle([0, 0, SCREEN_W - 1, SCREEN_H - 1], outline=(45, 30, 60))

    return img
