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
| `v0.13.0` | 2026-10-24 | Done | Performance — Alignment-Skip on Known-Clean Pipe (§3.5/§4.1, B4 closed): `state.pipe_known_clean()`/`mark_pipe()` persist a strict-boolean `pipe_clean` flag (atomic read-modify-write, fails dirty on absent/corrupt); repeat uploads skip the ~1.6–2 s `align_data_pipe` after a fully-ACKed SAVE, flip dirty pessimistically before streaming so any crash re-aligns next run; `FakeTransport` starts dirty (`start_idx=37`) proving the skip is state-driven; measured 1.50 s → 0.75 s vs simulator | `python3 -m unittest` 186/186 |
| `v0.14.0` | 2026-10-24 | Done | Ops & Observability (B6/B7 + §6.5): `logging` to stderr + global `--verbose/-v` (stdout reserved for `events list --json`); single templated `playerctl -f '{{status}}\t{{title}}\t{{artist}}\t{{album}}' metadata` call replaces four subprocesses (§4.3); shipped `systemd/ak820-radar.{service,timer}` + `ak820-hud.service` (`Restart=on-failure` pairs with exit 75); global `--dry-run` (render + sha256 digest + chunk count, zero USB, zero state writes) via new `ak820/renderers.py`; `sync_clock` DST fall-back note (§6.5); `wait_for_keypress` stdin-EOF fix — `/dev/null` no longer a false keypress | `python3 -m unittest` 200/200 |
| `v0.15.0` | 2026-10-24 | Done | Comprehensive Re-Audit (REVIEW.md §14): D10 `radar --date <invalid>` traceback → `ConfigError` exit 6 (D4 never covered the `--date` override; live/preview/dry-run all affected) + D11 missing upload file traceback → new `FileMissingError` exit 8 (README table, `DOCUMENTED_CODES`, package/shim re-exports synced); `run` exit-transparency + lint-lane audit observations recorded | `python3 -m unittest` 214/214 + live exit probes |
| `v0.16.0` | 2026-10-24 | Done | Modular Package Refactoring (REVIEW.md §15): `cli.py` (289 lines, at the §10 budget) split three ways — new `ak820/parser.py` (argparse surface, 104 lines) + new `ak820/handlers.py` (device-bound `(args, dev)` handlers + `DEVICE_HANDLERS` + `run_watch_loop`, 114 lines) + slim 106-line `cli.py` keeping offline routing, `--dry-run`, discovery and the §3.4 exit-code `main()`; every split name re-exported (identity, not copy) so `ak820.cli.*`, `ak820.*` and the `ak820_ctrl` shim are byte-for-byte compatible entry points | `python3 -m unittest` 228/228 + live `probe`/`--dry-run` via shim |
| `v0.17.0` | 2026-10-24 | Done | Timeout Guards & Bounded I/O (REVIEW.md §16): `drain_data_acks` capped at `DRAIN_MAX_READS` (a chattering pipe can no longer hang the CLI), `stream_payload` whole-transfer deadline (30 s base + 2× per-chunk budget, injectable clock) raising `UploadIncompleteError` exit 5 before SAVE, `LinuxHidrawTransport.write_data` write-all loop with stall cap (short writes completed; wedged pipe -> `ProtocolError` exit 7), FakeTransport `flood` fault | `python3 -m unittest` 243/243 + live `probe`/`--dry-run` via shim |
| `v0.18.0` | 2026-10-24 | Done | Comprehensive Re-Audit (REVIEW.md §17): D12 `upload <non-image/dir/corrupt>` raw PIL traceback → new `InvalidFileError` exit **9** via shared `actions.load_frames_guarded` (live + `--dry-run` fail identically) + D13 `events remove <no-match>` silent success → `ConfigError` exit 6 with config left byte-identical + D14 `--save-preview` path errors (missing parents auto-created; unusable targets exit 9) + m13 version drift (`__version__` 0.9.0 vs pyproject 1.0.0) locked equal by test; README/`DOCUMENTED_CODES`/package/shim synced in one invariant-locked commit | `python3 -m unittest` 263/263 + live exit probes 8/9 via shim |
| `v0.19.0` | 2026-10-24 | Done | Modular Package Refactoring (REVIEW.md §18): sysfs discovery (`find_hidraw_devices`/`find_keyboard_hidraw` + helpers) extracted byte-identically from `transport.py` (299 → 229 lines) into new `ak820/discovery.py` (99 lines) — *which* nodes vs *how* to talk over them; identity re-exports keep `transport`/`ak820`/`ak820_ctrl`/`cli` and all mock-patch targets intact; §10.2 refined (fd/io grep stays transport-only; sysfs path strings allowed in `discovery.py`); `discovery → {protocol}` keeps the graph acyclic | `python3 -m unittest` 278/278 + live `probe`/`--dry-run` via shim |
| `v0.20.0` | 2026-10-24 | Done | Disconnect Recovery & Open-Path Robustness (REVIEW.md §19): D15 `AK820ProDevice.__enter__` leaked raw OSError tracebacks (exit 1) when a node vanished between discovery and open (ENOENT/ENXIO) or the udev rule was missing (EACCES/EPERM) — `_translate` now maps ENOENT/ENXIO with ENODEV → `DeviceDisconnectedError` 75 and open failures → 75/4, m1 close-once now test-locked; D16 `handshake()` swallowed `PermissionDeniedError` as a §3.6 blip and misattributed strike two as a dead pipe (75) — permission errors now fail fast (4); mid-stream/mid-align disconnect (75) never retried like exit-5, never reaches SAVE, digest unrecorded, pipe flag dirty; `FakeTransport(perm_denied=True)` fault | `python3 -m unittest` 300/300 + live `probe`/`--dry-run` via shim |

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

