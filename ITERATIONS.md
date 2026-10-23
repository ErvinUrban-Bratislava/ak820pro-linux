# AK820 Pro Linux — Evolution Ledger (`ITERATIONS.md`)

## Summary Table

| Version | Date | Status | Focus | Verified By |
|:---|:---|:---|:---|:---|
| `v0.0.0` | 2026-10-23 | Baseline | Initial open-source release + architectural review (`REVIEW.md`) | `python3 -m unittest` 8/8 |
| `v0.1.0` | 2026-10-23 | Done | Fix D1: `parse_mode()` replaces eager `int(args.mode)` crash in `backlight set --mode` (case-insensitive names, 0..19 bounds, friendly errors) | `python3 -m unittest` 14/14 |
| `v0.2.0` | 2026-10-23 | Done | Fix D2: `UploadIncompleteError` (exit 5) — dropped chunk ACKs now abort before SAVE, digest recorded only on full transfer | `python3 -m unittest` 20/20 |
| `v0.3.0` | 2026-10-23 | Done | Fix D3: `MAX_FRAMES = 255` guard — `upload_frames_to_screen` rejects >255 frames before any USB packet; hybrid radar frame total clamped (no silent & 0xFF wrap) | `python3 -m unittest` 25/25 |
| `v0.4.0` | 2026-10-23 | Done | Fix D4/D5: `parse_event_date()` validates at `events add` (exit 6 `ConfigError`); `_safe_date()` falls back to Feb 28 only for (2,29) on non-leap years, rejects all other invalid components | `python3 -m unittest` 50/50 |
| `v0.5.0` | 2026-10-23 | Done | Fix D6/D7: `wait_for_keypress()` uses dynamic sysfs VID/PID scan (`find_keyboard_hidraw`, `base=` testable); `compute_upcoming_events()` honors `urgent_days`/`warn_days`/`soon_days` config thresholds; example config aligned | `python3 -m unittest` 61/61 |
| `v0.6.0` | 2026-10-23 | Done | Autonomous re-audit: REVIEW.md §9 defect resolution audit — all 7 defects (D1–D7) live-verified RESOLVED; REVIEW.md §10 detailed Phase 2 architecture spec (`ak820/` package, `protocol`/`transport`/`device`/`screen`/`state`/`cli`, dependency rules, per-phase DoD, <300-line modules) | `python3 -m unittest` 61/61 + live probes |

---

## Priority Queue (Overnight Autonomous Evolution)

- [x] **`v0.1.0`** — Fix Defect D1: `backlight set --mode <name>` eager int evaluation crash:
  - In `ak820_ctrl.py`: replace `LIGHT_MODES.get(args.mode.lower(), int(args.mode))` with a robust `parse_mode(value: str) -> int` function.
  - Support mode names case-insensitively, validate numeric bounds (`0..19`), and raise friendly error messages rather than unhandled `ValueError`.
  - Add unit tests in `tests/test_ctrl.py` covering all named modes ("breath", "rainbow", "static", etc.), valid numbers, and invalid inputs.
  - Verify `python3 -m unittest discover -s tests` passes 100%.

- [x] **`v0.2.0`** — Fix Defect D2: `upload_frames_to_screen` false success reporting on missing ACKs:
  - In `ak820_ctrl.py`: define `class UploadIncompleteError(Exception)` (exit code 5).
  - In `upload_frames_to_screen`: verify received ACKs match `total_chunks`. If `acks != total_chunks`, raise `UploadIncompleteError` and abort upload rather than printing "Upload and flash save complete!" and exiting 0.
  - Save digest hash only upon complete successful transfer.
  - Add unit test verifying `UploadIncompleteError` is raised when chunk ACKs drop.
  - Verify `python3 -m unittest discover -s tests` passes 100%.

