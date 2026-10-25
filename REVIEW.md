# Deep Architectural & Code Review — ak820pro-linux

> **STATUS UPDATE (v0.6.0 re-audit, 2026-10-23):** All **7 confirmed defects (D1–D7) are RESOLVED** and
> regression-tested. See §9 for the per-defect audit trail and §10 for the detailed Phase 2 modular
> package architecture spec. Original review content below is preserved as the historical record;
> live state at re-audit: `python3 -m unittest discover -s tests` → **61/61 OK**.

**Branch:** `ErvinUrban-Bratislava/project-review` @ `d2afd4c`
**Date:** 2026-10-23
**Scope:** `ak820_ctrl.py` (1,127 LOC), `milestone_radar.py`, `widgets.py`, `tests/test_ak820.py`, `udev/70-ajazz-ak820pro.rules`, `pyproject.toml`, README/FINDINGS.
**Live state at review time:** keyboard attached (`0c45:800A` on `/dev/hidraw0..3`), `ak820 probe` succeeds, `python3 -m unittest discover -s tests` → **8/8 OK** in 0.017 s.

---

## 0. Executive Summary

This is a genuinely impressive reverse-engineering achievement: the Sonix `SN32F290` protocol quirks (sub-packet ring counter, 2.0 s watchdog, unbuffered 4 KB RAM buffer) are correctly handled, and FINDINGS.md is exemplary documentation. The code *works*, but it is now at the exact size and coupling level where the current single-file structure starts costing more than it saves.

Headline verdicts:

| Area | Grade | One-line verdict |
|---|---|---|
| Architecture & modularity | **C+** | `ak820_ctrl.py` is a proto-monolith: 7 distinct concerns, a 330-line `main()`, and a layering inversion (upload protocol imports the radar module for hashing). |
| Protocol & HID reliability | **B-** | Happy path is solid and well-researched; failure paths (disconnect, permission, missing ACKs) produce tracebacks or — worse — **false success messages**. |
| Performance | **B+** | Measured: RGB565 conversion is 2.2 ms/frame; USB protocol (~90 ms/chunk + ~1.6 s alignment) dominates. One real win: skip re-alignment on clean state. |
| Testing | **C** | 8 tests cover rendering only. Zero coverage of the protocol layer — the part that actually needs it. No CI. |
| Code quality & standards | **C+** | No logging, no type annotations in the main module, dead config keys, one README-documented command that crashes. |

**7 confirmed defects** (reproduced live, §1) should be fixed before the next release; the rest are Tier A/B improvements with rationale and code.

---

## 1. Confirmed Defects (reproduced in this session)

These are not theoretical — each was verified by execution against this working tree.

### D1 — `backlight set --mode <name>` crashes with `ValueError` (Tier A)

`ak820_ctrl.py:1082`:

```python
mode_val = LIGHT_MODES.get(args.mode.lower(), int(args.mode)) if args.mode else 1
```

Python evaluates the *default argument* `int(args.mode)` **eagerly**, before `.get()` is called. Any named mode therefore crashes:

```text
$ python3 -c 'LIGHT_MODES.get("breath", int("breath"))'
ValueError: invalid literal for int() with base 10: 'breath'
```

The README itself documents the crashing invocation (`ak820 backlight set --mode breath --color "#FF8C00" --speed 3 --brightness 4`, README:153). This is the single most user-visible bug.

**Fix:**

```python
def parse_mode(value: str) -> int:
    """Resolve a lighting mode from name or number; fail with a helpful message."""
    v = value.strip().lower()
    if v in LIGHT_MODES:
        return LIGHT_MODES[v]
    try:
        n = int(v)
    except ValueError:
        raise SystemExit(
            f"Unknown mode '{value}'. Use a name ({', '.join(LIGHT_MODES)}) or 0..19."
        )
    if not 0 <= n <= max(LIGHT_MODES.values()):
        raise SystemExit(f"Mode number must be 0..{max(LIGHT_MODES.values())}, got {n}.")
    return n
```

Also note the current code lets `--mode 99` through to the firmware unclamped; the fix rejects it.

### D2 — Upload reports success even when ACKs are missing (Tier A)

`ak820_ctrl.py:377-395`: the chunk loop counts ACKs, but the outcome is never acted on:

```python
    if acks == total_chunks:
        mr.save_payload_hash(digest, label)
    print("Upload and flash save complete!")   # printed even if acks == 0
    return True                                # caller exits 0
```

If the device NAKs or drops every chunk, the user sees *"Upload and flash save complete!"* and `ak820 run -- ak820 upload ...` reports success. For a tool whose headline use case is CI/build status signalling, a false green is the worst possible failure mode.

**Fix — make incompleteness a first-class error:**

```python
class UploadIncompleteError(AK820Error):
    exit_code = 5
    def __init__(self, acks: int, total: int):
        super().__init__(f"upload incomplete: {acks}/{total} chunk ACKs received")
        self.acks, self.total = acks, total

# in upload_frames_to_screen, replacing the unconditional print:
    if acks != total_chunks:
        raise UploadIncompleteError(acks, total_chunks)
    mr.save_payload_hash(digest, label)
    print("Upload and flash save complete!")
    return True
```

Per FINDINGS.md §4, a missing ACK means the firmware state machine is desynchronized — continuing to stream chunks after a timeout is what caused the original "split frames" bug. A timeout should **abort the upload**, not be counted and forgotten.

### D3 — `build_radar_frames` can exceed the 255-frame firmware limit (Tier A)

`_build_gif_header` (`ak820_ctrl.py:317`) writes `header[0] = num_frames & 0xFF` — a silent wrap. `load_frames_from_file` caps at 255, but `build_radar_frames` (`ak820_ctrl.py:759-791`) does not: `hybrid` view = 4 × bongo-GIF frames + `2 × frames_per_slide` (up to 120). With a ~40-frame bongo GIF and `--seconds 18`, total = 160 + 120 = **280 frames** → header says `24`, the animation plays corrupted/garbage tail frames, and the payload is silently wrong.

**Fix — guard at the single choke point every path flows through:**

```python
MAX_FRAMES = 255  # firmware header byte, FINDINGS.md §3

def upload_frames_to_screen(dev, frames, delays_ms=None, label="screen", force=True):
    if len(frames) > MAX_FRAMES:
        raise ValueError(
            f"{label}: {len(frames)} frames exceeds firmware limit of {MAX_FRAMES} "
            f"(reduce --seconds or GIF frames)"
        )
```

Related: `frames_per_slide = max(1, min(60, ...))` silently clamps `--seconds 30` to ~18 s. Print a warning when clamping so the flag isn't silently ignored.

### D4 — Malformed event dates crash `radar`/`events list` with a raw traceback (Tier A)

`milestone_radar.py:159`:

```python
parts = [int(p) for p in raw_date.split("-")]   # ValueError on "not-a-date"
```

Reproduced: `compute_upcoming_events({"events":[{"title":"X","date":"not-a-date"}]})` → `ValueError: invalid literal for int() with base 10: 'not'`. `events add --date` performs **no validation**, so one typo in the config bricks the daily systemd timer with an unreadable traceback.

### D5 — The Feb-29 fallback crashes on any other invalid date (Tier A)

`milestone_radar.py:167-177`: the `except ValueError` fallback assumes the only possible error is Feb-29-on-non-leap. Reproduced: `date="2020-13-45"` → fallback tries `date(year, 13, 28)` → `ValueError: month must be in 1..12`. The fallback needs to be narrow:

```python
def _safe_date(year: int, month: int, day: int) -> datetime.date:
    try:
        return datetime.date(year, month, day)
    except ValueError:
        if (month, day) == (2, 29):          # the ONLY expected failure mode
            return datetime.date(year, 2, 28)
        raise ConfigError(f"Invalid event date components: {year:04d}-{month:02d}-{day:02d}")

def parse_event_date(raw: str) -> tuple[int | None, int, int]:
    """Validate at 'events add' time so bad data never reaches render time."""
    parts = raw.split("-")
    try:
        if len(parts) == 3:
            y, m, d = (int(p) for p in parts)
            datetime.date(y, m, d)
            return y, m, d
        if len(parts) == 2:
            m, d = (int(p) for p in parts)
            datetime.date(2024, m, d)        # 2024 is a leap year: validates Feb 29
            return None, m, d
    except ValueError:
        pass
    raise ConfigError(f"Invalid date {raw!r}: expected YYYY-MM-DD or MM-DD")
```

Validate in `events add` (fail fast at the CLI) *and* keep `_safe_date` as the render-time safety net.

### D6 — `wait_for_keypress` hardcodes `/dev/hidraw0` and `/dev/hidraw1` (Tier B)

`ak820_ctrl.py:608`. hidraw numbering is assigned in *probe-attach order*, not per-device. Today the keyboard happens to own hidraw0-3 on this machine (verified live), but plug in a USB hub/dongle first and the numbers shift — the keypress wait then watches someone else's device (or finds nothing and silently falls back to stdin). The project already contains the correct pattern (`find_hidraw_devices` scanning sysfs by VID/PID); reuse it:

```python
USAGE_PAGE_GENERIC_DESKTOP = 0x01

def find_keyboard_hidraw() -> list[str]:
    """All hidraw nodes belonging to the AK820 Pro's own HID interfaces."""
    paths = []
    for entry in _iter_hidraw_entries():                 # shared sysfs scan helper
        vid, pid, rd = _read_hid_identity(entry)
        if vid == VID and pid in PIDS:
            paths.append(f"/dev/{entry}")
    return paths
```