- [x] **`v0.13.0`** — Performance: Alignment-Skip on Known-Clean Pipe (§3.5/§4.1, biggest remaining win):
  - Record pipe-clean state in the dedup state file after a fully-ACKed SAVE; skip the ~1.6–2 s `align_data_pipe` on the next upload when the pipe is provably clean (sub_packet_idx == 0 per FINDINGS.md §4).
  - Flip to dirty pessimistically before streaming; any timeout/crash re-aligns next run.
  - `FakeTransport` starts dirty (nonzero ring index) to prove the skip is state-driven, not unconditional.
  - Measure before/after wall time on a repeat upload against `FakeTransport`; add regression tests for skip, dirty-flip, and recovery.
  - Verify `python3 -m unittest discover -s tests` passes 100%.

- [x] **`v0.14.0`** — Ops & Observability backlog (REVIEW.md B6/B7 + §6.5):
  - `logging` to stderr + global `--verbose/-v`; stdout reserved for machine-readable data (`events list --json`).
  - Single `playerctl -f '{{status}}\t{{title}}\t{{artist}}\t{{album}}' metadata` call instead of four subprocesses (§4.3).
  - Ship `systemd/ak820-radar.{service,timer}` + `ak820-hud.service` (Restart=on-failure pairs with exit 75); `--dry-run` global flag (render + digest + chunk count, no USB).
  - `sync_clock` DST ambiguity note (§6.5); `wait_for_keypress` stdin-EOF edge (instant false "keypress" when stdin is /dev/null).
  - Verify `python3 -m unittest discover -s tests` passes 100%.

- [x] **`v0.15.0`** — Comprehensive Re-Audit & Input-Validation Hardening (REVIEW.md §14):
  - D10: `radar --date <invalid>` (live / `--save-preview` / `--dry-run`) leaked a raw
    `ValueError` traceback — `today_from_args` now raises `ConfigError` (exit 6) for
    malformed *and* impossible dates (Feb 30, non-leap Feb 29).
  - D11: `upload <missing>` / `--dry-run upload <missing>` leaked a raw `FileNotFoundError` —
    new `FileMissingError` (exit 8); README Exit Codes, `DOCUMENTED_CODES`, and package +
    `ak820_ctrl` shim re-exports synced in the same commit (invariant tests lock all three).
  - §14.2 records accepted designs (`run` child-exit transparency) and the queued Tier C
    lane: numpy RGB565 fast path, ruff/mypy gate, `events remove` no-match UX.
  - New `tests/test_reaudit_v015.py` (14 offline tests, zero hardware).
  - Verify `python3 -m unittest discover -s tests` passes 100%.

- [x] **`v0.16.0`** — Modular Package Refactoring: `cli.py` split (§15):
  - New `ak820/parser.py`: the argparse tree (`build_parser`) — what the CLI accepts, nothing else.
  - New `ak820/handlers.py`: device-bound `(args, dev)` handlers, `DEVICE_HANDLERS`, `TFT_COMMANDS`, `run_watch_loop`.
  - `cli.py` keeps offline routing, `--dry-run`, device discovery, `_configure_logging`, and the
    §3.4 exit-code `main()`; re-exports every split name (same object, no copy drift) so
    `ak820.cli`, `ak820/__init__`, and the `ak820_ctrl` shim are unchanged.
  - New `tests/test_modularity_v016.py` (14 offline tests): re-export identity, shim parity,
    parser↔handler structural parity, <300-line budget, fresh-interpreter import (no cycle).
  - Verify `python3 -m unittest discover -s tests` passes 100%.

