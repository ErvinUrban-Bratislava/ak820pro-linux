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
| `v0.7.0` | 2026-10-23 | Done | Architecture Phase 1: `ak820/` package — `protocol.py` (constants/framing/parse helpers), `transport.py` (`HidTransport` seam + `LinuxHidrawTransport` errno→error translation + sysfs discovery), `device.py` (fd-free `AK820Pro` + `stream_payload`, m3 ACK-magic validation, m1 fd-leak fix); `FakeTransport` SN32F290 simulator (ring counter, ACK magic, injectable faults) + 17 offline protocol tests; `ak820_ctrl.py` now a compat shim re-exporting every public name | `python3 -m unittest` 78/78 |
| `v0.8.0` | 2026-10-23 | Done | Architecture Phase 2: `ak820/screen.py` (byte-identical optimized per-row RGB565 packer ~1.2 ms/frame, `fit_image` m9 alpha-mask fix, GIF loader, shared render_common constants + `lru_cache` fonts m8/§4.2) + `ak820/state.py` (dedup out of `milestone_radar` — §2.1 layering inversion killed — with `AK820_STATE_PATH` override + §4.5 memoryview hashing); `upload_frames_to_screen` no longer imports `milestone_radar`; full shim re-exports preserved | `python3 -m unittest` 108/108 |
| `v0.9.0` | 2026-10-23 | Done | Architecture Phase 3 (§10.5): `ak820/cli.py` (`build_parser()` flag-for-flag identical + `DEVICE_HANDLERS` dispatch table §2.3 + §3.4 exit-code `main(argv=None)`, `run_watch_loop` collapses both `--watch` loops, m12 progress gated on `isatty`) + `actions.py`/`backlight.py`/`status.py`/`config.py` command layer + `DeviceNotFoundError` (exit 3); `AK820ProDevice`/`wait_for_keypress` moved into `transport.py` (only fd/io module, §10.2); `ak820_ctrl.py` now a pure ~98-line re-export shim; console scripts → `ak820.cli:main`, PEP 639 `license = "MIT"`; all 12 package modules < 300 lines | `python3 -m unittest` 130/130 + live `probe`/`events list` |
| `v0.10.0` | 2026-10-24 | Done | Invariant Audit, CI Workflow & Final Polish: GitHub Actions matrix (py_compile + unittest on Py 3.10–3.13) + `tests/test_invariants.py` (compile gate, error-taxonomy→`main()` exit-code mapping for every `AK820Error` subclass, README↔`errors.py` exit-code table sync, pipx entry points) + README Exit Codes/Architecture/Testing sections | `python3 -m unittest` 137/137 + local `py_compile` gate |
| `v0.11.0` | 2026-10-24 | Done | Reliability & Robustness: `DeviceDisconnectedError` → exit 75 `EX_TEMPFAIL` (§3.4, systemd-recoverable unplug) + real-transport ENODEV read translation (unplug ≠ ACK timeout) + m4 align retry-once-then-abort (actions + device) + one automatic full upload retry on transient ACK loss (§3.4 exit-5 policy) + observable `handshake_failures` counter (§3.6) + `FakeTransport` faults `drop_once`/`disconnect_at` + 18 offline resilience tests, zero hardware | `python3 -m unittest` 155/155 |
| `v0.12.0` | 2026-10-24 | Done | Comprehensive Re-Audit & Contract Reconciliation: D8 exit-code drift fixed (`PermissionDeniedError`→4, `ProtocolError`→7 per §3.4; README re-synced to spec not code) + D9 §3.6 completed (handshake counter resets on success, dead control pipe raises exit-75 after 2 consecutive failures) + last open minor defects closed (m5 import-time path deleted, m6 atomic config write, m7 corrupt-JSON→`ConfigError`, m10 friendly `--color` error, m11 PIDS↔udev parity) + §10.2 dependency rule now test-enforced + REVIEW.md §11 audit trail; m1–m12 all closed | `python3 -m unittest` 171/171 + live `probe` |

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

- [x] **`v0.7.0`** — Architecture Modularity Phase 1: Dedicated `protocol.py` & `FakeTransport`:
  - Extract HID transport abstraction and Sonix packet framing into `ak820/protocol.py`.
  - Implement `FakeTransport` firmware simulator capable of modeling chunk packets, sub-packet ring counter, and injectable ACK drops.
  - Add offline protocol unit tests running against `FakeTransport` with no hardware attached.
  - Verify `python3 -m unittest discover -s tests` passes 100%.

- [x] **`v0.8.0`** — Architecture Modularity Phase 2: Dedicated `screen.py` & RGB565 pipeline:
  - Extract image loading, RGB565 conversion, animation framing, and display layout into `ak820/screen.py`.
  - Optimize RGB565 byte packing and clean buffer allocations.
  - Add unit tests for screen buffer generation and format validation.
  - Verify `python3 -m unittest discover -s tests` passes 100%.

