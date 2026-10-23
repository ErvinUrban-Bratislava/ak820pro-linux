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

### 4.1 Skip redundant alignment (§3.5) — saves ~2 s per repeat upload. Biggest single win.

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
| B4 | Alignment-skip on clean pipe (§3.5, ~2 s/upload saved) |
| B5 | Shared `render_common` (fonts/`_load_font` lru_cache/screen constants) |
| B6 | Single `playerctl` call; progress gated on tty; `logging` + `--verbose` |
| B7 | Ship systemd units; atomic config writes (m6); `--dry-run` flag |
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