- [x] **`v0.17.0`** — Timeout Guards & Bounded I/O (§16, RELIABILITY mode):
  - `drain_data_acks(max_reads=DRAIN_MAX_READS)`: bounded drain — an always-readable
    (chattering) data pipe can no longer spin the loop forever and hang the CLI or a
    `Restart=on-failure` systemd unit with no bound.
  - `stream_payload(..., deadline_s=None, clock=time.monotonic)`: whole-transfer deadline
    (30 s base + 2× the per-chunk timeout budget) checked before every chunk — a device
    ACKing just under the 1 s per-chunk timeout can never keep an upload alive indefinitely.
    A breach raises the same typed `UploadIncompleteError` (exit 5) *before* SAVE, so the
    digest stays unrecorded and the actions-level full retry applies unchanged.
  - `LinuxHidrawTransport.write_data`: write-all loop — short writes are completed (a partial
    chunk desyncs the firmware ring counter exactly like a dropped packet); consecutive
    zero-progress attempts are capped (`MAX_WRITE_RETRIES`) -> `ProtocolError` (exit 7).
  - FakeTransport gains a `flood=True` fault modeling the chattering pipe.
  - New `tests/test_timeout_guards.py` (15 offline tests, zero hardware, injectable clock —
    no test ever sleeps to prove a timeout).
  - Verify `python3 -m unittest discover -s tests` passes 100%.

- [x] **`v0.18.0`** — Comprehensive Re-Audit & File-Operand Contract (REVIEW.md §17):
  - D12: `upload <non-image|directory|corrupt>` leaked raw PIL/OSError tracebacks
    (exit 1) on both live and `--dry-run` paths — new `InvalidFileError` (exit **9**,
    present-but-unusable sibling of D11's exit-8 absent) raised by a single shared
    `actions.load_frames_guarded`; README table, `DOCUMENTED_CODES`, package + shim
    re-exports synced in the same commit (invariant tests lock all three).
  - D13 (queued Tier C closed): `events remove <no-match>` exited 0 and rewrote the
    config for nothing — now `ConfigError` (exit 6), file left byte-identical.
  - D14: `--save-preview` into a missing parent dir leaked `FileNotFoundError`
    (parents now auto-created, mirroring radar semantics); unusable targets
    (dir-as-file, file-as-dir) exit 9 friendly.
  - m13: `ak820.__version__` (0.9.0) vs pyproject (1.0.0) drift — both 0.18.0,
    locked equal by `TestVersionSync`.
  - New `tests/test_reaudit_v018.py` (20 offline tests, zero hardware).
  - Verify `python3 -m unittest discover -s tests` passes 100%.

- [x] **`v0.19.0`** — Modular Package Refactoring: `transport.py` → `discovery.py` split (REVIEW.md §18):
  - Sysfs scanning (`find_hidraw_devices`, `find_keyboard_hidraw`,
    `_read_hid_identity`, `_parse_usage_page`) extracted byte-identically into
    new `ak820/discovery.py` (99 lines); `transport.py` 299 → 229 lines and now
    owns only the fd/ioctl/select seam (`HidTransport`, `LinuxHidrawTransport`,
    `wait_for_keypress`, `AK820ProDevice`).
  - Identity re-exports through `transport`, `ak820`, `ak820_ctrl` shim and
    `cli` keep every historical import site and mock-patch target working.
  - §10.2 refined, not relaxed: fd/io tokens remain transport-only (grep-locked);
    sysfs path strings allowed in `transport.py` or `discovery.py`.
  - New `tests/test_discovery_v019.py` (13 offline tests, zero hardware).
  - Verify `python3 -m unittest discover -s tests` passes 100%.

- [x] **`v0.20.0`** — Disconnect Recovery & Open-Path Robustness (REVIEW.md §19):
  - D15: `AK820ProDevice.__enter__` leaked raw OSError tracebacks (exit 1) —
    unplug between sysfs discovery and `os.open` (ENOENT/ENXIO) or a missing
    udev rule (EACCES/EPERM) at open time. `_translate` now maps ENOENT/ENXIO
    alongside ENODEV → `DeviceDisconnectedError` (75); `__enter__` routes every
    open failure through it (→ 75/4). m1 fd-leak guarantee preserved and now
    test-locked (second-open failure closes the first fd exactly once).
  - D16: `handshake()` swallowed `PermissionDeniedError` as a transient §3.6
    blip; strike two misattributed a rejected pipe as a dead one (75 instead of
    the udev-rule fix, 4). Permission errors now propagate immediately; the
    OSError/ENODEV two-strike model is unchanged.
  - Disconnect semantics locked: mid-stream/mid-align unplug (75) is never
    retried like exit-5, never reaches SAVE, digest stays unrecorded, pipe flag
    stays dirty; CLI end-to-end exits 75/4, never a traceback.
  - `FakeTransport` gains a `perm_denied=True` fault (missing-udev-rule model).
  - New `tests/test_disconnect_recovery_v020.py` (22 offline tests, zero hardware).
  - Verify `python3 -m unittest discover -s tests` passes 100%.