- [x] **`v0.9.0`** — Architecture Modularity Phase 3: Clean CLI Dispatch & Compatibility Shim:
  - Extract CLI argparse commands and subcommands into `ak820/cli.py`.
  - Maintain top-level `ak820_ctrl.py` as a backward-compatible entrypoint shim so existing `pipx` / user scripts never break.
  - Keep all newly created modules strictly `< 300` lines.
  - Verify `python3 -m unittest discover -s tests` passes 100%.

- [x] **`v0.10.0`** — Invariant Audit, CI Workflow & Final Polish:
  - Add GitHub Actions CI workflow running `unittest` and `py_compile` across Python 3.10, 3.11, 3.12, 3.13.
  - Verify zero regressions, complete documentation update in `README.md`, and clean exit codes.
  - Verify `python3 -m unittest discover -s tests` passes 100%.

- [x] **`v0.11.0`** — Reliability & Robustness (zero hardware attached):
  - Protocol-level error handling: `DeviceDisconnectedError` exits 75 `EX_TEMPFAIL` (§3.4) so a mid-op unplug is systemd-recoverable; `LinuxHidrawTransport.read_data` translates ENODEV into that error instead of masking an unplug as an ACK timeout.
  - Disconnect recovery & timeout guards: one automatic full upload retry (re-align + re-stream) on transient ACK loss (§3.4 exit-5 "retry once"); m4 align failure retries once then aborts with `ProtocolError`; observable `handshake_failures` counter (§3.6).
  - `FakeTransport` fault injection extended: `drop_once` (transient loss) and `disconnect_at` (mid-upload ENODEV simulation).
  - New `tests/test_resilience.py` (18 offline tests) covering recovery, abort, errno translation, and the exit-75 CLI contract.
  - Verify `python3 -m unittest discover -s tests` passes 100%.

- [x] **`v0.12.0`** — Comprehensive Re-Audit & Contract Reconciliation (REVIEW.md §11):
  - D8: exit-code contract reconciled with §3.4 — `PermissionDeniedError` → 4 (was 3, indistinguishable from device-not-found), `ProtocolError` → 7 (was squatting on the permission code); README table re-synced to the spec, `DOCUMENTED_CODES` extended, CLI tests updated.
  - D9: §3.6 dead-control-pipe detection completed — `handshake()` resets the counter on success and raises `DeviceDisconnectedError` (exit 75) after 2 consecutive failures; `FakeTransport(ctrl_dead=True)` fault.
  - Last open minor defects closed: m5 (import-time `DEFAULT_EVENTS_PATH` deleted), m6 (atomic `save_events_config` via mkstemp+`os.replace`, bare-relative-path safe), m7 (corrupt JSON → `ConfigError` exit 6), m10 (friendly `--color banana` error at root + call site), m11 (`0xFEFE` dropped; PIDS↔udev parity now test-locked).
  - §10.2 dependency rule made executable: `TestDependencyRuleInvariant` (no fd/io outside `transport.py`, no rendering-domain imports in the protocol seam).
  - New `tests/test_audit_fixes.py` (16 offline tests); m1–m12 all closed; REVIEW.md §11 audit trail.
  - Verify `python3 -m unittest discover -s tests` passes 100%.

- [ ] **`v0.13.0`** — Performance: Alignment-Skip on Known-Clean Pipe (§3.5/§4.1, biggest remaining win):
  - Record pipe-clean state in the dedup state file after a fully-ACKed SAVE; skip the ~1.6–2 s `align_data_pipe` on the next upload when the pipe is provably clean (sub_packet_idx == 0 per FINDINGS.md §4).
  - Flip to dirty pessimistically before streaming; any timeout/crash re-aligns next run.
  - `FakeTransport` starts dirty (nonzero ring index) to prove the skip is state-driven, not unconditional.
  - Measure before/after wall time on a repeat upload against `FakeTransport`; add regression tests for skip, dirty-flip, and recovery.
  - Verify `python3 -m unittest discover -s tests` passes 100%.

- [ ] **`v0.14.0`** — Ops & Observability backlog (REVIEW.md B6/B7 + §6.5):
  - `logging` to stderr + global `--verbose/-v`; stdout reserved for machine-readable data (`events list --json`).
  - Single `playerctl -f '{{status}}\t{{title}}\t{{artist}}\t{{album}}' metadata` call instead of four subprocesses (§4.3).
  - Ship `systemd/ak820-radar.{service,timer}` + `ak820-hud.service` (Restart=on-failure pairs with exit 75); `--dry-run` global flag (render + digest + chunk count, no USB).
  - `sync_clock` DST ambiguity note (§6.5); `wait_for_keypress` stdin-EOF edge (instant false "keypress" when stdin is /dev/null).
  - Verify `python3 -m unittest discover -s tests` passes 100%.