- [x] **`v0.3.0`** — Fix Defect D3: 255-frame firmware limit guard:
  - Define `MAX_FRAMES = 255` constant in `ak820_ctrl.py`.
  - In `upload_frames_to_screen`: enforce `len(frames) <= MAX_FRAMES` before sending any USB packets.
  - In `build_radar_frames`: clamp total generated frames (bongo loops + event slides) so hybrid mode cannot silently wrap & 0xFF.
  - Add unit tests asserting `ValueError` on 256 frames and valid execution on <= 255 frames.
  - Verify `python3 -m unittest discover -s tests` passes 100%.

- [x] **`v0.4.0`** — Fix Defects D4 & D5: Event date validation and safe leap-year handling:
  - In `milestone_radar.py`: add `parse_event_date(raw: str)` validating `YYYY-MM-DD` or `MM-DD` at input time with informative errors.
  - Implement `_safe_date(year, month, day)` that specifically falls back to Feb 28 ONLY on (month, day) == (2, 29) on non-leap years, while rejecting invalid months/days.
  - Add unit tests in `tests/test_radar.py` testing invalid dates, leap years, non-leap Feb 29, and negative offsets.
  - Verify `python3 -m unittest discover -s tests` passes 100%.

- [x] **`v0.5.0`** — Fix Defects D6 & D7: Dynamic device path scanning & configuration hygiene:
  - In `ak820_ctrl.py`: replace hardcoded `/dev/hidraw0` and `/dev/hidraw1` in `wait_for_keypress()` with dynamic sysfs device scanning matching VID `0x0c45` / PID `0x800a`.
  - In `events.example.json` and `milestone_radar.py`: clean up dead configuration keys (`warn_days`, `urgent_days`) or implement their threshold warnings.
  - Add unit tests verifying sysfs discovery and config parsing.
  - Verify `python3 -m unittest discover -s tests` passes 100%.

- [x] **`v0.6.0`** — Autonomous Re-Audit & Verification Gate:
  - Re-run deep review against the updated codebase.
  - Update `REVIEW.md` to reflect that all 7 confirmed defects (D1 through D7) are resolved.
  - Formulate detailed architecture specs for Phase 2 (modular package refactoring).
  - Verify `python3 -m unittest discover -s tests` passes 100%.

- [ ] **`v0.7.0`** — Architecture Modularity Phase 1: Dedicated `protocol.py` & `FakeTransport`:
  - Extract HID transport abstraction and Sonix packet framing into `ak820/protocol.py`.
  - Implement `FakeTransport` firmware simulator capable of modeling chunk packets, sub-packet ring counter, and injectable ACK drops.
  - Add offline protocol unit tests running against `FakeTransport` with no hardware attached.
  - Verify `python3 -m unittest discover -s tests` passes 100%.

- [ ] **`v0.8.0`** — Architecture Modularity Phase 2: Dedicated `screen.py` & RGB565 pipeline:
  - Extract image loading, RGB565 conversion, animation framing, and display layout into `ak820/screen.py`.
  - Optimize RGB565 byte packing and clean buffer allocations.
  - Add unit tests for screen buffer generation and format validation.
  - Verify `python3 -m unittest discover -s tests` passes 100%.

- [ ] **`v0.9.0`** — Architecture Modularity Phase 3: Clean CLI Dispatch & Compatibility Shim:
  - Extract CLI argparse commands and subcommands into `ak820/cli.py`.
  - Maintain top-level `ak820_ctrl.py` as a backward-compatible entrypoint shim so existing `pipx` / user scripts never break.
  - Keep all newly created modules strictly `< 300` lines.
  - Verify `python3 -m unittest discover -s tests` passes 100%.

- [ ] **`v0.10.0`** — Invariant Audit, CI Workflow & Final Polish:
  - Add GitHub Actions CI workflow running `unittest` and `py_compile` across Python 3.10, 3.11, 3.12, 3.13.
  - Verify zero regressions, complete documentation update in `README.md`, and clean exit codes.
  - Verify `python3 -m unittest discover -s tests` passes 100%.