(Opening the keyboard's *keyboard* interface read-only is safe — hidraw and hid-input both receive reports; reading does not steal keystrokes.)

### D7 — Dead config keys mislead users (Tier B)

`events.example.json` ships `warn_days: 14`, `urgent_days: 3`, `default_mode`, `default_preset` — **none are read by any code** (grep confirms zero references). The urgency thresholds are hardcoded in `compute_upcoming_events` (3/14/35). Either honor them or remove them; a shipped example that lies about configurability generates support issues:

```python
def compute_upcoming_events(cfg, today=None):
    ...
    urgent = cfg.get("urgent_days", 3)
    warn   = cfg.get("warn_days", 14)
    soon   = cfg.get("soon_days", 35)
    if days_left == 0:      theme_key = "today"
    elif days_left <= urgent: theme_key = "urgent"
    elif days_left <= warn:   theme_key = "warn"
    elif days_left <= soon:   theme_key = "soon"
    else:                     theme_key = "calm"
```

### Minor defects (fix opportunistically)

| # | Location | Issue |
|---|---|---|
| m1 | `ak820_ctrl.py:120-125` | `__enter__` leaks `ctrl_fd` if the data-node `os.open` raises (fd stays open, no cleanup). Wrap in try/except calling `self.__exit__()`. |
| m2 | `ak820_ctrl.py:149-157` | `handshake()` swallows `OSError` and returns `None`; **every caller ignores the return value** — a dead control channel is invisible until the upload fails. |
| m3 | `ak820_ctrl.py:167-177` | `send_data_chunk` accepts *any* 64-byte read as an ACK. Validate the magic: `if not ack.startswith(b"\x01\x5A\x02"): raise ProtocolError(...)`. A stale/misaligned report currently counts as a good ACK. |
| m4 | `ak820_ctrl.py:353-357` | `align_data_pipe` failure prints a WARN and the upload proceeds anyway — into the exact desync state alignment exists to prevent. Retry once, then abort. |
| m5 | `milestone_radar.py:32` | `DEFAULT_EVENTS_PATH` is computed at import time (freezing `$AK820_EVENTS_PATH` too early) and never referenced. Delete it. |
| m6 | `milestone_radar.py:145-149` | `save_events_config` is non-atomic (crash mid-write corrupts the config) and `os.makedirs(os.path.dirname(path))` raises on a bare relative filename. Use `tempfile.mkstemp` + `os.replace`, and `dirname or "."`. |
| m7 | `milestone_radar.py:137-142` | `load_events_config` propagates `json.JSONDecodeError` as a traceback. Catch → `ConfigError(f"{path}: {e}")`. |
| m8 | `widgets.py:16` | `Tuple` imported, unused. Also `SCREEN_W/H`, `FONT_*_PATHS`, `_load_font` are duplicated verbatim between `widgets.py` and `milestone_radar.py` — extract a shared `render_common`. |
| m9 | `ak820_ctrl.py:260-273` | `fit_image("contain")` pastes an RGBA image onto RGB without a mask — semi-transparent pixels composite wrong. Use `out.paste(copy, (ox, oy), copy)` (alpha as mask). |
| m10 | `ak820_ctrl.py:551-557` | `parse_hex_color` raises bare `ValueError` on `--color banana` → traceback. Wrap into a CLI error. |
| m11 | udev rules | Cover `0xFEFE`? `PIDS` includes it but the udev rule doesn't — inconsistent. Either drop `0xFEFE` from `PIDS` (it looks like a test fixture PID) or add the rule line. |
| m12 | `ak820_ctrl.py:384` | `\r` progress spam lands verbatim in journald (no tty). Gate on `sys.stdout.isatty()`. |

---

## 2. Architecture & Modularity

### 2.1 Is `ak820_ctrl.py` a monolith? Yes — measurably

1,127 lines / 45 KB containing **seven distinct concerns**:

| Concern | Symbols | Approx. LOC |
|---|---|---|
| HID discovery | `_parse_usage_page`, `find_hidraw_devices` | 65 |
| HID transport | ioctl constants, `AK820ProDevice` | 120 |
| Image processing | `image_to_rgb565`, `fit_image`, `load_frames_from_file`, `_build_gif_header` | 80 |
| Upload protocol | `align_data_pipe`, `upload_frames_to_screen`, `upload_to_screen` | 120 |
| RTC | `sync_clock` | 35 |
| Backlight/status | `LIGHT_MODES`, `SHOWCASE_PRESETS`, `set_backlight`, status/demo/keypress | 280 |
| CLI | `main()` argparse + dispatch | 330 |

Warning signs already visible in the source:

1. **Layering inversion.** `upload_frames_to_screen` — the *protocol* layer — does `import milestone_radar as mr` at function level (`ak820_ctrl.py:346`) purely to reach the SHA-256 dedup helpers. Dedup is a *persistence* concern; it has nothing to do with milestone rendering. The function-level imports (10 of them: lines 346, 644, 667, 766, 795-796, 966, 973, 1008, 1025) are the code telling you the dependency graph is wrong.
2. **`main()` is a god-function**: parsing, config CRUD (`events add/remove` logic inline at lines 926-949), rendering, upload orchestration, and the `--watch` loops all live in one 330-line body.
3. **Blast radius**: any change to backlight presets re-imports the whole module including PIL and the HID ioctl layer; nothing in the protocol layer can be tested without the CLI namespace.

### 2.2 Recommended target layout (package, with compat shim)

```text
ak820pro/
├── __init__.py          # __version__
├── errors.py            # AK820Error taxonomy + exit codes (§3.4)
├── protocol.py          # constants, _cmd_packet, ioctl numbers, _build_gif_header
├── transport.py         # HidTransport protocol + LinuxHidrawTransport + sysfs discovery
├── device.py            # AK820Pro: feature cmds, handshake, chunk stream, align_data_pipe
├── imaging.py           # fit_image, load_frames_from_file, image_to_rgb565
├── upload.py            # upload_frames_to_screen (+ frame guard, UploadResult)
├── state.py             # payload-hash dedup store (moved out of milestone_radar)
├── config.py            # events config load/save (atomic), idle-backlight cfg
├── backlight.py         # LIGHT_MODES, presets, set_backlight, status lighting, demo
├── radar_frames.py      # build_radar_frames (belongs with the radar domain)
├── widgets.py           # (moved as-is)
├── milestone_radar.py   # rendering + date math only
└── cli.py               # argparse + one small handler per subcommand
ak820_ctrl.py            # compat shim: `from ak820pro.cli import main` (keeps pipx entry point working)
```

**Dependency rule (enforceable with import-linter or a CI grep):**

```text
cli → {upload, backlight, radar_frames, config, imaging, device}
upload → {device, imaging, state}
device → {transport, protocol, errors}
transport → {protocol, errors}
{milestone_radar, widgets} → imaging (rendering only — never device/transport)
```

Why this split specifically:

- **`transport.py` is the seam that unlocks mock testing (§5)** — everything above it becomes pure Python testable without hardware.
- **`state.py` fixes the inversion**: `upload.py` imports `state`, `milestone_radar` imports nothing above the rendering line. The radar module becomes a pure renderer + date calculator (which is what its docstring already claims).
- **`errors.py` centralizes the exit-code contract** (§3.4), which systemd units and `ak820 run` both need.
- **Migration is incremental and zero-risk to users**: move one module at a time, keep `ak820_ctrl.py` re-exporting every public name (`from ak820pro.device import AK820ProDevice  # noqa: F401` etc.), so the installed `ak820`/`ak820-ctrl` console scripts and any user scripts importing `ak820_ctrl` keep working. Update `pyproject.toml` to `packages = ["ak820pro"]` in the final step.

### 2.3 `main()` → dispatch table

```python
HANDLERS = {
    "upload":     cmd_upload,
    "radar":      cmd_radar,
    "card":       cmd_card,
    "hud":        cmd_hud,      # aliases: stats
    "media":      cmd_media,
    "sync-clock": cmd_sync,
    "probe":      cmd_probe,
    "backlight":  cmd_backlight,
    "status":     cmd_status,
    "run":        cmd_run,
}

def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command in OFFLINE_COMMANDS:          # events, radar --save-preview, probe
            return HANDLERS[args.command](args)
        tft, ctrl = find_hidraw_devices()
        if not ctrl or (needs_tft(args.command) and not tft):
            raise DeviceNotFoundError("AK820 Pro HID interfaces not found (is the USB cable connected?)")
        with open_device(tft, ctrl) as dev:
            return HANDLERS[args.command](args, dev) or 0
    except AK820Error as e:
        print(f"ak820: error: {e}", file=sys.stderr)
        return e.exit_code
```

Each handler ≤ 40 lines, independently testable with a fake device. The `--watch` loops become one shared helper (`run_watch_loop(push_fn, interval)`) instead of two copy-pasted `while True: sleep; push` blocks (lines 1016-1040).

---

## 3. Protocol & HID Reliability

### 3.1 What's already right (keep it)

- The two-stage `align_data_pipe` correctly encodes the firmware constraints from FINDINGS.md §4 (sub-packet counter not reset by `0x72`, 2.0 s watchdog, mode-5 feature-report blackout). The 8×220 ms coarse + 8×220 ms fine probing stays under the watchdog with margin — good engineering.
- `drain_data_acks()` before/after transactions addresses the leftover-final-ACK race directly.
- The ioctl encodings are correct: the kernel defines both `HIDIOCSFEATURE`/`HIDIOCGFEATURE` as `_IOC(_IOC_WRITE|_IOC_READ, 'H', 0x06/0x07, len)` → `0xC0480600|65<<16` — the hardcoded constants match. Add a comment citing `include/uapi/linux/hid.h` so future readers don't "fix" the `0xC0` direction bits.
- Dedup-before-align ordering (`check_and_record_payload_hash` runs *before* `align_data_pipe`) correctly avoids a 2 s alignment for no-op pushes.

### 3.2 Failure-mode matrix (current vs. target)

| Failure | Current behavior | Target behavior |
|---|---|---|
| Device unplugged before command | `sys.exit("...not found...")` (OK) | same, exit 3 |
| Device unplugged **mid-upload** | `OSError(ENODEV)` traceback | `DeviceDisconnectedError`, exit 75 (EX_TEMPFAIL), one clean line |
| No permission (udev not installed) | `PermissionError` traceback | `PermissionDeniedError` + "install udev rule (README §Setup)" hint, exit 4 |
| Chunk ACK timeout | counted, upload continues, "complete!" printed | abort, exit 5, state file *not* written (already correct) |
| ACK magic mismatch (`01 5A 02`) | accepted as valid ACK | `ProtocolError`, abort |
| `GET_FEATURE` handshake fails | silently returns `None`, ignored | count consecutive failures; after 2, raise `DeviceDisconnectedError` |
| Config JSON corrupt | `JSONDecodeError` traceback | `ConfigError` with path, exit 1 |

### 3.3 Concrete transport hardening

Wrap the raw fd layer once; every caller benefits:

```python
# transport.py
import errno

class LinuxHidrawTransport:
    def __init__(self, ctrl_fd: int | None, data_fd: int | None):
        self.ctrl_fd, self.data_fd = ctrl_fd, data_fd

    def _translate(self, e: OSError, what: str):
        if e.errno == errno.ENODEV:
            return DeviceDisconnectedError(f"{what}: device disappeared (unplugged?)")
        if e.errno == errno.EACCES:
            return PermissionDeniedError(
                f"{what}: access denied. Install the udev rule (see README) or re-run with sudo."
            )
        return e

    def write_data(self, payload: bytes) -> None:
        try:
            os.write(self.data_fd, b"\x00" + payload)
        except OSError as e:
            raise self._translate(e, "data write") from e

    def read_data(self, timeout: float) -> bytes | None:
        try:
            r, _, _ = select.select([self.data_fd], [], [], timeout)
            if not r:
                return None
            ack = os.read(self.data_fd, 64)
        except OSError as e:
            raise self._translate(e, "data read") from e
        if not ack.startswith(ACK_MAGIC):
            raise ProtocolError(f"bad ACK {ack[:4].hex()} (expected 015a02) — pipe desync, re-align required")
        return ack
```

And the `__enter__` leak fix + friendly open errors:

```python
def __enter__(self):
    try:
        if self.ctrl_path:
            self.ctrl_fd = os.open(self.ctrl_path, os.O_RDWR)
        if self.tft_path:
            self.data_fd = os.open(self.tft_path, os.O_RDWR)
    except OSError as e:
        self.__exit__()                       # close whatever did open (fixes m1)
        raise translate_open(e) from e
    return self
```

### 3.4 Exit-code contract (new — needed by systemd and `ak820 run`)

| Code | Meaning | Consumer behavior |
|---|---|---|
| 0 | success (incl. dedup skip) | — |
| 2 | usage error (argparse default) | — |
| 3 | device not found | give up |
| 4 | permission denied | run setup steps |
| 5 | upload incomplete (missing ACKs) | retry once |
| 75 | `EX_TEMPFAIL` — transient disconnect mid-op | systemd `Restart=` will retry |

```python
class AK820Error(Exception):
    exit_code = 1
class DeviceNotFoundError(AK820Error):  exit_code = 3
class PermissionDeniedError(AK820Error): exit_code = 4
class UploadIncompleteError(AK820Error): exit_code = 5
class DeviceDisconnectedError(AK820Error): exit_code = 75
```

`--watch` loops should catch `DeviceDisconnectedError`, print one line, and exit 75 — that turns "keyboard unplugged" from a traceback into a systemd-recoverable event.

### 3.5 Alignment skip on known-clean pipe (~2 s saved per repeat upload)

Every upload pays ~1.6-2 s of `align_data_pipe` even when the previous upload ended with all ACKs + clean `SAVE` — in which case `sub_packet_idx` is provably 0 (FINDINGS.md §4). Record that in the state file and skip:

```python
# state.py
def pipe_known_clean() -> bool: ...
def mark_pipe(clean: bool) -> None: ...

# upload.py
if not state.pipe_known_clean():
    aligned_pkts, acked = dev.align_data_pipe()
    if not acked:
        dev.align_data_pipe()                    # one retry
        if not dev.align_data_pipe()[1]:
            raise ProtocolError("data pipe will not align — check cable / re-seat device")
state.mark_pipe(False)                           # pessimistic until proven
... stream chunks ...
if acks == total_chunks:
    state.mark_pipe(True)                        # next upload skips alignment
```

On the first timeout, fall back to a full align + retry of the failed chunk. This is safe because the state file is only written after a fully-ACKed `SAVE`, and any crash/timeout flips it back to dirty.

### 3.6 `handshake()` should be observable

```python
def handshake(self, post_delay=CMD_DELAY) -> bytes | None:
    time.sleep(CMD_DELAY)
    try:
        resp = self.get_feature()
    except OSError as e:
        log.debug("handshake GET_FEATURE failed: %s", e)
        self._handshake_failures += 1
        if self._handshake_failures >= 2:
            raise DeviceDisconnectedError("control pipe not responding (2 failed handshakes)")
    else:
        self._handshake_failures = 0
    time.sleep(post_delay)
    return resp
```

Two consecutive failures is a dead control pipe — continuing guarantees a confusing failure later.

---

## 4. Performance & Resource Footprint

Measured on this machine (Ryzen AI MAX+), not estimated:

| Operation | Measured | Notes |
|---|---|---|
| `image_to_rgb565` (pure-Python double loop) | **2.2 ms/frame** | 255-frame GIF ≈ 0.56 s CPU total |
| USB chunk ACK wait | ~90 ms/chunk (FINDINGS §2) | 1 frame = 8 chunks + header chunk |
| `align_data_pipe` | ~1.6-2.0 s | paid on *every* upload today |
| Full single-frame upload | ~3 s wall | protocol-bound, CPU < 1 % |
| `--watch 15` duty cycle | ~20 % of interval | mostly `select()` sleep |
| Peak memory (255-frame GIF) | ~25 MB | frames list 8.4 MB + payload bytearray + `bytes()` copy |

**Honest conclusion: there is no CPU problem.** The loop is protocol-bound. The four changes worth making, in order of payoff:

### 4.1 Skip redundant alignment (§3.5) — saves ~2 s per repeat upload. Biggest single win. **DONE v0.13.0 (§12).**

### 4.2 Cache fonts with `functools.lru_cache`

`_load_font` re-parses the TTF on every call, and `_fit_text_font` calls it once *per candidate size* (up to 6× per text element), and `--watch` re-renders every refresh. Free fix in both `widgets.py` and `milestone_radar.py` (ideally one shared `render_common.py`, fixing duplication m8):

```python
@functools.lru_cache(maxsize=128)
def _truetype(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)

def _load_font(paths: list[str], size: int):
    for p in paths:
        if os.path.exists(p):
            try:
                return _truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()
```

### 4.3 One `playerctl` call instead of four

`get_media_status` (`widgets.py:214-234`) spawns up to 4 sequential subprocesses with 1 s timeouts each — worst case 4 s per refresh for a widget that should cost ~50 ms:

```python
out = subprocess.check_output(
    ["playerctl", "-f", "{{status}}\t{{title}}\t{{artist}}\t{{album}}", "metadata"],
    text=True, stderr=subprocess.DEVNULL, timeout=1.5,
).strip()
status, title, artist, album = (out.split("\t", 3) + ["", "", "", ""])[:4]
```

(The `dbus-send` fallback parsing — `split('"')[-2]` over raw reply text — is inherently brittle; keep it as last resort but log at debug when it's used, and consider `dasbus`/`dbus-fast` as an optional dep later.)

### 4.4 Optional numpy fast path for RGB565 (nice-to-have, not urgent)

At 2.2 ms/frame the pure loop is fine for single frames; a 255-frame GIF conversion is 0.56 s of pure Python. If GIF-heavy usage grows, add an *optional* accelerator with graceful fallback (do **not** make numpy a hard dependency — the "dependency-light" positioning is a feature):

```python
try:
    import numpy as _np
except ImportError:
    _np = None

def image_to_rgb565(img: Image.Image) -> bytes:
    rgb = img.convert("RGB")
    if _np is not None:
        a = _np.asarray(rgb, dtype=_np.uint16)
        out = ((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)
        return out.astype("<u2").tobytes()      # little-endian, byte-identical to the loop
    ...existing loop...
```

Verified equivalence logic: `(r & 0xF8) << 8 == (r >> 3) << 11`, `(g & 0xFC) << 3 == (g >> 2) << 5`, `b >> 3` unchanged.

### 4.5 Micro-cleanup: avoid the `bytes(payload)` copy

`upload_frames_to_screen` builds a `bytearray` then copies to `bytes` (peak 2× payload). Slice the bytearray directly for chunking and hash it in place (`hashlib.sha256(memoryview(payload))`) — saves ~8 MB peak on worst-case uploads. Marginal, but free.

### 4.6 What *not* to do

Don't build a resident daemon to "cache pixel buffers" yet. Each push is a short-lived process; the dedup store already prevents redundant USB traffic, and a daemon adds lifecycle/locking concerns (udev events, session changes) that outweigh the ~3 s per-push protocol cost at watch intervals ≥ 10 s. Revisit only if sub-5 s HUD refresh becomes a goal.

---

## 5. Testing & Verification (mock HID — the headline gap)

### 5.1 Current state

8 tests, all rendering/packet-shape. The protocol layer — `align_data_pipe`, chunk streaming, ACK accounting, dedup, discovery — has **zero coverage**, and cannot be tested as written because `AK820ProDevice` hardwires `os.open`/`fcntl.ioctl`/`select` to real fds. That means CI can never verify the driver, and every regression in the trickiest code path is found on the physical keyboard.

### 5.2 The enabling refactor: inject a transport

Define the seam (this is the same `transport.py` from §3.3 — one refactor serves both reliability and testability):

```python
# transport.py
from typing import Protocol

class HidTransport(Protocol):
    def set_feature(self, report64: bytes) -> None: ...
    def get_feature(self) -> bytes: ...
    def write_data(self, payload: bytes) -> None: ...
    def read_data(self, timeout: float) -> bytes | None: ...

class AK820Pro:
    """Pure state machine over a transport — no fds, no ioctls, fully testable."""
    def __init__(self, transport: HidTransport): ...
```

`AK820ProDevice` becomes a thin factory: `AK820Pro(LinuxHidrawTransport(open_fds))`. All protocol logic (`align_data_pipe`, `upload_frames_to_screen`, `sync_clock`, `set_backlight`) moves onto `AK820Pro` and touches nothing OS-specific.

### 5.3 A firmware simulator for tests

The fake must model the three firmware behaviors FINDINGS.md documents, or the tests are worthless:

```python
# tests/fake_transport.py
ACK = b"\x01\x5A\x02" + b"\x00" * 61

class FakeTransport:
    """Emulates SN32F290: 64-packet sub-counter, per-chunk ACK, injectable faults."""

    def __init__(self, drop_ack_at: int | None = None, ack_magic: bool = True):
        self.sub_packet_idx = 0          # [0x2000003A]
        self.chunks_expected = 0
        self.chunks_seen = 0
        self.feature_log: list[bytes] = []
        self.rx_queue: list[bytes] = []
        self._buf = bytearray()
        self.drop_ack_at = drop_ack_at   # drop the Nth chunk ACK to simulate loss

    # --- control pipe -----------------------------------------------------
    def set_feature(self, report64: bytes) -> None:
        self.feature_log.append(bytes(report64))
        if report64[0] == 0x04 and report64[1] == 0x72:      # IMAGE_CFG
            self.chunks_expected = report64[8] | (report64[9] << 8)
            self.chunks_seen = 0

    def get_feature(self) -> bytes:
        return b"\x00" * 64                                   # canned handshake

    # --- data pipe ----------------------------------------------------------
    def write_data(self, payload: bytes) -> None:
        self._buf += payload
        while len(self._buf) >= 64:
            pkt, self._buf = bytes(self._buf[:64]), self._buf[64:]
            self.sub_packet_idx = (self.sub_packet_idx + 1) % 64
            if self.sub_packet_idx == 0:                      # full 4096B chunk landed
                self.chunks_seen += 1
                if self.chunks_seen != self.drop_ack_at:
                    self.rx_queue.append(ACK if self.ack_magic else b"\xFF" * 64)

    def read_data(self, timeout: float) -> bytes | None:
        return self.rx_queue.pop(0) if self.rx_queue else None
```

### 5.4 The tests this unlocks

```python
# tests/test_protocol.py
class TestUploadProtocol(unittest.TestCase):
    def test_upload_streams_all_chunks_and_acks(self):
        fake = FakeTransport()
        dev = AK820Pro(fake)
        frames = [b"\x00" * 32768]                    # 1 frame → 8 chunks + header chunk
        upload_frames(dev, frames, label="t", force=True, align=False)
        self.assertEqual(fake.chunks_seen, 9)

    def test_missing_ack_raises_not_false_success(self):        # regression for D2
        fake = FakeTransport(drop_ack_at=3)
        with self.assertRaises(UploadIncompleteError) as ctx:
            upload_frames(AK820Pro(fake), [b"\x00" * 32768], label="t", force=True, align=False)
        self.assertLess(ctx.exception.acks, ctx.exception.total)

    def test_bad_ack_magic_is_protocol_error(self):             # regression for m3
        fake = FakeTransport(ack_magic=False)
        with self.assertRaises(ProtocolError):
            upload_frames(AK820Pro(fake), [b"\x00" * 32768], label="t", force=True, align=False)

    def test_over_255_frames_rejected(self):                    # regression for D3
        with self.assertRaises(ValueError):
            upload_frames(AK820Pro(FakeTransport()), [b"\x00" * 32768] * 256, label="t")

    def test_sync_clock_packet_sequence(self):
        fake = FakeTransport()
        sync_clock(AK820Pro(fake), now=datetime.datetime(2026, 10, 23, 7, 30, 15))
        time_data = next(f for f in fake.feature_log if f[2] == 0x5A)
        self.assertEqual(time_data[3:9], bytes([26, 10, 23, 7, 30, 15]))
        self.assertEqual(time_data[62:64], b"\xAA\x55")

    def test_align_data_pipe_locks_subpacket_index(self):
        fake = FakeTransport()
        fake.sub_packet_idx = 37                              # arbitrary dirty state
        _, acked = AK820Pro(fake).align_data_pipe()
        self.assertTrue(acked)
        self.assertEqual(fake.sub_packet_idx, 0)

    def test_backlight_named_mode_parses(self):                 # regression for D1
        self.assertEqual(parse_mode("breath"), 7)
        self.assertEqual(parse_mode("13"), 13)
        with self.assertRaises(SystemExit):
            parse_mode("banana")
```

Plus pure-unit tests that need no transport at all:

- `find_hidraw_devices(base=<tmp_path>)` against a fake sysfs tree — requires making the sysfs root a parameter (one-line change, huge testability payoff):

```python
def find_hidraw_devices(base: str = "/sys/class/hidraw", dev_prefix: str = "/dev/"): ...
```

- `_parse_usage_page` with hand-built descriptor bytes (`b"\x06\x68\xff"` → `0xFF68`).
- Dedup round-trip with `AK820_STATE_PATH` env override (new — makes the state path test-configurable instead of hardcoded `~/.cache`).
- `compute_upcoming_events`: Feb-29 clamping, `MM-DD` dates, past-date rollover, malformed date → `ConfigError` (D4/D5 regressions).
- `load_frames_from_file` on a Pillow-generated 3-frame GIF (Pillow can write GIFs — no binary fixture needed).
- `parse_hex_color`, `fit_image` output sizes for all three modes.
- CLI smoke tests via `--save-preview` paths (already device-free) + a new global `--dry-run` flag that renders, computes the digest/chunk count, prints what *would* be uploaded, and exits 0 — this makes every CLI path CI-verifiable without hardware.

### 5.5 CI workflow (`.github/workflows/ci.yml`)

```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix: { python-version: ["3.10", "3.12", "3.13"] }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "${{ matrix.python-version }}" }
      - run: pip install . && pip install ruff
      - run: ruff check ak820pro tests
      - run: python -m unittest discover -s tests -v
```

No `hidraw` access needed on runners — that's the entire point of the transport seam. Add a `verify_cmd` entry to the Desk Deck proposals catalog per the workflow rules, e.g. `verify_cmd: "cd <repo> && python -m unittest discover -s tests"`.

---

## 6. Code Quality & Standards

### 6.1 Type annotations

`widgets.py` is partially annotated; `milestone_radar.py` uses pre-PEP-585 `typing.Dict/List` (the project requires ≥3.10 — use builtins); `ak820_ctrl.py` is essentially unannotated. The protocol boundary is where annotations pay for themselves:

```python
def find_hidraw_devices(base: str = "/sys/class/hidraw") -> tuple[str | None, str | None]: ...
def load_frames_from_file(path: str, fit_mode: str = "fill",
                          max_frames: int = 255) -> tuple[list[bytes], list[int] | None]: ...
def upload_frames_to_screen(dev: AK820Pro, frames: list[bytes],
                            delays_ms: list[int] | None = None,
                            label: str = "screen", force: bool = True) -> UploadResult: ...
def parse_hex_color(hex_str: str) -> tuple[int, int, int]: ...
```

Add `mypy ak820pro` (or pyright) to CI once the package split lands. Replace `from typing import Dict, List, Optional, Tuple` with `X | None` / `list[...]` / `tuple[...]` throughout.

### 6.2 Logging vs. print

Today everything is `print()` to stdout — diagnostics, warnings, and data (`events list --json`) are indistinguishable, and `\r` progress lands in journald. Convention to adopt:

- **stdout**: machine-readable data only (`events list --json`, `probe --json`).
- **stderr via `logging`**: everything else.

```python
log = logging.getLogger("ak820")

def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname).1s %(message)s",
        stream=sys.stderr,
    )

# progress: only when interactive
if sys.stdout.isatty():
    print(f"\r  Progress: {pct:3d}% ...", end="", flush=True)
else:
    if (idx + 1) % 16 == 0:
        log.info("upload %d%% (%d/%d chunks)", pct, idx + 1, total_chunks)
```

Add a global `--verbose/-v` flag. This also makes `journalctl -u ak820-radar` output actually diagnosable.

### 6.3 systemd integration

The README timer recipe works *only while the user has an active graphical session*, because the udev rule relies on `TAG+="uaccess"`, which grants access to the **active** local session. A lingered/headless user (or a timer that fires before login) gets `PermissionError` — currently a traceback, and per §3.4 should be exit 4. Fixes:

1. **Ship the unit files in the repo** (`systemd/ak820-radar.service`, `ak820-radar.timer`, `ak820-hud.service`) instead of only documenting them — they drift otherwise. Watch-mode unit:

```ini
# systemd/ak820-hud.service
[Unit]
Description=Ak820 Pro live HUD updater
After=systemd-user-sessions.service

[Service]
ExecStart=%h/.local/bin/ak820 hud --watch 15
Restart=on-failure
RestartSec=30          # pairs with exit 75 on unplug: retries until the cable returns
Nice=10

[Install]
WantedBy=default.target
```

2. **Add a group-based udev fallback** for lingered sessions, and document `loginctl enable-linger $USER`:

```text
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0c45", ATTRS{idProduct}=="800a", MODE="0660", GROUP="ak820", TAG+="uaccess"
```
```bash
sudo groupadd -f ak820 && sudo usermod -aG ak820 "$USER"   # then re-login
```

3. **Exit-code contract (§3.4)** is what makes `Restart=on-failure` sane: 3/4 (won't self-heal) shouldn't hot-loop; 75 (transient) should.

### 6.4 `pyproject.toml`

- `license = { text = "MIT" }` is deprecated under PEP 639 (setuptools ≥ 77 warns): use `license = "MIT"` and drop the License classifier.
- Add dev extras so CI and contributors install one thing:

```toml
[project.optional-dependencies]
dev = ["ruff>=0.6", "mypy>=1.10"]

[tool.ruff]
target-version = "py310"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B"]   # UP auto-flags the typing.Dict modernization
```

- After the package split (§2.2): `packages = ["ak820pro"]` replaces `py-modules`; keep the `ak820`/`ak820-ctrl` script entry points pointed at `ak820pro.cli:main`.

### 6.5 Smaller standards items

- `getattr(args, "config", None)` / `getattr(args, "json", False)` sprinkled through `main()` (9 occurrences) are a smell: argparse namespaces should be shaped by the parser, not probed defensively. With per-subcommand handlers (§2.3) each handler knows its own attributes.
- `check_and_record_payload_hash(payload, mode_desc, force)` — `mode_desc` is accepted and unused; drop it or store it (it's already stored via `save_payload_hash`).
- `events.example.json` `default_mode: "static"` vs. code default `"radar"` — inconsistent dead defaults (see D7).
- `sync_clock` uses naive `datetime.now()`; during a DST fall-back repeat hour the RTC gets the second occurrence of the ambiguous time. Use `time.localtime()` + `datetime.fromtimestamp(time.time())` consistently and note the limitation in a comment.
- README: add a "Exit codes" table, a fonts dependency note (cards silently degrade to PIL's bitmap font if no Liberation/Noto/DejaVu TTF exists — worth a `fonts-liberation` mention in install docs), and a troubleshooting entry mapping exit codes 3/4 to cable/udev.

---

## 7. Prioritized Roadmap

### Tier A — correctness, do first (each has a regression test from §5.4)

| # | Item | Est. effort |
|---|---|---|
| A1 | Fix `--mode` eager-`int()` crash (D1) + `parse_mode` | 15 min |
| A2 | Upload failure honesty: `UploadIncompleteError`, exit 5, no false "complete!" (D2) | 30 min |
| A3 | Frame-count ≤ 255 guard + clamp warning (D3) | 15 min |
| A4 | Date validation at `events add` + narrow Feb-29 fallback (D4/D5) | 45 min |
| A5 | Error taxonomy + friendly disconnect/permission messages (m1, §3.3/3.4) | 1-2 h |
| A6 | ACK magic validation + handshake failure counting (m2, m3) | 30 min |
| A7 | Remove dead config keys or honor `warn_days`/`urgent_days` (D7) | 30 min |
| A8 | udev: reconcile `0xFEFE` PID vs rules (m11); add GROUP fallback + linger docs | 30 min |

### Tier B — structural, next iteration

| # | Item |
|---|---|
| B1 | Package split (§2.2) with `ak820_ctrl.py` compat shim; move dedup to `state.py` |
| B2 | `HidTransport` seam + `FakeTransport` firmware simulator + protocol test suite (§5) |
| B3 | CI workflow + ruff + mypy; `verify_cmd` registered on the Deck |
| B4 | ~~Alignment-skip on clean pipe (§3.5, ~2 s/upload saved)~~ **DONE v0.13.0 (§12)** |
| B5 | Shared `render_common` (fonts/`_load_font` lru_cache/screen constants) |
| B6 | ~~Single `playerctl` call; progress gated on tty; `logging` + `--verbose`~~ **DONE v0.14.0 (§13)** |
| B7 | ~~Ship systemd units; atomic config writes (m6); `--dry-run` flag~~ **DONE v0.14.0 (§13)** |
| B8 | `find_hidraw_devices(base=...)` parameter + fake-sysfs tests; keypress via sysfs scan (D6) |

### Tier C — nice to have

- Optional numpy RGB565 fast path (§4.4) with byte-identical output test.
- `probe --json` / `--verbose` for AI-tool consumption (matches the existing `events list --json` pattern).
- `dasbus`-based MPRIS backend replacing `dbus-send` text scraping.
- Config schema validation (`jsonschema`) if the events file grows more knobs.

---

## 8. What Not to Change

To keep this review balanced — these are deliberate, correct designs worth preserving:

- **`align_data_pipe`'s two-stage probe** is the right shape given the 2.0 s watchdog budget (max 1.76 s worst case); don't "simplify" it to a fixed pre-delay.
- **SHA-256 payload dedup before touching USB** is exactly right for `--watch` loops and daily timers — it's why watch mode is cheap. Just relocate it to `state.py`.
- **`report_descriptor` parsing for interface discovery** (verified world-readable, `0444`) is more robust than matching hidraw indices; extend it, don't replace it.
- **FINDINGS.md as the protocol source of truth** with code comments citing firmware addresses (`0x2000003A`, `[0x200000F0]`) — keep this convention in every new protocol constant.
- **No shell=True anywhere**, `subprocess.run(cmd)` list form in `ak820 run` — correct.

---

*Review performed against the live worktree; all defect reproductions and measurements were executed on this machine (keyboard attached, `0c45:800A`). Test suite state at review time: 8/8 passing.*

---

## 9. Defect Resolution Audit (v0.6.0 re-audit, 2026-10-23)

Re-verified by live execution against the current worktree (`ak820_ctrl.py` 1,250 LOC,
`milestone_radar.py` 491 LOC, `widgets.py` 379 LOC) plus the regression suite
(**61/61 passing**). Every defect below was probed live, not just grep-confirmed.

| # | Defect | Fixed in | Live verification at re-audit | Status |
|---|---|---|---|---|
| D1 | `backlight set --mode <name>` eager-`int()` crash | `v0.1.0` | `parse_mode("breath")→7`, `parse_mode("13")→13`; `parse_mode("banana")` raises `ValueError` with the full mode-name list, caught at the CLI call site (`ak820_ctrl.py:1186`) → friendly error, no traceback | **RESOLVED** |
| D2 | False "complete!" on dropped chunk ACKs | `v0.2.0` | `UploadIncompleteError` (exit 5) raised when `acks != total_chunks`; digest recorded only on full transfer; `tests/test_upload.py` | **RESOLVED** |
| D3 | Silent `& 0xFF` wrap past 255 frames | `v0.3.0` | `MAX_FRAMES = 255` (`ak820_ctrl.py:41`) enforced in `upload_frames_to_screen` before any USB packet; hybrid radar frame total clamped; `tests/test_frame_limit.py` | **RESOLVED** |
| D4 | Malformed event dates → raw traceback | `v0.4.0` | `parse_event_date("not-a-date")` and `("2020-13-45")` raise `ConfigError` (exit 6) at `events add` time; `tests/test_radar.py` | **RESOLVED** |
| D5 | Feb-29 fallback crashed on any invalid component | `v0.4.0` | `_safe_date()` clamps (2,29)→Feb 28 **only** on non-leap years (`parse_event_date("2026-02-29")` correctly rejected — 2026 is not a leap year; `("2028-02-29")→(2028,2,29)` accepted); `("03-15")→(None,3,15)` | **RESOLVED** |
| D6 | Hardcoded `/dev/hidraw0/1` in `wait_for_keypress` | `v0.5.0` | `find_keyboard_hidraw(base="/sys/class/hidraw")` dynamic sysfs VID/PID scan (`ak820_ctrl.py:140`), `base=` parameterized for fake-sysfs tests; `tests/test_hidraw.py` | **RESOLVED** |
| D7 | Dead config keys (`warn_days`/`urgent_days`) | `v0.5.0` | `compute_upcoming_events` honors `urgent_days`/`warn_days`/`soon_days` via `_threshold()` with safe fallbacks (3/14/35); `events.example.json` aligned with code defaults; `tests/test_config_thresholds.py` | **RESOLVED** |

**Re-audit verdict:** Tier A defect lane (§7 A1–A4, A7) is fully closed. Remaining open work is
structural (Tier B): the monolith split (§2.1), the transport seam (§5.2), and the minor-defect table
(m1–m12), which Phase 2 (§10) now subsumes into concrete per-version deliverables.

---

## 10. Phase 2 Architecture Spec — Modular Package Refactoring (`ak820/`)

Detailed, implementation-ready spec for queue items **v0.7.0 → v0.9.0** (with v0.10.0 CI gate).
Hard constraints for every phase: each new module **< 300 lines**; `ak820_ctrl.py` keeps **every
existing public name importable** (backward-compat shim); `python3 -m unittest discover -s tests`
stays 100 % green at every tag.

### 10.1 Target package layout (supersedes §2.2 — package name is `ak820/`)

```text
ak820/
├── __init__.py        # __version__ = "0.9.0"; re-export public API          (~20 LOC)
├── errors.py          # AK820Error taxonomy + exit codes (§3.4)              (~40 LOC)
├── protocol.py        # v0.7.0: VID/PIDS, ioctl constants, _cmd_packet,
│                      #   ACK_MAGIC, MAX_FRAMES, _build_gif_header,
│                      #   parse_mode, parse_hex_color                        (~180 LOC)
├── transport.py       # v0.7.0: HidTransport Protocol + LinuxHidrawTransport
│                      #   (errno→error translation, §3.3) + sysfs discovery
│                      #   (find_hidraw_devices(base=), find_keyboard_hidraw)  (~220 LOC)
├── device.py          # v0.7.0: AK820Pro pure state machine over a transport:
│                      #   handshake (failure counting, §3.6), send_data_chunk
│                      #   (ACK-magic validation m3), align_data_pipe,
│                      #   upload_frames_to_screen (D2/D3 guards), sync_clock  (~280 LOC)
├── screen.py          # v0.8.0: image_to_rgb565 (optimized packing), fit_image
│                      #   (m9 alpha-mask fix), load_frames_from_file,
│                      #   frame/delay buffer allocation, display layout       (~260 LOC)
├── state.py           # v0.8.0: payload-hash dedup store moved out of
│                      #   milestone_radar (fixes §2.1 layering inversion);
│                      #   AK820_STATE_PATH env override                      (~80 LOC)
├── cli.py             # v0.9.0: build_parser() + HANDLERS dispatch table (§2.3),
│                      #   one ≤40-line handler per subcommand, exit-code
│                      #   mapping main() (§3.4)                              (~290 LOC)
ak820_ctrl.py          # v0.9.0 compat shim: `from ak820.cli import main`,
                       #   re-export every public name (`# noqa: F401`)       (~60 LOC)
```

`milestone_radar.py` and `widgets.py` stay top-level in Phase 2 (rendering domain, device-free
already); their duplicated constants (m8) move into `ak820/screen.py` as `render_common` symbols.

### 10.2 Dependency rule (acyclic; enforce with a CI grep)

```text
cli       → {device, screen, state, protocol, errors} + {milestone_radar, widgets}
device    → {transport, protocol, errors}
transport → {protocol, errors}
screen    → {protocol, errors}          # imaging only — NEVER device/transport
state     → {errors}
milestone_radar, widgets → {screen}     # rendering only
FORBIDDEN: any ak820.* import of hidraw/ioctl/select outside transport.py;
           any function-level `import milestone_radar` inside protocol/device paths.
```

CI check (v0.10.0): `grep -rn "import milestone_radar" ak820/device.py ak820/protocol.py` must be
empty; `grep -rn "fcntl\|hidraw\|select" ak820/ --include='*.py' | grep -v transport.py` must be empty.

### 10.3 v0.7.0 — `protocol.py` + `transport.py` + `FakeTransport` (the testability seam)

1. Extract constants and pure framing helpers into `protocol.py`; `ak820_ctrl.py` re-imports them
   (shim semantics preserved).
2. Define the seam exactly as §5.2: `HidTransport` `Protocol` with
   `set_feature/get_feature/write_data/read_data`; `LinuxHidrawTransport` wraps real fds and does
   the errno→`DeviceDisconnectedError`/`PermissionDeniedError` translation (§3.3) — this also closes
   m1 (`__enter__` fd leak) and m2 (observable handshake).
3. `AK820Pro` becomes the fd-free state machine; `AK820ProDevice` stays as a thin factory
   (`AK820Pro(LinuxHidrawTransport(open_fds))`) so existing callers are untouched.
4. `tests/fake_transport.py`: firmware simulator per §5.3 — **must** model (a) the 64-packet
   sub-packet ring counter `[0x2000003A]`, (b) per-4096-byte-chunk ACK with `01 5A 02` magic,
   (c) injectable `drop_ack_at` and `ack_magic=False` fault knobs.
5. New offline suite `tests/test_protocol.py` implementing every §5.4 case (stream-all-chunks,
   D2 missing-ACK, m3 bad-magic, D3 over-limit, sync-clock byte sequence, align locks sub-index,
   D1 parse_mode) — zero hardware required.

### 10.4 v0.8.0 — `screen.py` + RGB565 pipeline + `state.py`

1. Move `image_to_rgb565`, `fit_image`, `load_frames_from_file`, `_build_gif_header` consumers,
   and screen layout constants into `screen.py`; adopt §4.2 `lru_cache` fonts and §4.5
   `memoryview` hashing (no `bytes(payload)` copy); fix m9 (`paste(..., copy)` alpha mask).
2. Optimize RGB565 packing: per-row `bytes` comprehension over `img.tobytes()` instead of the
   per-pixel `getpixel` double loop; keep output **byte-identical** (regression test compares
   old-vs-new on fixed fixtures, §4.4 equivalence identities).
3. Move dedup (`check_and_record_payload_hash`, `save_payload_hash`) from `milestone_radar.py`
   into `state.py` with `AK820_STATE_PATH` env override — kills the §2.1 layering inversion and
   makes dedup testable.
4. Tests: RGB565 byte-exactness on a Pillow-generated gradient, `fit_image` sizes for all three
   modes, GIF round-trip (Pillow-written 3-frame GIF), dedup round-trip with env override.

### 10.5 v0.9.0 — `cli.py` + compat shim

1. `build_parser()` returns the identical argparse tree (flag-for-flag compatible); `main(argv=None)`
   implements the §2.3 dispatch table + §3.4 exit-code contract; `--watch` loops collapse into one
   `run_watch_loop(push_fn, interval)` helper; m12 progress gated on `sys.stdout.isatty()`.
2. `ak820_ctrl.py` becomes the shim: `from ak820.cli import main` plus explicit re-exports of all
   public names (`AK820ProDevice`, `parse_mode`, `MAX_FRAMES`, `UploadIncompleteError`,
   `find_hidraw_devices`, `find_keyboard_hidraw`, `upload_frames_to_screen`, …) so pipx entry points
   and user scripts never break.
3. `pyproject.toml`: `packages = ["ak820"]` (keep `py-modules = ["ak820_ctrl"]` during transition),
   console scripts → `ak820.cli:main`; PEP 639 `license = "MIT"` (§6.4).
4. Tests: CLI smoke via `--save-preview` (device-free), exit-code assertions per §3.4 table,
   `main(["backlight","set","--mode","banana"])` → non-zero + friendly stderr.

### 10.6 v0.10.0 — CI + final polish gate

GitHub Actions matrix (3.10–3.13) running `py_compile` + `unittest` + the §10.2 dependency greps;
README exit-codes table + fonts note (§6.5); minor-defect table (m1–m12) closed or explicitly
waived with rationale in this file.

**Definition of Done per phase:** tag pushed, suite green, every new module < 300 lines, shim
imports verified (`python3 -c "import ak820_ctrl; ak820_ctrl.<name>"` for each re-exported symbol).

---

## 11. Comprehensive Re-Audit (v0.12.0, 2026-10-24)

Full architectural audit of the modular `ak820/` package (12 modules, largest `cli.py`
at 278 lines — all < 300). Live state at audit: keyboard attached, `ak820 probe` OK,
suite **155/155 → 171/171** after closing the findings below.

### 11.1 New defects found & closed in v0.12.0

| # | Finding | Evidence | Fix |
|---|---|---|---|
| **D8** | Exit-code contract silently drifted from §3.4: `PermissionDeniedError` exited **3** (indistinguishable from `DeviceNotFoundError` — a wrapper cannot tell "cable unplugged" from "udev rule missing"), and `ProtocolError` squatted on §3.4's permission code **4**. The README had been synced to the *code*, not the spec. | `errors.py` vs §3.4 table | `PermissionDeniedError` → **4** ("run setup steps"), `ProtocolError` → **7** (protocol desync, retry); README table, `DOCUMENTED_CODES`, and CLI tests re-synced; `tests/test_audit_fixes.py` |
| **D9** | §3.6 was half-implemented: `handshake_failures` only ever *incremented* (never reset on success, never acted on) — a dead control pipe still surfaced as a confusing late failure instead of exit 75. | `device.py` `handshake()` | Success resets the counter; **2 consecutive** failures raise `DeviceDisconnectedError` (exit 75); `FakeTransport(ctrl_dead=True)` fault + 4 tests |
| m5 | `DEFAULT_EVENTS_PATH` froze `$AK820_EVENTS_PATH` at import time and was never referenced | `milestone_radar.py:38` | Deleted; callers use `get_default_events_path()` |
| m6 | `save_events_config` non-atomic; `os.makedirs(dirname)` crashed on a bare relative filename | render-time config corruption | `tempfile.mkstemp` + `os.replace`; `dirname or "."` |
| m7 | Corrupt events JSON raised a raw `JSONDecodeError` traceback | `load_events_config` | Caught → `ConfigError` (exit 6) with the offending path |
| m10 | `backlight set --color banana` traceback: `"banana"` is 6 chars so the length guard passed and `int('na', 16)` leaked a bare `ValueError` | `parse_hex_color` | Friendly message at the root + friendly `sys.exit` at the CLI call site |
| m11 | `PIDS` shipped `0xFEFE` (a reverse-engineering test fixture) with zero udev coverage — discovery could match a device the rules never grant | `protocol.py` vs `udev/` | Dropped `0xFEFE`; new invariant test parses the udev rule and asserts the PID sets match |
| — | §10.2 dependency rule was spec-only — never enforced by CI or tests | no grep, no test | `TestDependencyRuleInvariant`: no `fcntl`/`select`/`os.open` outside `transport.py`; no `milestone_radar` import anywhere in the protocol seam |

### 11.2 Minor-defect table (m1–m12) — final status

**All twelve closed.** m1 ✓ v0.7.0 · m2 ✓ v0.12.0 (D9 completed §3.6) · m3 ✓ v0.7.0 ·
m4 ✓ v0.11.0 · m5 ✓ v0.12.0 · m6 ✓ v0.12.0 · m7 ✓ v0.12.0 · m8 ✓ v0.8.0 · m9 ✓ v0.8.0 ·
m10 ✓ v0.12.0 · m11 ✓ v0.12.0 · m12 ✓ v0.9.0.

### 11.3 Architecture verdict & remaining backlog

Grades moved from §0's C+/B-/B+/C/C+ to **A- / B+ / B+ / A- / A-**: the transport seam makes
the whole protocol layer hardware-free testable (171 offline tests), the error taxonomy is
now spec-exact and invariant-locked, and every documented minor defect is closed.

Remaining Tier B/C opportunities (queued in ITERATIONS.md, v0.13.0+): §3.5 alignment-skip on
known-clean pipe (~2 s saved per repeat upload — biggest remaining perf win), `logging` +
`--verbose` + single-`playerctl`-call media status (B6), shipped systemd units + `--dry-run`
flag (B7), optional numpy RGB565 fast path (§4.4), `sync_clock` DST note (§6.5), and the
`wait_for_keypress` stdin-EOF edge (returns "keypress" instantly when stdin is /dev/null).

---

## 12. v0.13.0 — Alignment-Skip on Known-Clean Pipe (§3.5/§4.1, B4 closed)

Implemented exactly per the §3.5 contract:

- `ak820/state.py`: `pipe_known_clean()` / `mark_pipe(clean)` persist a strict-boolean
  `pipe_clean` flag in the dedup state file. All reads fail **dirty** (absent/corrupt/
  truthy-but-not-`True`), and every write is atomic (m6 mkstemp+`os.replace` idiom) with
  read-modify-write so `mark_pipe` and `save_payload_hash` never clobber each other's fields.
- `ak820/actions.py`: `upload_frames_to_screen` skips `align_data_pipe` entirely when
  `pipe_known_clean()` (previous upload fully-ACKed + SAVE'd ⇒ `sub_packet_idx` provably 0,
  FINDINGS.md §4); flips the flag **dirty before streaming** so any timeout/crash re-aligns
  next run; marks clean only after the fully-ACKed SAVE. The dedup-skip path leaves the flag
  untouched (no USB traffic ⇒ pipe state unchanged).
- `FakeTransport` now **starts dirty** (`start_idx=37`) so tests prove the skip is
  state-driven, not unconditional; `start_idx=0` models a clean power-on.
- Measured against `FakeTransport` (sleep-dominated): aligned upload **1.50 s** vs
  align-skipped **0.75 s** — on real hardware the align probe costs ~1.6–2 s, so the saving
  scales accordingly. Locked by `tests/test_align_skip.py` (15 offline tests: skip, dirty
  flip, recovery, dedup interaction, wall-time margin, flag persistence + atomicity).

---

## 13. v0.14.0 — Ops & Observability (B6/B7 + §6.5 closed)

- **B6 logging + `--verbose/-v`**: `cli._configure_logging()` puts every diagnostic on
  **stderr** (INFO default, DEBUG with `-v`); stdout is reserved for machine-readable
  data — `events list --json` provably parses with stderr discarded.
- **B6/§4.3 single `playerctl` call**: one templated
  `playerctl -f '{{status}}\t{{title}}\t{{artist}}\t{{album}}' metadata` replaces four
  subprocesses; empty output falls through to the `dbus-send` MPRIS path.
- **B7 systemd units**: `systemd/ak820-radar.{service,timer}` (oneshot + 30 min timer,
  no `Restart=` so exit 3 cannot restart-loop) and `ak820-hud.service`
  (`Restart=on-failure` — pairs with exit 75 EX_TEMPFAIL so an unplug re-spawns the
  watch loop after re-seating).
- **B7 `--dry-run`**: global flag; renders the exact payload and prints digest + chunk
  count with **zero** hidraw opens and **zero** state writes. New `ak820/renderers.py`
  holds the per-command `(frames, delays, label)` renderers shared by the live upload
  path and the preview, keeping `cli.py` under the 300-line budget.
- **§6.5 `sync_clock` DST note**: documented in `device.sync_clock` — naive local
  wall-clock; the RTC (no timezone field) receives the second occurrence of a
  fall-back-repeated hour. Firmware limitation, not a host bug.
- **`wait_for_keypress` stdin-EOF edge**: `select()` reports `/dev/null` as readable;
  the stdin fallback now consumes the byte and treats EOF (`b""`) as *no keypress*.
- Locked by `tests/test_observability.py` (14 offline tests); 200/200 suite green.

---

## 14. Comprehensive Re-Audit (v0.15.0, 2026-10-24)

Full architectural audit of the 13-module `ak820/` package (largest `cli.py`
at ~290 lines — all < 300). Suite **200/200 → 214/214** after closing the
findings below.

### 14.1 New defects found & closed in v0.15.0

| # | Finding | Evidence | Fix |
|---|---|---|---|
| **D10** | `radar --date banana` (live, `--save-preview`, and `--dry-run`) leaked a raw `ValueError` traceback from `date.fromisoformat` — D4 validated `events add --date` but never the `--date` override path | `config.today_from_args` | Invalid *or impossible* (Feb 30, non-leap Feb 29) overrides raise `ConfigError` (exit 6) with a friendly message; `tests/test_reaudit_v015.py` |
| **D11** | `upload <missing>` and `--dry-run upload <missing>` leaked a raw `FileNotFoundError` traceback | `actions.upload_to_screen`, `renderers.render_upload` | New `FileMissingError` → exit **8** (user error, retry never helps — distinct from transient 75 and config 6); README table, `DOCUMENTED_CODES`, package + shim re-exports synced |

### 14.2 Audit observations (accepted designs, no code change)

- **`run` exit-code transparency**: `cmd_run` propagates the wrapped command's
  exit code verbatim, so a child exiting 3–8/75 is indistinguishable from a
  driver error to a `$?` wrapper. Accepted deliberately — a wrapper must pass
  the child's status through (like `nice`/`timeout`), and every driver failure
  happens *before* the child starts. Recorded here so the collision is a known
  contract, not a surprise.
- **B3 lint lane**: `ruff`/`mypy` are not installed on the host; the CI workflow
  keeps the `py_compile` + unittest gate (mirrored offline by
  `TestCompileInvariant`). Lint integration stays queued (Tier C).
- Dependency rule (§10.2), error taxonomy (§3.4), dedup + pipe-clean state
  machine (§3.5), and the renderers seam (B7) re-verified clean — no new
  layering violations, no bare excepts outside `state._read_state`'s deliberate
  fail-dirty catch.

### 14.3 Remaining backlog (queued Tier C)

- Optional numpy RGB565 fast path (§4.4) with byte-identical output test.
- `ruff` + `mypy` gate once the tools are installed (B3 remainder).
- ~~`events remove <target>` exits 0 when nothing matched~~ **DONE v0.18.0 (§17, D13)**.

---

## 15. Modular Package Refactoring — `cli.py` Split (v0.16.0, 2026-10-24)

`cli.py` had reached 289 lines — within budget but carrying three distinct
concerns (argparse surface, device-bound handlers, routing/exit-code `main()`).
v0.16.0 splits it three ways, all modules comfortably < 300 lines:

| Module | Lines | Single responsibility |
|---|---|---|
| `ak820/parser.py` (new) | 104 | The argparse tree: `build_parser()` flag-for-flag identical |
| `ak820/handlers.py` (new) | 114 | Device-bound `(args, dev)` handlers + `DEVICE_HANDLERS` + `TFT_COMMANDS` + `run_watch_loop` |
| `ak820/cli.py` | 106 | Offline-first routing, `--dry-run`, hidraw discovery, `_configure_logging`, §3.4 exit-code `main()` |

**Compatibility contract (locked by `tests/test_modularity_v016.py`):**

- `cli.build_parser is parser.build_parser`, `cli.run_watch_loop is
  handlers.run_watch_loop`, `cli.DEVICE_HANDLERS is handlers.DEVICE_HANDLERS`
  — re-exports are the *same objects*, so `mock.patch("ak820.cli._dispatch")`,
  `ak820.build_parser`, and the `ak820_ctrl` shim keep working untouched.
- Structural parity: every parsed subcommand except the offline pair
  (`events`, `probe`) has a `DEVICE_HANDLERS` entry — dispatch can never
  `KeyError` on a command the parser accepted.
- Fresh-interpreter import of `ak820.cli`/`parser`/`handlers`/`ak820_ctrl`
  proves no import cycle was introduced.
- §10.2 dependency rule untouched: no fd/io moved outside `transport.py`
  (the existing invariant test globs all of `ak820/*.py` and passed).

Suite **214/214 → 228/228**; live verification on the attached keyboard:
`ak820_ctrl.py probe` (rc 0, both hidraw nodes) and `--dry-run card`
(sha256 digest + chunk count, rc 0) through the unchanged shim.

## 16. Timeout Guards & Bounded I/O (v0.17.0, 2026-10-24)

RELIABILITY iteration: every wait in the protocol path now has a bound, so no
firmware misbehavior can hang the CLI (or a systemd unit) indefinitely.

| Guard | Location | Failure mode closed |
|---|---|---|
| Bounded drain (`DRAIN_MAX_READS = 64`) | `device.drain_data_acks` | a chattering/always-readable data pipe spun the unbounded drain loop forever (CLI + unit hang, no bound) |
| Whole-transfer deadline (`UPLOAD_BASE_DEADLINE_S + chunks × timeout × CHUNK_DEADLINE_FACTOR`) | `device.stream_payload` (`deadline_s`, injectable `clock`) | a device ACKing every chunk just under the 1 s per-chunk timeout kept an upload alive forever; a breach raises `UploadIncompleteError` (exit 5) *before* SAVE — digest unrecorded, actions retry unchanged |
| Write-all loop + stall cap (`MAX_WRITE_RETRIES = 64`) | `transport.LinuxHidrawTransport.write_data` | the unchecked `os.write` return silently sent partial chunks (ring-counter desync identical to a dropped packet); a wedged pipe now fails fast with `ProtocolError` (exit 7) |

Design notes:

- **No new exit codes.** The deadline reuses the exit-5 transient-retry contract
  (§3.4), so `DOCUMENTED_CODES`, the README table, and the shim stay untouched —
  a timeout is indistinguishable (and handled identically) to a dropped ACK.
- **Deterministic tests.** `clock` injection keeps every deadline test instant;
  the suite never sleeps to prove a timeout.
- FakeTransport gains `flood=True` (chattering pipe). `tests/test_timeout_guards.py`
  (15 tests) pins the drain cap, the deadline formula (48 s for a 9-chunk payload),
  abort-before-SAVE, zero-chunk abort on a negative deadline, short-write
  completion (65 -> 55 -> 25 offer slices), stall/ENODEV/EACCES translation, and
  both guards through the real `actions.upload_frames_to_screen` pipeline.

Suite **228/228 → 243/243**, zero hardware required; live `probe` (rc 0) and
`--dry-run card` (rc 0) re-verified through the unchanged shim.

## 17. Comprehensive Re-Audit (v0.18.0, 2026-10-24)

Full architectural audit of the 15-module `ak820/` package (largest
`transport.py` at 299 lines — all < 300). Every previously closed area
(dependency rule §10.2, error taxonomy §3.4, timeout guards §16, dedup/pipe
state machine §3.5, cli split §15) re-verified clean. The audit swept every
CLI-reachable path for raw-traceback leaks and silent-success anti-patterns;
three new defects found, all reproduced live before fixing. Suite
**243/243 → 263/263**.

### 17.1 New defects found & closed in v0.18.0

| # | Finding | Evidence | Fix |
|---|---|---|---|
| **D12** | `upload notes.txt`, `upload <directory>`, and corrupt/truncated images leaked raw PIL/OSError tracebacks (`UnidentifiedImageError`, `IsADirectoryError`, `OSError: image file is truncated`) with exit 1 — live and `--dry-run` paths both affected | `screen.load_frames_from_file` via `actions.upload_to_screen` / `renderers.render_upload` | New `InvalidFileError` → exit **9** (present-but-unusable sibling of D11's exit-8 *absent*): shared `actions.load_frames_guarded` translates loader-domain failures once, so live and dry-run fail identically; README table, `DOCUMENTED_CODES`, package + shim re-exports synced in the same commit |
| **D13** | `events remove <no-match>` printed "Removed 0 event(s)." and exited **0** — a typo looked like success, and the config file was rewritten atomically for nothing (queued Tier C item now closed) | `config.cmd_events` remove branch | No-match raises `ConfigError` (exit 6) with a "check `events list`" hint and the file is left byte-identical; matching remove (by id or case-insensitive title) unchanged |
| **D14** | `hud/media --save-preview <path>` into a missing parent dir leaked `FileNotFoundError`; `radar --save-preview <existing-file>` leaked `FileExistsError`; a directory as a file target leaked PIL's `IsADirectoryError` | `config.cmd_hud_preview` / `cmd_media_preview` / `cmd_radar_preview` | Missing parents auto-created (mirrors radar's directory semantics); unusable targets (existing dir as file target, existing file as dir target) raise `InvalidFileError` (exit 9) |
| **m13** | Version drift: `ak820.__version__` said `0.9.0` while `pyproject.toml` said `1.0.0` — neither matched the `v0.17.0` tag series | `__init__.py`, `pyproject.toml` | Both set to `0.18.0`; `TestVersionSync` locks them equal forever |

### 17.2 Audit observations (accepted designs, no code change)

- **Guard placement in `actions.py`, not `screen.py`**: file-operand errors are
  a CLI-contract concern, not an imaging one — D11's `FileMissingError` already
  lives in the actions layer, so D12's translation belongs beside it and
  `screen.py` stays a pure PIL domain. One shared `load_frames_guarded` keeps
  the translation single-sourced for both call sites; `renderers` already
  imports `actions`, so no cycle is introduced.
- **Exit-9 taxonomy choice**: 9 deliberately neighbors 8 so wrappers read
  `case $? in 8|9) fix the path;;` — absent vs unusable are distinguishable
  when it matters, unified when it doesn't. No existing code renumbered.
- `backlight` with no sub-action still applies default lighting (action
  defaults to `set` with mode 1) — pre-existing documented behavior, left as
  is; argparse `choices` guard `--preset`/`--mode`/`--color` paths (m10).
- `widgets` subprocess probes (playerctl, sensors) all fail soft inside
  `except Exception` with placeholder values — verified intentional (§8 spirit:
  HUD must render with no media server).

### 17.3 Remaining backlog (queued Tier C)

- Optional numpy RGB565 fast path (§4.4) with byte-identical output test.
- `ruff` + `mypy` gate once the tools are installed (B3 remainder).
- `probe --json` for AI-tool consumption (matches `events list --json`).
- Events-config schema validation (`jsonschema`) if the file grows more knobs.

---

## 18. Modular Package Refactoring — `transport.py` Split: `discovery.py` (v0.19.0, 2026-10-24)

`transport.py` had grown to 299 lines — one line under the §10 budget — by
mixing two distinct responsibilities: *which* hidraw nodes belong to the
keyboard (sysfs scanning) and *how* to talk over them (fd/ioctl/select I/O).
v0.19.0 extracts the scanning half into `ak820/discovery.py` (99 lines):

- **Moved**: `_read_hid_identity`, `_parse_usage_page`, `find_hidraw_devices`,
  `find_keyboard_hidraw` — byte-identical bodies, `base=` testability intact.
- **Kept in `transport.py`** (229 lines): `HidTransport`, `LinuxHidrawTransport`,
  `wait_for_keypress` (polls fds — fd/io stays here), `AK820ProDevice`.
- **Identity re-exports**: `transport` re-imports all four names from
  `discovery`, so `ak820.transport.find_hidraw_devices`, the `ak820_ctrl`
  shim, `ak820.*`, and every `mock.patch.object(transport, ...)` target in
  the existing suite keep working unchanged (locked by
  `tests/test_discovery_v019.py`).

### 18.1 §10.2 dependency rule — refined, not relaxed

The fd/io seam is unchanged: `fcntl`, `select`, and raw fd opens (`os.open`)
live **only** in `transport.py`, still grep-enforced across every other
module. What moved is the *sysfs path string* (`/sys/class/hidraw` default)
and plain attribute-file reads (`open()`/`os.listdir`) — no fd/io — which
now live in `discovery.py`. The CI grep test (`test_protocol.py`) therefore
splits its token list: fd/io tokens stay transport-only; sysfs path strings
are allowed in `transport.py` **or** `discovery.py`. `discovery.py` is
deliberately token-clean otherwise (docstrings included) so the grep stays
mechanical.

Dependency graph addition (acyclic): `discovery → {protocol}`.
`transport → {discovery, protocol, errors}`; nothing imports `transport`
from `discovery`, so no cycle (fresh-interpreter import test included).

### 18.2 Verification

`tests/test_discovery_v019.py` (13 offline tests): re-export identity across
`transport`/`ak820`/`ak820_ctrl`/`cli`, fd/io seam preserved in both
directions (discovery clean, transport still owns the primitives),
mock-patch surface (`transport.find_keyboard_hidraw` still steers
`wait_for_keypress`), fake-sysfs functional parity, missing-base tolerance,
identity/usage-page helper behavior, <300-line budget, fresh-interpreter
no-cycle import. Suite: 263 → 278 tests, all green; live `probe` +
`--dry-run radar` through the `ak820_ctrl` shim on real hardware.

---

## 19. Disconnect Recovery & Open-Path Robustness (v0.20.0, 2026-10-24)

Reliability audit of the two seams where a physical unplug or a missing udev
rule could still escape the §3.4 exit-code contract as a raw traceback:

### 19.1 New defects found & closed in v0.20.0

- **D15 — `AK820ProDevice.__enter__` leaked raw OSError on open.** Sysfs
  discovery and `os.open` are not atomic: unplug in between raises
  `FileNotFoundError` (ENOENT) or `OSError(ENXIO)` (driver detached), and a
  missing udev rule raises `PermissionError` at *open* time — all exited 1
  with a traceback while the taxonomy already had codes for exactly these
  states. `_translate` now maps ENOENT/ENXIO alongside ENODEV to
  `DeviceDisconnectedError` (75, re-seat and re-run), and `__enter__` routes
  every open failure through it (EACCES/EPERM → `PermissionDeniedError`, 4).
  The m1 fd-leak guarantee is preserved and now *locked by test*: when the
  second open fails, the first fd is closed exactly once.
- **D16 — `handshake()` swallowed `PermissionDeniedError` as a transient
  blip.** The §3.6 two-strike counter treats failures as flaky-cable noise,
  but a rejected control pipe (udev rule missing) never recovers: strike two
  misattributed it as a dead pipe (75, "re-seat") instead of pointing at the
  real fix (4, "install the udev rule"). Permission errors now propagate
  immediately without touching the counter; the OSError/ENODEV two-strike
  model is unchanged.

### 19.2 Disconnect semantics (locked by tests)

- Mid-stream unplug (`FakeTransport(disconnect_at=N)`): `DeviceDisconnectedError`
  (75) propagates — it is **not** retried like exit-5 `UploadIncompleteError`
  (retrying an unplug wastes the run; systemd `Restart=on-failure` re-runs
  after re-seating). No SAVE packet reaches a dead device, the digest stays
  unrecorded (next run retries, never skips), and the §3.5 pipe flag stays
  dirty (next run re-aligns).
- Disconnect during alignment propagates as 75 through the same contract.
- CLI end-to-end: vanished node → exit 75, permission denied → exit 4,
  never a traceback.

### 19.3 Verification

`tests/test_disconnect_recovery_v020.py` (22 offline tests, zero hardware):
errno-translation table (incl. EIO pass-through regression), open-path typed
exits + m1 close-once proof, handshake fast-fail vs two-strike parity,
mid-stream/mid-align disconnect through the real upload pipeline with state
isolation (no digest recorded, pipe dirty), no-retry-on-75 proof via a
counting `stream_payload` patch, and CLI-level exit probes. `FakeTransport`
gains a `perm_denied=True` fault (missing-udev-rule model). Suite: 278 → 300
tests, all green; live `probe` + `--dry-run radar` through the `ak820_ctrl`
shim on real hardware.

## 20. Comprehensive Re-Audit (v0.21.0, 2026-10-24)

Full-codebase audit of the operand/interrupt/retry seams: every CLI path was
walked for raw tracebacks, undocumented exit codes, and invariant bypasses.

### 20.1 New defects found & closed in v0.21.0

- **D17 — `run <missing-command>` leaked a raw `FileNotFoundError` traceback
  (exit 1).** `subprocess.run` raises at exec time, outside the §3.4 taxonomy.
  The command operand is a file operand: absent → `FileMissingError` (8, fix
  the path), present-but-not-executable → `InvalidFileError` (9, `chmod +x`).
  Deliberately *not* 4: exit 4 must keep meaning "the device node rejected
  us" (udev rule) so wrappers keep that distinction on `$?` alone.
- **D18 — Ctrl+C dumped a raw traceback.** Upload streaming, `wait_for_keypress`,
  and the `run` child all sit outside any KeyboardInterrupt handler (only the
  `--watch` loop and the demo had local catches). `main()` now catches
  `KeyboardInterrupt` → friendly stderr line + exit **130** (128+SIGINT, the
  shell convention — same status Python's default produced, minus the
  traceback). Mid-upload an interrupt is always safe to re-run: the §3.5 pipe
  flag is pessimistically dirty before the first chunk and the digest is only
  recorded after a fully-ACKed SAVE. The `--watch` loop keeps its local clean
  stop (exit 0) — that is intentional UX, now regression-locked.
- **D19 — the exit-5 full retry bypassed the m4 alignment invariant.**
  `upload_frames_to_screen`'s retry path called `align_data_pipe()` and
  *discarded* the result, streaming into a provably un-aligned pipe — the
  exact desync state m4 exists to prevent. Both call sites now share one
  `_ensure_aligned(dev)` helper (retry once, then `ProtocolError` exit 7);
  a structural test locks that no call site touches `dev.align_data_pipe`
  outside the helper.

### 20.2 Minor defects closed

- **m14** — a `run` child killed by signal N exited `(-N) & 0xFF` (247 for
  SIGKILL); now the shell convention `128+N` (137), extending §14.2's
  run-exit transparency to signal deaths.
- **m15** — `run` with no operand exited the undocumented generic 1; now
  documented usage code 2 (same class as argparse).
- **m16** — `backlight set --mode/--color <bad>` used `sys.exit(str)` →
  undocumented 1; now friendly stderr + documented usage code 2. The README
  Exit Codes table row 2 covers invalid flag values; rows 8/9 cover the `run`
  operand cases; a new row documents 130.

### 20.3 Accepted designs (recorded, not defects)

- `--dry-run` scopes to *USB* traffic only: offline commands (`events` CRUD,
  `--save-preview` file writes) still act, matching the flag's help text.
- Dedup keys on the payload digest, not the label: an identical payload under
  a new label is correctly skipped — the screen genuinely already shows it.
- `set_status_backlight`'s unknown-status branch is unreachable from the CLI
  (argparse `choices` gate) — kept as defense for library callers.

### 20.4 Verification

`tests/test_reaudit_v021.py` (16 offline tests, zero hardware): `run` operand
typed exits 8/9 incl. CLI end-to-end and the 9-not-4 distinction, signal →
137 / normal → passthrough, empty operand → 2, KeyboardInterrupt → 130 with
friendly stderr + watch-loop clean-stop regression, retry-align abort proof
(stream called exactly once, align called 3×), happy drop-once retry still
completes end-to-end, structural align-centralization lock, backlight usage
exits 2. Two pre-existing tests (m10/banana) re-anchored to the new exit 2.
Suite: 300 → 316 tests, all green; live `probe` (real hardware), `--dry-run
radar`, and empty-`run` exit-2 probe through the `ak820_ctrl` shim.

## 21. Comprehensive Re-Audit (v0.24.0, 2026-10-24)

Full-codebase re-audit walking every error, operand, and signal path for raw
tracebacks, undocumented exits, and contract drift — with every suspect first
reproduced live through the `ak820_ctrl` shim before a line of code changed.

### 21.1 New defects found & closed in v0.24.0

- **D20 — a *successful* upload exited 1.** `upload_frames_to_screen` returns
  `True` (uploaded) / `False` (dedup skip); `main()` ended with
  `return result or 0`, which hands `True` through verbatim — and because
  `bool` is an `int` subclass, the console-script wrapper's `sys.exit(True)`
  exits **1**. The §3.4 contract (README row 0) says 0 is the only success
  status, so the more-successful outcome (a real flash) exited nonzero while
  the dedup skip exited 0. `main()` now maps any bool handler result to 0;
  the shim's `__main__` also switched to `sys.exit(main())` so script and
  entry-point share one contract. Test-locked end-to-end: a FakeTransport
  upload through `cli.main()` returns int 0 (assertNotIsInstance bool) and
  `sys.exit(rc)` raises `SystemExit(0)`.
- **D21 — bare `ak820 backlight` dumped an `AttributeError` traceback.**
  `dispatch_backlight`'s `args.bl_action or "set"` fallback reads `preset`,
  `color`, … — attributes argparse only defines once a sub-action parses
  them. The sub-action is now `required=True`: friendly argparse usage error,
  documented exit 2 (m16 class), and the crash path is unreachable.
- **D22 — events-config shape violations leaked raw tracebacks.** m7 covered
  corrupt JSON only. A config that is a *directory* or unreadable (OSError),
  valid JSON whose top level is not an object (`AttributeError` on `cfg.get`),
  whose `events` is not a list, or whose entry is not an object all crashed
  with exit 1 — as did an unwritable write target (`events add` into a
  read-only dir). `load_events_config` now validates the shape at the single
  load choke point (every consumer may assume dict-of-list-of-dicts) and both
  load and save translate OSError → `ConfigError` (6): fix the config, the
  D4/D5/m7 class.
- **D23 — `--save-preview` write failures leaked raw tracebacks.** D14 typed
  *target shape* errors (9) but not *write* errors: an unwritable parent
  (makedirs EACCES), an unwritable target (PIL save EACCES/ENOSPC), or an
  unknown file extension (PIL `ValueError`) all exited 1 with a traceback.
  `_prepare_preview_file` and a new `_save_preview` wrapper now map every
  such failure to `InvalidFileError` (9) — same unusable-target class as
  D12/D14 — across `radar`, `hud`, `stats`, and `media` previews.
- **D24 — `--seconds/--watch/--dwell nan|inf` crashed or hung.** `type=float`
  accepts `nan`/`inf`: `radar --seconds nan` died in `round(NaN)`
  (ValueError), `--seconds inf` in `round(inf)` (OverflowError), and
  `hud --watch inf` would sleep forever. A `finite_float` argparse type
  rejects non-finite values with the friendly usage error, exit 2 (m16
  precedent); finite values (incl. 0 and negatives, which clamp) parse
  exactly as before.

### 21.2 Minor defects closed

- **m17** — `wait_for_keypress`'s stdin fallback crashed on a *closed* stdin
  (select EBADF on a closed fd 0, ValueError on a closed file object); the
  whole stdin interaction is one guarded block now — no usable stdin means no
  keypress can arrive: `False`.
- **m19** — a hand-edited `backlight.idle_color` in events.json crashed the
  *restore-idle* step (after the keypress wait) with a raw `ValueError` from
  `parse_hex_color`; now `ConfigError` (6), the same fix-the-config class.

### 21.3 Accepted designs & queued observations

- `--dry-run` + `--save-preview` together: the preview acts, the dry-run
  notice is skipped — preview is the more specific intent (unchanged from
  §20.3's dry-run scoping).
- **m18 (closed v0.26.0)** — non-string *field values* inside otherwise-valid
  event objects (`"title": 123` → `.lower()` AttributeError; `note` with a stray
  `{placeholder}` → `format` KeyError; `idle_mode` as a string). Shape is
  locked (D22); value-type coercion shipped in `ak820/configvalues.py`, called
  at the same `load_events_config` choke point: JSON scalars coerce losslessly
  (numbers/bools → text, numeric strings/integral floats → int for the idle
  backlight fields), `null`/list/object values and `note` placeholders other
  than `{years}` → `ConfigError` (6) naming the `events[i].field` location.
- **m20 (closed v0.26.0)** — `events add` rewrites the config even when the
  added event is byte-identical to an existing entry (id/title dedup already
  prevents duplicates, so this is cosmetic). v0.26.0: the write is skipped when
  the rebuilt event list equals the loaded one — "already up to date" line,
  file bytes and mtime untouched.

### 21.4 Verification

`tests/test_reaudit_v024.py` (31 offline tests, zero hardware): D20 int-0 +
not-a-bool + console-wrapper `SystemExit(0)` + dedup-skip regression; D21
exit 2 + subaction parity + `backlight list` unchanged; D22 list/string/
non-list-events/bad-entry/directory/unreadable/unwritable → 6 incl. m7 and
happy-path regressions; D23 unwritable parent/target + unknown extension → 9
incl. D14b and happy-path regressions; D24 nan/inf/±inf on all three flags →
2 + finite parity; m17 closed-file-object and closed-fd stdin; m19 direct +
through `main()`. Suite: 342 → 373 tests, all green (5 numpy-optional skips
on numpy-free system python); `scripts/lint.sh` GREEN; live probes through
the shim: shape/dir config → 6, bare `backlight` → 2, `--seconds nan` → 2,
unwritable preview → 9, `probe` → 0 on real hardware, version 0.24.0.
## 22. Architecture & Modularity — Top-Level Monolith Decomposition (v0.25.0, 2026-10-24)

The `ak820/` package had been under the §10 budget since v0.16.0, but the two
top-level rendering modules had drifted far past it while mixing three
responsibilities each: `milestone_radar.py` (510 lines) held the events-config
domain, the urgency palette, and the card renderers; `widgets.py` (359 lines)
held sysfs/proc + D-Bus observation and the HUD/media renderers. Worse, the
package reached *back out* through them: `cli.main()` imported `milestone_radar`
just to catch `ConfigError`, and `backlight.py` (lighting domain) persisted its
idle preset through the rendering monolith. v0.25.0 splits both monoliths into
single-responsibility package modules and reverses that arrow for good:

| New module | Lines | Responsibility | Imports |
|:---|:---|:---|:---|
| `ak820/events.py` | 230 | events-config domain: path resolution, D4/D5 dates, m7/D22 shape, D7 thresholds, countdown rows | stdlib + `errors` |
| `ak820/palette.py` | 77 | THEMES urgency palette (pure data, one entry per `theme_key`) | `typing` only |
| `ak820/cards.py` | 263 | hero/board/custom 128x128 renderers (pure functions of row dicts) | PIL + `screen`, `palette` |
| `ak820/telemetry.py` | 243 | sysfs/proc sampling + playerctl/MPRIS probes (best-effort, never raises) | stdlib only |
| `ak820/hud.py` | 197 | HUD + Now-Playing renderers (stats/media injectable) | PIL + `screen` |

- **Shims**: `milestone_radar.py` (60 lines) and `widgets.py` (33 lines) are now
  pure identity re-exports (`mr.load_events_config is ak820.events.load_events_config`),
  so every historical `milestone_radar.*` / `widgets.*` import, user script, and
  systemd timer keeps working (locked by `tests/test_modularity_v025.py`).
- **Package self-containment**: no `ak820/` module imports the top-level shims
  anymore — the dependency arrow points one way (shims → package), grep-locked
  across every package module. `cli.main()` lost its `milestone_radar` import
  and its second except branch.
- **`ConfigError` re-parented**: moved from `milestone_radar` into `errors.py`
  under `AK820Error` (exit 6 unchanged). `main()`'s single `except AK820Error`
  branch covers it, and the §3.4 invariant test that maps *every* error class
  to its exit code now covers `ConfigError` automatically.
- **Domain/rendering seam**: `events.py`/`telemetry.py`/`palette.py` are PIL-free
  (config and probe paths never load Pillow); `cards.py` never imports `events`
  (renderers consume plain row dicts); `hud.py` keeps its telemetry import
  function-level so both renderers stay injectable and offline-testable.
- **Probe seam move for tests**: `get_media_status` now lives in `telemetry`, so
  the canonical patch target is `ak820.telemetry.shutil` / `.subprocess`
  (`test_observability.py` updated; patching `widgets.shutil` now fails loudly
  instead of silently no-op'ing).

### 22.1 Behavior neutrality — proven, not asserted

A 13-fixture render manifest (hero incl. empty/single states, board incl.
empty, custom incl. bogus theme, HUD incl. hot thresholds, media playing/paused/
idle + the computed countdown rows) was captured **before** the split and
re-verified **after**: all PNG sha256 digests byte-identical. Moving the probe
code into `ak820/` brought it inside the lint gate's scope for the first time:
24 new ruff findings were fixed behavior-neutrally (typing modernization,
`contextlib.suppress` for the unlink guard, `l`→`ln` renames) or justified
inline with the `state.py` noqa idiom (best-effort probe contract on
BLE001/S110/S112; PATH-probing optional tools on S603/S607).

### 22.2 Verification

`tests/test_modularity_v025.py` (18 offline tests, zero hardware): shim identity
across 19 `milestone_radar` + 4 `widgets` names, package-shim import grep ban,
PIL-free domain modules, `cards`↛`events` and `hud` function-level telemetry
seams, `ConfigError` re-parenting + exit-6 mapping through `main()`, <300-line
budget for the five new modules **and** the three top-level shims, fresh-
interpreter no-cycle import of every new module, and byte-level render parity
through both surfaces. Suite: 373 → 391 tests, all green (5 numpy-optional
skips); `scripts/lint.sh` GREEN (ruff + format + mypy, 21 sources). Live probes
through the shim: `events list/--json/add/remove` via `$AK820_EVENTS_PATH`,
`--dry-run radar|card`, `radar/hud/media --save-preview`, corrupt config → 6,
no-match remove → 6, bad `--date` → 6, bare `backlight` → 2. Found while
probing: `events` rejects `--config` while `radar` accepts it → queued v0.26.0.

## 23. Config-Override Parity Completed on the Device-Bound Surface (v0.27.0, 2026-10-25)

The v0.26.0 invariant — *every events-config consumer accepts the same
`--config` override* — held for the offline surface (`radar`, `events`) but not
for the device-bound one: `backlight set/off --save` persisted the idle preset
only through `$AK820_EVENTS_PATH`'s target, and the `status`/`run` keypress
idle-restore read it the same way — no flag existed. v0.27.0 threads an
optional `config_path` through `get_idle_backlight_cfg`/
`save_idle_backlight_cfg`/`apply_idle_backlight`/`set_status_backlight` and
exposes `--config` on `backlight` (parent + SUPPRESS sub-action positions,
sub-action wins — the v0.26.0 argparse pattern), `status`, and `run`.
`backlight demo/list` parse the flag uniformly but never touch the config
(documented parse-and-ignore, so scripts can pass one `$CONFIG` everywhere).

`events add --note` closes the last JSON-only event field, validated through
the new public `configvalues.validate_note` — the single home of the
`{years}`-only rule, shared with the load-time coercion so the CLI and the
loader can never drift (both `ConfigError` 6). A re-add without `--note`
preserves the replaced entry's `note` instead of silently dropping it, and
m20's no-op skip extends over the preserved note (byte + mtime stable).

**Library trust boundary (decision):** `compute_upcoming_events` keeps *no*
second type guard — the D22/m18 choke point at `load_events_config` is the
invariant's only home, and a duplicated guard in the render hot path would
only drift. Hand-injecting callers (test seams, embedding tools) own their
contract; the boundary is documented in the docstring and locked by test.

`SHOWCASE_PRESETS` moved to new pure-data `ak820/presets.py` (identity
re-export through `backlight`, shim/package surfaces unchanged) to keep
`backlight.py` under the §10 budget after the threading — 221 lines.

### 23.1 Verification

`tests/test_device_config_parity_v027.py` (36 offline tests, zero hardware):
`--config` parsing in both positions + precedence + None defaults across
backlight/status/run; `config_path` beating `$AK820_EVENTS_PATH` in
get/save/apply incl. a wire-level MODE_DATA byte proof and the m19 regression
through the new seam; handler threading spies (`cmd_backlight`/`cmd_status`/
`cmd_run`); `--note` write/validate-6/file-untouched/preserve/replace/no-op-
mtime/render; shared-rule identity (same note rejected by loader AND CLI with
6); trust-boundary docstring lock; presets re-export identity + line budget.
Suite: 422 → 458 tests, all green (5 numpy-optional skips); `scripts/lint.sh`
GREEN. Live probes on real hardware through the shim: `backlight set --preset
warm-white --save --config F` wrote the idle block into F (legacy default
untouched), `status idle --config F` / `run --config F -- true` → 0, `--note`
add → 0 + render "kept 0", no-op re-add mtime-stable with note preserved,
`--note "{years} in {place}"` → 6, version 0.27.0. Found while probing:
`backlight.idle_rainbow` is in no `configvalues` field list — a hand-edited
`"false"` string is truthy in `set_backlight`'s byte assembly (rainbow ON
when the user meant OFF) → queued v0.28.0 (m21).

---

## 24. m21 — `idle_rainbow` Bool Coercion (v0.28.0, 2026-10-25)

§23.1's live probe found the one idle field missing from every `configvalues`
list: a hand-edited `"idle_rainbow": "false"` is a non-empty string — truthy
in `set_backlight`'s `1 if rainbow else 0` byte assembly — rainbow turned ON
when the user meant OFF (repro: MODE_DATA[8] == 1) — and `null` silently
meant OFF instead of a fix-the-config error.

Fix at the same m18/D22 choke point (`load_events_config` →
`coerce_config_values`): `_IDLE_BOOL_FIELDS = ("idle_rainbow",)` coerced by
`_as_bool` — bools pass through, `0`/`1` ints and `"true"/"false"/"1"/"0"`
text (case/whitespace-insensitive, mirroring `_as_int`'s strip) coerce
losslessly; everything else (`"yes"`, `2`, `-1`, `1.0`, `null`, list/object)
is `ConfigError` (6) naming `backlight.idle_rainbow`. Rejecting `1.0` is
deliberate: the lossless spellings are bool, int 0/1 and the four texts —
close-but-not-lossless values are named, never guessed, matching the
taxonomy's stance on `null`.

### 24.1 Verification

`tests/test_idle_rainbow_bool_v028.py` (11 offline tests, zero hardware):
wire-byte proof (MODE_DATA[8]) through the consumer seam
`apply_idle_backlight(dev, path)` for every accepted spelling incl. the m21
repro form (`"false"` → byte 0); exit 6 naming `backlight.idle_rainbow`
through the load choke point `events list --config` for every rejected value
(JSON-kind naming proven: "got null"); `ConfigError` at the consumer seam;
coerced bool persisted by `events add` (the hand-edited string never survives
a round-trip); absent-field default stays `False`. Anti-recurrence invariant:
every `bl.get(...)` key in `get_idle_backlight_cfg` must appear in one of the
three coercion field lists — m21's root cause (a field in no list) now fails
the suite mechanically. Suite: 458 → 469 tests, all green (5 numpy-optional
skips); `scripts/lint.sh` GREEN. Live probes on real hardware through the
shim: `events list --config F` / `status idle --config F` with
`"idle_rainbow": "false"` → 0 (coerced OFF, rainbow byte 0), with `"yes"` →
6 naming the field on both surfaces, version 0.28.0.

---

## 25. Comprehensive Re-Audit — v0.25–v0.28 Surface Growth (v0.29.0, 2026-10-25)

Cadence-5 re-audit (last full: §21/v0.24.0): walked every error, operand and
signal path across the surface the last four iterations grew — the monolith
split shims, `--config` on every consumer, `--note`, `presets.py`, and the
bool coercion — reproducing every suspect live (real hardware + shim) before
touching code. Parity gates re-verified green: README Exit Codes ↔
`errors.py` ↔ `DOCUMENTED_CODES`, §10.2 dependency rule, <300-line budget
(24 package modules + 3 shims), shim identity.

**D25 — non-object `backlight` section → raw AttributeError (exit 1).** D22
shape-checked the `events` list and its entries but never the `backlight`
section itself. A hand-edited `"backlight": "warm"` (or list/number/null)
passed the loader untouched and crashed every idle restore —
`get_idle_backlight_cfg`'s `bl.get` — with a raw AttributeError; live-proven
on real hardware via `status idle --config F`. Now rejected at the single
choke point: a present `backlight` must be an object, else `ConfigError` (6)
naming the section and the JSON kind (`got null`, never `got NoneType`).

**D26 — `"events": null` → raw TypeError (exit 1).** D22's guard read
`if events is not None:` — null slipped through as "absent", but every
consumer reads `cfg.get("events", [])`, and the default only covers an
ABSENT key: the null reached the countdown/add loops and crashed `events
list`, `events add`, and every radar render with `TypeError: 'NoneType'
object is not iterable` (live-proven on all three seams). The guard is now
`"events" in cfg and not isinstance(...)` — present must be a list, null is
named, never guessed into `[]`. Missing sections stay fine (defaults apply).

**m22 — `--dry-run probe` swallowed the interface report.** probe opens no
hidraw node and sends zero USB bytes, yet the dry-run block intercepted it
with "nothing to preview" — the exact output a dry-run user wants. probe now
routes before the dry-run block; non-render commands keep the documented
message.

**Doc drift found by the walk:** the README Architecture table still listed
the pre-v0.16 eight-module world — synced to all 24 modules + 3 shims; the
Testing section's "140+ tests" count refreshed. `configvalues.kind` promoted
to public so the section-shape checks share m18/m21's JSON-kind vocabulary
(one home, no drift back to `type(...).__name__`).

### 25.1 Verification

`tests/test_reaudit_v029.py` (25 offline tests, zero hardware): D26 through
load + CLI list/add + dry-run radar (exit 6, file byte-stable); D25 for every
wrong JSON kind through load, the `get_idle_backlight_cfg`/
`apply_idle_backlight`/`set_status_backlight` consumer seams (ConfigError,
device never touched) and the full CLI `status idle` seam with mocked
discovery+device (exit 6); absent/empty sections keep defaults (regression);
shared-vocabulary lock (`configvalues.kind` used, no `type(` drift);
anti-recurrence AST invariant — no None-comparison may ever return to
`load_events_config` (the D26 pattern: null-as-absent), prose exempt;
table-driven per-kind rejection for both sections; m22 dry-run probe
passthrough incl. headless NOT FOUND + dry-run block intact for renderers.
Suite: 469 → 494 tests, all green (5 numpy-optional skips);
`scripts/lint.sh` GREEN. Live probes on real hardware through the shim:
`events list --config` with `events: null` → 6 "got null"; `status idle
--config` with `"backlight": "warm"` / `null` → 6 naming the section (was
AttributeError exit 1); `--dry-run probe` → interface report exit 0; valid
`events.example.json` list unchanged; version 0.29.0.

Queued for v0.30.0: m23 (`idle_mode` config value unbounded — CLI bounds
0..19 via `parse_mode`, hand-edited 255 wraps silently in `mode & 0xFF`),
m24 (`events remove` id match case-sensitive vs title case-insensitive),
m25 (`events add` re-add drops unknown hand-added entry fields), m26
(negative `--dwell`/`--watch` accepted by `finite_float`).
