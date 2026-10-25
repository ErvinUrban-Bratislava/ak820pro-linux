"""System + media telemetry probes: sysfs/proc sampling and MPRIS queries (v0.25.0).

Split out of `widgets.py` under the §10 modularity budget. This module owns the
*observation* half — reading /proc + /sys for CPU/RAM/temp/uptime and querying
playerctl/D-Bus MPRIS for the current track — and imports NO rendering code:
probing never loads PIL. The pixel half lives in `ak820.hud`.

Every probe is best-effort by contract: a missing sensor or dead player degrades
to a neutral value (0.0 / None / inactive), never an exception — the HUD timer
unit must survive a headless or sensor-less host.

`widgets.py` re-exports both functions unchanged (identity, not copy); tests
patch `ak820.telemetry.shutil` / `ak820.telemetry.subprocess` at this seam.
"""

import datetime
import glob
import os
import platform
import shutil
import subprocess
import time


def get_system_stats() -> dict:
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
    except Exception:  # noqa: BLE001 — best-effort probe contract: /proc/stat
        try:  # missing or exotic -> load-average fallback, never a crash
            cpu_pct = round(min(os.getloadavg()[0] / (os.cpu_count() or 1) * 100.0, 100.0), 1)
        except Exception:  # noqa: BLE001 — no loadavg either (non-Linux) -> 0.0
            cpu_pct = 0.0

    # 2. RAM
    ram_pct = 0.0
    ram_used_gb = 0.0
    ram_total_gb = 0.0
    try:
        with open("/proc/meminfo") as f:
            mem = dict(ln.split(":", 1) for ln in f.read().splitlines() if ":" in ln)
        total_kb = float(mem["MemTotal"].split()[0])
        avail_kb = float(mem.get("MemAvailable", mem.get("MemFree", "0")).split()[0])
        used_kb = total_kb - avail_kb
        ram_pct = round((used_kb / total_kb) * 100.0, 1) if total_kb > 0 else 0.0
        ram_used_gb = round(used_kb / (1024 * 1024), 1)
        ram_total_gb = round(total_kb / (1024 * 1024), 1)
    except Exception:  # noqa: BLE001, S110 — best-effort probe contract: absent
        pass  # or unparseable /proc/meminfo keeps the RAM gauges at 0.0

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
            except Exception:  # noqa: BLE001, S112 — best-effort probe contract:
                continue  # unreadable sensor node -> try the next hwmon entry
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
    except Exception:  # noqa: BLE001, S110 — best-effort probe contract: absent
        pass  # /proc/uptime keeps the HUD uptime cell at its "0m" default

    hostname = platform.node().split(".")[0].upper()[:10]

    return {
        "cpu_pct": cpu_pct,
        "ram_pct": ram_pct,
        "ram_used_gb": ram_used_gb,
        "ram_total_gb": ram_total_gb,
        "temp_c": temp_c,
        "uptime": uptime_str,
        "hostname": hostname,
        # Numeric-strftime exemption class (v0.39.0): %H/%M are locale-
        # independent digits — no LC_TIME spelling can move a byte here
        # (the exemption and its allow-list lock are documented in dates.py).
        "time": datetime.datetime.now().strftime("%H:%M"),
    }


def get_media_status() -> dict:
    """Query currently playing media via playerctl or D-Bus MPRIS."""
    # 1. Try playerctl if installed
    if shutil.which("playerctl"):
        try:
            # B6/§4.3: ONE templated call replaces four subprocesses. playerctl
            # prints one line per active player; the first line wins. Empty
            # output means no player is running -> fall through to dbus-send.
            raw = subprocess.check_output(
                [  # noqa: S607 — probing an optional tool on PATH is the point
                    "playerctl",
                    "-f",
                    "{{status}}\t{{title}}\t{{artist}}\t{{album}}",
                    "metadata",
                ],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=1.5,
            ).strip()
            if raw:
                status, title, artist, album = [*raw.splitlines()[0].split("\t"), "", "", "", ""][
                    :4
                ]
                return {
                    "active": True,
                    "status": status or "Stopped",
                    "title": title or "Unknown Title",
                    "artist": artist or "Unknown Artist",
                    "album": album,
                }
        except Exception:  # noqa: BLE001, S110 — best-effort probe contract: a dead
            pass  # or misbehaving playerctl falls through to the D-Bus fallback

    # 2. Try native dbus-send
    if shutil.which("dbus-send"):
        try:
            names_raw = subprocess.check_output(
                [  # noqa: S607 — optional tool on PATH (bus enumeration)
                    "dbus-send",
                    "--session",
                    "--dest=org.freedesktop.DBus",
                    "--type=method_call",
                    "--print-reply",
                    "/org/freedesktop/DBus",
                    "org.freedesktop.DBus.ListNames",
                ],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=1.0,
            )
            mpris_names = [
                line.split('"')[1]
                for line in names_raw.splitlines()
                if 'string "org.mpris.MediaPlayer2.' in line
            ]
            for dest in mpris_names:
                # S603/S607: `dest` is a bus name parsed from dbus-send's own
                # ListNames reply (not user input), and dbus-send is an optional
                # PATH tool — querying the session bus is this probe's purpose.
                status_raw = subprocess.check_output(  # noqa: S603
                    [  # noqa: S607
                        "dbus-send",
                        "--session",
                        f"--dest={dest}",
                        "--type=method_call",
                        "--print-reply",
                        "/org/mpris/MediaPlayer2",
                        "org.freedesktop.DBus.Properties.Get",
                        "string:org.mpris.MediaPlayer2.Player",
                        "string:PlaybackStatus",
                    ],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=1.0,
                )
                status = "Stopped"
                if "Playing" in status_raw:
                    status = "Playing"
                elif "Paused" in status_raw:
                    status = "Paused"

                meta_raw = subprocess.check_output(  # noqa: S603
                    [  # noqa: S607
                        "dbus-send",
                        "--session",
                        f"--dest={dest}",
                        "--type=method_call",
                        "--print-reply",
                        "/org/mpris/MediaPlayer2",
                        "org.freedesktop.DBus.Properties.Get",
                        "string:org.mpris.MediaPlayer2.Player",
                        "string:Metadata",
                    ],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=1.0,
                )
                title = ""
                artist = ""
                album = ""
                lines = meta_raw.splitlines()
                for i, ln in enumerate(lines):
                    if "xesam:title" in ln and i + 1 < len(lines):
                        title = lines[i + 1].split('"')[-2] if '"' in lines[i + 1] else ""
                    elif "xesam:artist" in ln and i + 2 < len(lines):
                        for j in range(i + 1, min(i + 5, len(lines))):
                            if '"' in lines[j]:
                                artist = lines[j].split('"')[-2]
                                break
                    elif "xesam:album" in ln and i + 1 < len(lines):
                        album = lines[i + 1].split('"')[-2] if '"' in lines[i + 1] else ""

                if title or status in ("Playing", "Paused"):
                    return {
                        "active": True,
                        "status": status,
                        "title": title or "Media Playing",
                        "artist": artist or dest.split(".")[-1],
                        "album": album,
                    }
        except Exception:  # noqa: BLE001, S110 — best-effort probe contract: no
            pass  # session bus / no MPRIS player -> the idle card below

    return {
        "active": False,
        "status": "Stopped",
        "title": "No Media Playing",
        "artist": "Launch Spotify / Browser",
        "album": "",
    }
