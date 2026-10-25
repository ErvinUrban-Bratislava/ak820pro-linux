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
| `v0.22.0` | 2026-10-24 | Done | Static Analysis Gate (§14.2): ruff (check + format --check) + mypy on `ak820/` wired into `scripts/lint.sh` and a new CI `lint` job reading one committed `pyproject.toml` baseline (line-length 100, target py310, curated rule families) — local and CI can never drift; all 27 ruff + 37 mypy findings fixed behavior-neutrally (implicit Optionals, `contextlib.suppress`, m4 retry re-expressed via short-circuit `and` with call-count test-lock, explicit `check=False`, transport `__all__` pins §18 re-exports against F401), only two justified noqa sites; `[project.scripts]` intactness regression-locked after the config-insertion trap | `python3 -m unittest` 328/328 + `scripts/lint.sh` GREEN + live `--dry-run`/`--help`/version probes via shim |
| `v0.23.0` | 2026-10-24 | Done | Optional numpy RGB565 fast path (§4.4): guarded `import numpy as _np` in `screen.py`, vectorized `_rgb565_numpy` (uint16 shifts, `astype("<u2")` little-endian) dispatched behind the unchanged `image_to_rgb565` signature with the v0.8.0 per-row loop kept as numpy-free fallback; worst case 128×128×255 frames **0.488 s → 0.008 s (~60×)**, byte-identical to the reference loop on gradient + noise; `pyproject` mypy override keeps the gate green on numpy-free CI without making numpy a dependency | `python3 -m unittest` 342/342 on both numpy-free system python (5 skips) and numpy venv (0 skips) + `scripts/lint.sh` GREEN + live `--dry-run` exit-8/version probes |
| `v0.21.0` | 2026-10-24 | Done | Comprehensive Re-Audit (REVIEW.md §20): D17 `run <missing-command>` raw `FileNotFoundError` traceback → `FileMissingError` 8 / non-executable → `InvalidFileError` 9 (never 4 — 4 stays "device node rejected us") + D18 Ctrl+C during upload/wait/child dumped a traceback → `main()` catches `KeyboardInterrupt` → friendly line + exit 130 (watch loop keeps its clean local stop) + D19 exit-5 retry discarded the align result and streamed into an un-aligned pipe (m4 bypass) → shared `_ensure_aligned` helper, structurally test-locked + m14 signal death → 128+N (137 not 247) + m15/m16 undocumented exit-1 user errors → documented usage 2; README Exit Codes synced (rows 2/8/9 extended, new 130 row) | `python3 -m unittest` 316/316 + live `probe`/`--dry-run`/exit-2 probes via shim |
| `v0.24.0` | 2026-10-24 | Done | Comprehensive Re-Audit (REVIEW.md §21): D20 successful upload exited **1** (`main()` returned `True`, bool-is-int → console wrapper `sys.exit(True)` → 1; both uploaded & dedup-skip now map to 0, shim `__main__` uses `sys.exit(main())`) + D21 bare `backlight` AttributeError traceback → required sub-action, usage 2 + D22 events-config shape/read/write violations (non-object, bad `events`, directory/unreadable/unwritable) → `ConfigError` 6 at the single load choke point + D23 `--save-preview` write failures (unwritable parent/target, unknown extension) → `InvalidFileError` 9 + D24 `--seconds/--watch/--dwell nan|inf` → `finite_float` argparse type, usage 2 + m17 closed-stdin keypress wait → False + m19 hand-edited `idle_color` → `ConfigError` 6; README rows 2/6/9 extended; m18/m20 queued | `python3 -m unittest` 373/373 + `scripts/lint.sh` GREEN + live exit-2/6/9 probes via shim |
| `v0.26.0` | 2026-10-24 | Done | CLI surface parity + carried §21 minors: `events list\|add\|remove` accept `--config F` **before and after** the sub-action (`argparse.SUPPRESS` sub-action defaults keep the parent value — argparse copies the sub-action namespace over the parent's; sub-action position wins) + m18 closed via new `ak820/configvalues.py` — events-config field-value coercion at the `load_events_config` choke point (D22's value half: scalars coerce losslessly, null/list/object + non-`{years}` note placeholders → `ConfigError` 6 naming `events[i].field`; `"idle_mode": "1"` → 1) + m20 closed — identical `events add` skips the write ("already up to date", bytes + mtime untouched); README exit-6 row + events `--config` example synced | `python3 -m unittest` 422/422 + `scripts/lint.sh` GREEN + live parity/exit-6/mtime-stable no-op probes via shim |
| `v0.25.0` | 2026-10-24 | Done | Architecture & Modularity (REVIEW.md §22): split the two top-level monoliths into single-responsibility package modules — `milestone_radar.py` (510 lines) → `ak820/events.py` (PIL-free events-config domain: D4/D5/m7/D22 validation + countdown rows) + `ak820/cards.py` (hero/board/custom renderers) + `ak820/palette.py` (THEMES urgency palette); `widgets.py` (359 lines) → `ak820/telemetry.py` (PIL-free sysfs/proc + playerctl/MPRIS probes) + `ak820/hud.py` (HUD/media card renderers); `ConfigError` moved into `ak820/errors.py` re-parented under `AK820Error` (exit 6 unchanged; `main()` now has ONE except branch and no shim import); both top-level files became identity shims and `ak820/` is now **self-contained** (zero imports of `milestone_radar`/`widgets`); all 21 package modules + 3 shims < 300 lines; 13-fixture render manifest byte-identical pre/post | `python3 -m unittest` 391/391 + `scripts/lint.sh` GREEN + byte-identical render parity + live events-CRUD/dry-run/preview/exit-2/6 probes via shim |
| `v0.29.0` | 2026-10-25 | Done | Comprehensive Re-Audit (REVIEW.md §25, cadence 5): D25 non-object `backlight` section (raw AttributeError exit 1 at every idle restore, live-proven on hardware) + D26 `"events": null` (passed D22's `is not None` guard; `cfg.get("events", [])` returns the null, not the default — raw TypeError exit 1 on list/add/radar) both closed at the `load_events_config` choke point as ConfigError 6 naming section + JSON kind (`configvalues.kind` promoted public — one vocabulary with m18/m21); m22 `--dry-run probe` no longer swallows the interface report (probe sends zero USB); AST anti-recurrence invariant forbids None-comparison guards at the choke point; README Architecture table synced from the pre-v0.16 eight-module world to all 24 modules + 3 shims | `python3 -m unittest` 494/494 + `scripts/lint.sh` GREEN + live real-hardware exit-6/`--dry-run probe` probes |
| `v0.28.0` | 2026-10-25 | Done | m21 closed — `idle_rainbow` bool coercion at the `configvalues` choke point: new `_as_bool` + `_IDLE_BOOL_FIELDS` (bools pass; `0`/`1` ints and `"true"/"false"/"1"/"0"` text case/whitespace-insensitive coerce losslessly; `"yes"`, `2`, `-1`, `1.0`, `null`, list/object → `ConfigError` 6 naming `backlight.idle_rainbow` — same taxonomy as m18); the m21 repro `"false"` now writes rainbow byte 0 (was 1); anti-recurrence invariant locks every `get_idle_backlight_cfg` `bl.get` field into a coercion list; README exit-6 row synced | `python3 -m unittest` 469/469 + `scripts/lint.sh` GREEN + live real-hardware probes (`"false"` → 0/coerced, `"yes"` → 6 both seams) |
| `v0.30.0` | 2026-10-25 | Done | Carried §25 minors: m23 idle int bounds at the `configvalues` choke point (bounds in `protocol.py` feed loader AND parser choices — one home; `_IDLE_INT_FIELDS` derived from `_IDLE_INT_RANGES` so an unbounded idle int field is unrepresentable; out-of-range -> ConfigError 6 naming field + range) + m24 one case-insensitive id-or-title identity rule (`_same_event`) shared by add + remove + m25 re-add preserves unknown hand-added fields (`dict(prior)` rebuild, `--note` wins, m20 no-op extends) + m26 `non_negative_float` rejects negative `--seconds`/`--watch`/`--dwell` at parse time (usage 2) | `python3 -m unittest` 519/519 + `scripts/lint.sh` GREEN + live real-hardware exit-6/exit-2/CRUD probes |
| `v0.31.0` | 2026-10-25 | Done | m27 closed — blank `--short`/`--title`/remove-target rejected at the CLI: new public `configvalues.validate_nonblank` (ConfigError 6, D4 precedent) is the single home of the non-blank rule shared by `events add` flags and the `events remove` target; returns the stripped text so padded values store the canonical id/label (`" DAUGHTER "` -> `daughter`/`DAUGHTER`, never `-daughter-`) and remove stays symmetric (m24's class); DECIDED: `--title` gets the same guard, loader stays permissive for hand-edited data (trust boundary test-locked); `_same_event` empty-needle semantics table-locked; README exit-6 row synced | `python3 -m unittest` 539/539 + `scripts/lint.sh` GREEN + live shim probes (repro forms -> 6, padded round-trip canonical) |
| `v0.32.0` | 2026-10-25 | Done | m28 closed — whitespace-only `--note` no longer stored verbatim (it rendered an EMPTY milestone column: `note_tpl or "Milestone"` is truthy for spaces): CLI rejects blank `--note` through the existing `validate_nonblank` (one rule, four entry points — `--short " "` and `--note " "` now fail identically), DECIDED against "treat as absent" because it guesses at intent and still cannot clear a note; `validate_note` now returns the stripped text and the coercion persists it, so hand-edited `"note": "   "` normalizes at the choke point to the documented `""` fallback; trust boundary revisited and KEPT CLI-only with the decisive reason recorded (`"note": ""` is a legal working spelling — load-time rejection bricks whole configs), identity fields deliberately not stripped at load; asymmetry test-locked as a table | `python3 -m unittest` 567/567 + `scripts/lint.sh` GREEN + live **real-hardware** probes (repro -> 6, padded stored `{years} yrs`, hand-edited blank -> `Milestone`, `status idle --config` -> 0) |
| `v0.33.0` | 2026-10-25 | Done | m29 closed — `--note` no longer accepted for categories that never render it (live-proven: `--category birthday --note "ignored {years}"` exited 0, stored, rendered `Turns 37`): new `configvalues.validate_note_category` rejects the inert pair as ConfigError 6 naming flag + category + both fixes, and it validates the **RESULTING entry** so the mirror path is covered too — a re-add with no `--note` at all preserved a note the new category could never show (m25's prior-copy). DECIDED reject over warn/document (a flag that does nothing is the m18 class; a warning is half-silent); `--clear-note` chosen over a `--note -` sentinel and removes the key (absent, not `""`), `--note`+`--clear-note` -> 6 checked before the blank rule; loader stays permissive (CLI 6 / loader 0 table-locked per category); anti-recurrence: category vocabulary now has ONE home feeding parser choices AND the renderer, inert set derived from it (m23's pattern), literals banned by source invariant | `python3 -m unittest` 602/602 + `scripts/lint.sh` GREEN + live **real-hardware** probes (repro -> 6, `--clear-note` -> key gone + `Milestone`, repeat clear mtime-stable, contradiction -> 6) |
| `v0.34.0` | 2026-10-25 | Done | m30 closed: unbounded dynamic text bounded on 128x128 screen via `fit_text` (shrink font then ellipsize), font fallbacks for DejaVu/Liberation, dynamic test fixture budget, CI font parity | `python3 -m unittest` 642/642 + `scripts/lint.sh` GREEN + remote CI GREEN (#40193038662) |
| `v0.35.0` | 2026-10-25 | Done | m31 closed (self-healing trigger: 19 gate failures "value escaped above the panel") — the m30 containment rule was width-only: `fit_text` measured width but never line count, so a `\n` split a fitted line and a **centred** box (custom-card `--value`) drew its first line ten px above the panel (7-line value → y=15 vs panel y=25), top-anchored boxes spilled below their border, silent exit 0. Fixed at the single home of containment: new `screen.flatten_lines` collapses every `str.splitlines()` boundary to a space, called by both `fit_text` and `ellipsize`, so every caller inherits vertical containment; newline-free text returned byte-identical (parity-locked), CLI unchanged (loader stays permissive, m27/m28/m29 boundary), source invariants forbid re-implementation, §10 budget regression-guarded | `python3 -m unittest` 658/658 + `scripts/lint.sh` GREEN (ruff+mypy) + live pixel-containment render probes |
| `v0.36.0` | 2026-10-25 | Done | m31b closed — the m30/m31 containment rule reached `hud.py`: media title/artist/album char-count caps (the exact proxy m30 rejected; 16-char title = 182 px amputated mid-glyph, single long word dropped line 1) replaced by `screen.fit_text` + a measured greedy wrap that marks its tail instead of dropping it; HUD `hostname` fitted to the clock's measured start x and `uptime` to its card border (live: painted to x=123 past the border at 121); every budget derived from the actual neighbouring element. New `configvalues.validate_single_line` (ConfigError 6) rejects line breaks in `events add --short/--title/--note` and `card --title/--value/--sub/--footer` at the ONE seam live and `--dry-run` share — the creation path refuses what the renderer can only flatten; loader stays permissive (CLI 6 / loader 0 table), CLI regex locked equal to `screen._LINE_BREAK_RE` (PIL-free twin), AST lock forbids any unfitted dynamic draw in hud | `python3 -m unittest` 693/693 + `scripts/lint.sh` GREEN + live real-hardware probes (break forms -> 6 zero-USB, valid -> 0, hand-edited `\n` still loads, renders measured contained) |
| `v0.27.0` | 2026-10-25 | Done | Config-override parity on the device-bound surface + `events add --note`: `config_path` threaded through `get/save/apply_idle_backlight_cfg` + `set_status_backlight`, `--config` on `backlight` (parent + SUPPRESS sub-action positions, sub-action wins), `status`, `run` — v0.26.0 invariant now holds everywhere; `SHOWCASE_PRESETS` → new pure-data `ak820/presets.py` (identity re-export, `backlight.py` 221 lines); `events add --note` validated by new public `configvalues.validate_note` (single home of `{years}` rule, loader+CLI both exit 6), re-add without `--note` preserves the note, m20 no-op extends over it; `compute_upcoming_events` trust boundary decided + docstring/test-locked (no second guard — `load_events_config` choke point stays the only home); README + REVIEW §23 synced | `python3 -m unittest` 458/458 + `scripts/lint.sh` GREEN + live real-hardware `--save --config`/note/exit-6 probes via shim |

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

- [x] **`v0.21.0`** — Comprehensive Re-Audit & Interrupt/Operand Contract (REVIEW.md §20):
  - D17: `run <missing-command>` leaked a raw `FileNotFoundError` traceback (exit 1) —
    command operand now typed: absent → `FileMissingError` (8), non-executable →
    `InvalidFileError` (9), never 4 (4 stays reserved for the device node / udev).
  - D18: Ctrl+C during upload, keypress wait, or a `run` child dumped a raw traceback —
    `main()` catches `KeyboardInterrupt` → friendly stderr + exit 130 (128+SIGINT);
    mid-upload interrupt is always safe (pipe flag dirty, digest unrecorded);
    `--watch` keeps its local clean stop (exit 0), regression-locked.
  - D19: the §3.4 exit-5 full retry discarded `align_data_pipe()`'s result and streamed
    into a provably un-aligned pipe (m4 invariant bypass) — both call sites now share
    `_ensure_aligned` (retry once → `ProtocolError` 7); structural test locks it.
  - m14: `run` child killed by signal N exits 128+N (137 for SIGKILL, not 247).
  - m15/m16: `run` with no operand and `backlight set --mode/--color <bad>` moved from
    undocumented exit 1 to documented usage-error exit 2; README Exit Codes synced
    (rows 2/8/9 extended, new 130 row).
  - New `tests/test_reaudit_v021.py` (16 offline tests, zero hardware).
  - Verify `python3 -m unittest discover -s tests` passes 100%.

- [x] **`v0.22.0`** — Static Analysis Gate (Tier C lane, queued since §14.2):
  - Add `ruff` (+ `ruff format --check`) and `mypy` on `ak820/` to CI and a local
    `scripts/lint.sh`; fix every finding in an isolated, behavior-neutral commit.
  - Baseline `pyproject.toml` config (line-length, target-version) committed so
    local and CI agree; no functional changes allowed in this iteration.
  - Verify `python3 -m unittest discover -s tests` passes 100% + lint gate green.
  - **Shipped:** `[tool.ruff]` (line-length 100, target py310, curated select) +
    `[tool.mypy]` (files=["ak820"], py3.10) committed as the single config source;
    `scripts/lint.sh` (ruff check + format --check + mypy) runs identically local
    and in the new CI `lint` job. All 27 ruff + 37 mypy findings fixed behavior-
    neutrally: implicit Optionals typed, `contextlib.suppress` for the four
    try/except-OSError/pass sites, m4 align retry re-expressed via short-circuit
    `and` (call-count test-locked), `check=False` explicit on `run` child,
    transport `__all__` pins the §18 re-exports against F401, SHOWCASE_PRESETS
    typed `list[dict[str, Any]]`; two noqa sites carry written justifications
    (state fail-dirty contract, `run` untrusted-input-by-design). New
    `tests/test_lint_gate_v022.py` (12 tests: gate end-to-end, pyproject baseline,
    `[project.scripts]` intactness regression, re-export identity, m4 semantics).

- [x] **`v0.23.0`** — Performance: numpy RGB565 Fast Path (Tier C lane, queued since §14.2):
  - Vectorized RGB565 packer in `ak820/screen.py` behind the existing function
    signature; keep the per-row fallback for numpy-free installs (import guard).
  - Measure before/after on a 128×128×255-frame worst case; byte-identical output
    locked by test against the current packer.
  - Verify `python3 -m unittest discover -s tests` passes 100%.
  - **Shipped:** guarded `try: import numpy as _np / except ImportError: _np = None`
    in `screen.py`; `_rgb565_numpy` (frombuffer→uint16 reshape, `(r>>3)<<11 |
    (g>>2)<<5 | b>>3`, `astype("<u2")` little-endian) dispatched by
    `image_to_rgb565` after the unchanged size guard; v0.8.0 per-row loop
    extracted verbatim to `_rgb565_rows` as the fallback. Worst case
    128×128×255 frames: **0.488 s (1.91 ms/frame) → 0.008 s (0.03 ms/frame),
    ~60×**, byte-identical asserted against the reference loop. numpy stays
    optional: `pyproject` gains only a `[[tool.mypy.overrides]]`
    `ignore_missing_imports` for `numpy` so the lint gate stays green on
    numpy-free CI; `dependencies` untouched. New `tests/test_rgb565_numpy_v023.py`
    (14 tests: dual-path byte-identity, dispatch routing, size guard on both
    paths, import-guard + no-hard-dependency source locks); suite runs green
    both ways — system python (numpy absent, 5 skips) and numpy venv (0 skips).

- [x] **`v0.24.0`** — Comprehensive Re-Audit (REVIEW.md §21):
  - Walk every error, operand, and signal path for raw tracebacks, undocumented
    exits, and contract drift; reproduce every suspect live before fixing.
  - D20: successful upload exits 1 (bool-is-int through `sys.exit`) -> 0.
  - D21: bare `backlight` AttributeError traceback -> required action, exit 2.
  - D22: events-config shape/read/write violations -> `ConfigError` (6).
  - D23: `--save-preview` write failures -> `InvalidFileError` (9).
  - D24: non-finite `--seconds/--watch/--dwell` -> usage error exit 2.
  - m17 closed-stdin keypress wait; m19 bad `idle_color` -> 6.
  - New `tests/test_reaudit_v024.py` (31 offline tests, zero hardware).
  - Verify `python3 -m unittest discover -s tests` passes 100% + lint gate green.
  - **Shipped:** all five defects + two minors closed as above; suite 342 -> 373,
    `scripts/lint.sh` GREEN, live probes 2/6/9 via the `ak820_ctrl` shim.
    Queued follow-ups: m18 (config field-value coercion), m20 (no-op `events
    add` rewrite).

- [x] **`v0.25.0`** — Architecture & Modularity: monolith decomposition (REVIEW.md §22):
  - Split `milestone_radar.py` (510 lines) into `ak820/events.py` (events-config
    domain: path resolution, D4/D5 date validation, m7/D22 shape checks, D7
    thresholds, countdown rows — PIL-free), `ak820/cards.py` (hero/board/custom
    128x128 renderers), `ak820/palette.py` (THEMES urgency palette).
  - Split `widgets.py` (359 lines) into `ak820/telemetry.py` (sysfs/proc sampling
    + playerctl/D-Bus MPRIS probes — PIL-free) and `ak820/hud.py` (HUD + media
    card renderers, dicts injectable).
  - Move `ConfigError` into `ak820/errors.py` as an `AK820Error` subclass
    (exit 6); `cli.main()` drops its `milestone_radar` import and second
    except branch; the invariant taxonomy test now covers ConfigError too.
  - `milestone_radar.py` + `widgets.py` become identity re-export shims; no
    `ak820/` module imports either anymore (package self-contained, §10.2).
  - Every module strictly < 300 lines; renders byte-identical (sha256 manifest
    over 13 fixtures: hero/board/custom states + HUD/media states).
  - New `tests/test_modularity_v025.py` (18 offline tests, zero hardware).
  - Verify `python3 -m unittest discover -s tests` passes 100% + lint gate green.
  - **Shipped:** as above; suite 373 -> 391; `scripts/lint.sh` GREEN — moving the
    probe code into `ak820/` brought 24 new ruff findings into the gate's scope,
    all fixed (typing modernization, `contextlib.suppress`, `l`->`ln`) or justified
    inline with the `state.py` noqa idiom (best-effort probe contract, PATH-probing
    optional tools); render manifest byte-identical pre/post across all 13 fixtures;
    live probes: events CRUD via `$AK820_EVENTS_PATH`, `--dry-run radar|card`,
    `radar/hud/media --save-preview`, exit 6 (bad `--date`, corrupt cfg, no-match
    remove), exit 2 (bare `backlight`). Found while probing: `events` rejects
    `--config` while `radar` accepts it -> queued below with carried m18/m20.

- [x] **`v0.26.0`** — CLI surface parity + carried §21 minors:
  - `events` subcommand gains `--config FILE` (v0.25.0 live probe: `events list
    --config F` is rejected as unrecognized while `radar --config F` works —
    every events-config consumer must accept the same override; today only
    `$AK820_EVENTS_PATH` works for `events`).
  - m18 (carried from §21): events-config field-value coercion — non-string
    `title`/`date`/`category` values crash downstream consumers.
  - m20 (carried from §21): `events add` with identical content rewrites the
    file as a no-op — skip the write when nothing changed.
  - Verify `python3 -m unittest discover -s tests` passes 100% + lint gate green.
  - **Shipped:** all three closed; suite 391 -> 422; `scripts/lint.sh` GREEN.
    `--config` accepted before AND after `list|add|remove` via
    `argparse.SUPPRESS` defaults on the sub-actions (argparse copies the
    sub-action namespace over the parent's — a None default would silently
    clobber `events --config F list`; sub-action position wins on conflict).
    m18 shipped as new `ak820/configvalues.py` (D22's value half at the same
    `load_events_config` choke point): JSON scalars coerce losslessly, null/
    list/object values + non-`{years}` note placeholders -> ConfigError 6
    naming `events[i].field`. m20: identical re-add prints "already up to
    date", file bytes + mtime untouched. README exit-6 row + events `--config`
    example synced; `tests/test_cli_parity_v026.py` (31 offline tests, zero
    hardware). Live probes: parity both positions + precedence, int title
    renders "123", bad placeholder/null title/garbage idle_mode exit 6,
    no-op add mtime-stable. Found while probing: device-bound consumers
    (`backlight --save`, idle restore) still ignore `--config` -> queued below.

- [x] **`v0.27.0`** — Config-override parity on the device-bound surface +
    `events add --note`:
  - `backlight set --save` / idle restore ignore the `--config` override
    (v0.26.0 finding): `get_idle_backlight_cfg()`/`save_idle_backlight_cfg()`
    call `load_events_config()`/`save_events_config()` with no path and the
    `backlight`/`status`/`run` subcommands expose no `--config` flag at all —
    they honor only `$AK820_EVENTS_PATH`. Thread an optional `config_path`
    through both functions and add the flag so the v0.26.0 invariant ("every
    events-config consumer accepts the same override") holds everywhere.
  - `events add` cannot write `note` — the one event field that requires
    hand-editing JSON. Add `--note TEXT` validated by the shipped
    `configvalues` `{years}`-only placeholder rule (same ConfigError 6), and
    make `events add` preserve an existing entry's `note` when the flag is
    omitted (today a re-add silently drops it).
  - Decide + document the `compute_upcoming_events` library-seam contract:
    hand-injected dicts bypass `configvalues` coercion — either document the
    trust boundary in the docstring or add a defensive guard.
  - Verify `python3 -m unittest discover -s tests` passes 100% + lint gate green.
  - **Shipped:** all three closed; suite 422 -> 458; `scripts/lint.sh` GREEN.
    `config_path` threaded through `get_idle_backlight_cfg`/
    `save_idle_backlight_cfg`/`apply_idle_backlight`/`set_status_backlight`;
    `--config` on `backlight` (parent + SUPPRESS sub-action positions,
    sub-action wins — v0.26.0 argparse pattern; demo/list parse-and-ignore),
    `status`, `run`. `SHOWCASE_PRESETS` moved to new pure-data
    `ak820/presets.py` (identity re-export) to keep `backlight.py` at 221
    lines under the §10 budget. `events add --note` validated by the new
    public `configvalues.validate_note` (single home of the `{years}` rule —
    loader and CLI can't drift, both exit 6); re-add without `--note`
    preserves the entry's note; m20 no-op skip extends over it (mtime-stable).
    Trust boundary: NO second guard in `compute_upcoming_events` — the
    `load_events_config` choke point stays the invariant's only home; the
    decision is docstring-locked + test-locked. README `--note`/`--config`
    examples + exit-6 row synced; REVIEW.md §23.
    `tests/test_device_config_parity_v027.py` (36 offline tests, zero
    hardware). Live probes on real hardware: `backlight set --save --config F`
    wrote the idle block into F (legacy default untouched), `status idle`/
    `run --config F` exit 0, note render "kept 0", bad note exit 6. Found
    while probing: `idle_rainbow` bypasses `configvalues` — `"false"` string
    is truthy in the rainbow byte -> queued below as m21.

- [x] **`v0.28.0`** — m21 (REVIEW.md §23.1): `backlight.idle_rainbow` value
    coercion — the one idle field in no `configvalues` list:
  - A hand-edited `"idle_rainbow": "false"` (non-empty string) passes through
    `coerce_config_values` untouched and is truthy in `set_backlight`'s byte
    assembly (`1 if rainbow else 0`) — rainbow mode turns ON when the user
    meant OFF (live-probe proven v0.27.0: wire byte data[8] == 1). `null`
    silently means OFF instead of a fix-the-config error.
  - Add `_IDLE_BOOL_FIELDS = ("idle_rainbow",)` to `configvalues`: bools pass
    through; `"true"/"false"/"1"/"0"` strings and 0/1 ints coerce losslessly
    to bool; anything else (other strings, 2, null, list/object) ->
    ConfigError (6) naming `backlight.idle_rainbow` — same taxonomy as the
    m18 int/text fields at the same choke point.
  - Regression tests: wire-byte proof (data[8]) for each accepted form, exit
    6 for each rejected one, through both `events list --config` (load choke
    point) and `apply_idle_backlight(dev, path)` (consumer seam).
  - Verify `python3 -m unittest discover -s tests` passes 100% + lint gate green.
  - **Shipped:** as specified; suite 458 -> 469; `scripts/lint.sh` GREEN.
    `_as_bool` + `_IDLE_BOOL_FIELDS` at the same m18/D22 choke point; text
    spellings case/whitespace-insensitive (mirrors `_as_int`'s strip); `1.0`
    deliberately rejected — close-but-not-lossless is named, never guessed.
    Anti-recurrence invariant (the real win): every `bl.get(...)` key in
    `get_idle_backlight_cfg` must appear in one of the three coercion lists,
    so a future idle field in no list fails the suite mechanically — m21's
    root cause is structurally dead, not just patched. README exit-6 row
    synced. `tests/test_idle_rainbow_bool_v028.py` (11 offline tests, zero
    hardware). Live probes on real hardware: `"false"` → exit 0 + rainbow byte
    0 (was 1), `"yes"` → 6 naming `backlight.idle_rainbow` on both seams,
    version 0.28.0.

- [x] **`v0.29.0`** — Comprehensive Re-Audit (REVIEW.md §25, cadence 5 — last
    full re-audit was v0.24.0 §21): walk every error, operand and signal path
    again after the v0.25–v0.28 surface growth (package split shims,
    `--config` on every consumer, `--note`, `presets.py`, bool coercion);
    reproduce every suspect live (real hardware + shim) before fixing; verify
    README Exit Codes ↔ `errors.py` ↔ `DOCUMENTED_CODES` parity, §10.2
    dependency rule, <300-line budget and shim identity across all 24 package
    modules + 3 shims; close the top findings and queue the rest.
  - Verify `python3 -m unittest discover -s tests` passes 100% + lint gate green.
  - **Shipped:** D25 + D26 + m22 closed; parity gates all re-verified green.
    D25: a non-object `backlight` section (string/list/number/null) was never
    shape-checked — raw AttributeError exit 1 at every idle restore
    (live-proven on real hardware via `status idle --config`). D26:
    `"events": null` passed D22's `is not None` guard but consumers read
    `cfg.get("events", [])` — the default only covers an ABSENT key — raw
    TypeError exit 1 on `events list`/`add`/radar render (live-proven all
    three seams). Both die at the `load_events_config` choke point as
    ConfigError 6 naming section + JSON kind (`got null`, never `NoneType` —
    `configvalues.kind` promoted public, one vocabulary with m18/m21);
    missing sections keep defaults, present-but-wrong is named never guessed.
    m22: `--dry-run probe` printed "nothing to preview" instead of the
    interface report — probe sends zero USB bytes, now routed before the
    dry-run block. Anti-recurrence: AST invariant forbids any
    None-comparison guard from returning to `load_events_config` (the D26
    pattern itself), table-driven per-kind rejection for both sections.
    Doc drift found + fixed: README Architecture table was still the
    pre-v0.16 eight-module world — synced to 24 modules + 3 shims; Testing
    count refreshed; exit-6 row extended. `tests/test_reaudit_v029.py`
    (25 offline tests, zero hardware). Suite 469 → 494; `scripts/lint.sh`
    GREEN; live probes: null/non-object configs → 6 friendly (were exit-1
    tracebacks), `--dry-run probe` → report, valid config unchanged,
    version 0.29.0. Queued m23–m26 below.

- [x] **`v0.30.0`** — Carried §25 minors (config-value bounds + CRUD
    consistency):
  - m23: `idle_mode` config value is unbounded — the CLI bounds 0..19 via
    `parse_mode`, but a hand-edited `"idle_mode": 255` wraps silently in
    `set_backlight`'s `mode & 0xFF` (firmware behavior unknown). Decide:
    per-field range validation at the `configvalues` choke point (mirror
    the CLI's bounds) vs documented clamp.
  - m24: `events remove` matches `id` case-sensitively but `title`
    case-insensitively — ids are generated lowercase, so `events remove
    DAUGHTER` (id spelling) fails with exit 6 while the title works; make
    the id match consistent (lower both) or document the asymmetry.
  - m25: `events add` re-add rebuilds the entry from the six schema fields
    only — unknown hand-added fields are silently dropped on update; decide
    preserve-vs-document.
  - m26: `finite_float` accepts negatives — `--dwell -5` cycles presets
    instantly, `--watch -5` silently disables watch; reject `< 0` at parse
    time (usage 2) or document.
  - Verify `python3 -m unittest discover -s tests` passes 100% + lint gate green.
  - **Shipped:** all four closed after live pre-fix repro (wire-byte + CLI
    evidence: 255 -> byte 255, 256 -> 0, brightness 9 -> silent 5; remove
    DAUGHTER -> 6; re-add dropped `icon`/`priority`; `--dwell -5` parsed).
    m23 DECIDED range validation over clamp (a silent clamp is the guessing
    m18/m21 killed): bounds live in `protocol.py` (`MAX_BRIGHTNESS`/
    `MAX_SPEED`/`MAX_DIRECTION` beside `MAX_LIGHT_MODE`) and feed BOTH
    `_as_int(bounds=...)` -> ConfigError 6 naming field + range AND
    `parser.py`'s argparse choices — one home, no CLI/loader drift;
    `_IDLE_INT_FIELDS` is DERIVED from `_IDLE_INT_RANGES`, so an unbounded
    idle int field is no longer representable (root cause structurally
    dead). m24: one identity rule `config._same_event` — id AND title
    case-insensitive, shared by add + remove (add's case-sensitive title
    match had the mirror bug: a re-add duplicated instead of updated);
    empty needles never match. m25 DECIDED preserve (the v0.27.0 note
    precedent generalized): re-add starts from `dict(prior)`, schema
    fields overwrite, `--note` wins, m20 no-op extends over extras
    (mtime-stable). m26: new `non_negative_float` (composes `finite_float`,
    which stays the nan/inf home) on `--seconds`/`--watch`/`--dwell` ->
    usage exit 2; zero stays valid. README exit-2/6 rows + Testing count
    synced; REVIEW.md §26. `tests/test_minors_v030.py` (25 offline tests,
    zero hardware: wire-byte in-range proof, table-driven exit-6 per field,
    device-never-touched repro-form proof, bounds single-home + derivation
    + parser-choices drift locks, matcher both directions, D13/m20/D24
    regressions, shim parity). Suite 494 -> 519; `scripts/lint.sh` GREEN;
    live real-hardware probes: `idle_mode: 255` -> 6 friendly on both
    seams (was a silent garbage byte), valid config -> 0 device restored,
    `remove DAUGHTER` -> "Removed 1", negative flags -> 2 pre-device,
    `--dry-run probe` intact, version 0.30.0. Found while probing: m27
    queued below.

- [x] **`v0.31.0`** — m27 (found during the v0.30.0 probes): `events add
    --short` accepts an empty or whitespace-only value — `--short " "`
    yields the degenerate id `"-"` and `--short ""` the empty id, with a
    blank `short_title` screen label; the v0.30.0 `_same_event` empty-
    needle guard keeps these from colliding, but the entry is unusable
    (radar renders an empty label, removal needs the title). Validate
    non-empty-after-strip at the CLI (ConfigError 6 family, the D4
    precedent — `--short` is the id's only source), lock the empty-needle
    semantics of `_same_event` against regression, and decide whether
    `--title` deserves the same strip guard.
  - Verify `python3 -m unittest discover -s tests` passes 100% + lint gate green.
  - Closed in v0.31.0: new public `configvalues.validate_nonblank` —
    single home of the non-blank rule (ConfigError 6, D4 precedent),
    shared by `events add --short`/`--title` and the `events remove`
    target; returns the stripped text, so padded values store the
    canonical id/label (`" DAUGHTER "` -> `daughter`/`DAUGHTER`, never
    `-daughter-`) and remove targets stay symmetric (m24's class).
    DECIDED: `--title` gets the same guard; the loader stays permissive
    for hand-edited data (trust boundary, test-locked). `_same_event`
    empty-needle semantics table-locked. README exit-6 row + Testing
    count synced; REVIEW.md §27. `tests/test_minors_v031.py` (20 offline
    tests, zero hardware: repro argv -> 6 with bytes + mtime stable,
    stripped-storage locks, remove symmetry, needle table, unit contract,
    trust-boundary lock). Suite 519 -> 539; `scripts/lint.sh` GREEN;
    live CLI probes via shim (repro forms -> 6 where degenerate stores
    exited 0, padded round-trip canonical, blank remove target -> 6),
    version 0.31.0. Found while probing: m28 queued below.

- [x] **`v0.32.0`** — m28 (found during the v0.31.0 probes): `events add
    --note "   "` stores the whitespace verbatim — `validate_note` passes
    it (no placeholders to reject), so the entry persists a blank note
    (live-proven: stored `'   '`). Decide: reject blank as ConfigError 6
    via the existing `validate_nonblank` (one rule, one home), or treat
    blank as absent (preserve the prior note on re-add, omit on fresh
    add). While there, revisit the v0.31.0 loader trust boundary: should
    `load_events_config` reject blank `id`/`title`/`short_title` in
    hand-edited configs, or keep the CLI-only boundary (currently
    test-locked)?
  - Verify `python3 -m unittest discover -s tests` passes 100% + lint gate green.
  - Closed in v0.32.0: DECIDED reject at the CLI (not "treat as absent") —
    `--note` now goes through `validate_nonblank` like `--short`/`--title`/
    remove-target (one rule, four entry points, ConfigError 6); treating blank
    as absent was rejected because it *guesses* at intent and buys nothing —
    absent preserves the prior note, so it would not even clear one. The blank
    was not inert: `note_tpl or "Milestone"` is truthy for spaces, so the
    documented fallback never fired and the MILESTONE column rendered empty
    (live-proven on both the CLI and a hand-edited config). Second half:
    `validate_note` now RETURNS the stripped text (the `validate_nonblank`
    pattern — normalization single-home, callers cannot forget to strip) and
    `coerce_config_values` persists it, so hand-edited `"   "` becomes the
    documented `""` fallback at the choke point rather than per consumer.
    Trust-boundary revisit DECIDED with the decisive reason recorded: the
    CLI-only rejection stays, because `"note": ""` is a legal *working*
    hand-edited spelling — rejecting blanks at load bricks whole configs
    (every command, including the ones needed to inspect the file, would exit 6
    over one field); m18 rejects wrong-*typed* values because they are
    unusable, a blank string is usable-but-degraded. Identity fields are
    deliberately NOT stripped at load (would silently change matching semantics
    of user-written data); the creation path normalizes. Asymmetry locked as a
    table (CLI 6 / loader 0). README exit-6 row + Testing count synced,
    parser `--note` help documents non-empty, REVIEW.md §28.
    `tests/test_minors_v032.py` (28 offline tests, zero hardware: blank-form
    table naming the flag, repro argv -> 6 with bytes+mtime stable, no
    traceback, blank re-add leaves prior note byte-identical, placeholder rule
    fires after the strip, storage/render locks compared against the row's own
    `years` so nothing is date-dependent, loader fallback + m18 regression,
    trust-boundary + source invariants that the CLI never strips a note itself);
    v0.27.0's `test_empty_note_allowed` superseded -> `test_empty_note_rejected`.
    Suite 539 -> 567 + `scripts/lint.sh` GREEN + live **real-hardware** probes
    (repro -> 6 where an exit-0 blank store was, padded note stored
    `{years} yrs`, hand-edited `"   "` listed `Milestone`, padded re-add ->
    "already up to date", `status idle --config` -> 0 with idle coercion intact,
    version 0.32.0). Found while probing: m29 queued below.

- [x] **`v0.33.0`** — m29 (found during the v0.32.0 probes): `--note` is
    silently inert for `--category birthday|anniversary` — live-proven:
    `events add --category birthday --note "ignored {years}"` exited 0, stored
    `note: 'ignored {years}'`, and `events list` rendered `Turns 1` (the note
    is consulted only in the milestone branch of `compute_upcoming_events`).
    A flag that is accepted, persisted, and never rendered is a silent no-op.
    Decide: warn on stderr (keep the write — the category may change later) vs
    reject as ConfigError 6 vs document in `--note` help; whichever is chosen,
    lock the category/note interaction table (3 categories x note present) so
    the render semantics are explicit. While there, close the related gap the
    m28 decision exposed: no CLI path clears an existing note (omitting `--note`
    preserves it, blank is now rejected), so JSON hand-editing is the only way —
    decide `--clear-note` vs `--note -` sentinel and keep the non-blank rule
    intact.
  - Verify `python3 -m unittest discover -s tests` passes 100% + lint gate green.
  - Closed in v0.33.0: DECIDED **reject at the CLI** (`configvalues.validate_note_category`,
    ConfigError 6) over warn-or-document — a flag that does nothing is the class
    this project has closed since m18, a stderr warning is half-silent and
    invisible to scripts, and help text alone leaves the trap armed; the message
    names the flag, the category and both fixes. The check runs against the
    **RESULTING entry** (after the m25 prior-copy merge and the `--clear-note`
    pop), which also catches the mirror path the flag alone would miss:
    `events add --category birthday` with no `--note` at all preserved a note the
    new category never renders. Consequence accepted + test-locked: an identical
    re-add of a hand-edited birthday+note entry is rejected too (m20's no-op sits
    after the rule) — the CLI never affirms what it refuses to create.
    `--clear-note` chosen over a `--note -` sentinel (a sentinel collides with
    real note text and argparse treats leading dashes as options); the key goes
    ABSENT not `""`, and `--note` + `--clear-note` → exit 6 checked BEFORE the
    blank rule so the contradiction is what gets named. Loader stays permissive
    (m27/m28 boundary, table-locked CLI 6 / loader 0 per category).
    Anti-recurrence: the category vocabulary now has ONE home
    (`configvalues.CATEGORY_*` / `EVENT_CATEGORIES` feeding parser choices AND the
    renderer, `NOTE_INERT_CATEGORIES` derived) — m23's pattern, so a new category
    cannot escape the note rule; source invariants forbid category literals in
    `parser.py`/`events.py` and lock the resulting-entry ordering. README exit-6
    row + events section + Testing count synced, REVIEW.md §29;
    `tests/test_minors_v033.py` (35 offline tests, zero hardware: repro argv -> 6
    with bytes+mtime stable, message names flag/category/both fixes, inertness
    comes from the category not `{years}`, preserved-note path + escape hatch +
    legal birthday->milestone revival, `--clear-note` key-absent/fallback/no-op
    mtime/fresh-entry/extras-preserved/contradiction ordering, 3x3 render table
    compared against each row's own `years`, trust-boundary table per category,
    8-case unit table, one-home + parser-surface locks); suite 567 -> 602 +
    `scripts/lint.sh` GREEN + live **real-hardware** probes (repro -> 6 where an
    exit-0 inert store was, `--clear-note` removed the key and listed
    `Milestone`, repeat clear -> "already up to date" mtime-stable, contradiction
    -> 6, milestone note round-trip intact, version 0.33.0). Found while probing:
    m30 queued below.

- [x] **`v0.34.0`** — m30 (found during the v0.33.0 probes): unbounded dynamic
    text on the 128x128 board card. Nothing bounds a `note`'s length
    (`validate_nonblank` checks non-blank, `validate_note` only the `{years}`
    placeholder), and `cards.render_board_card` draws each row's
    `f"{date_label} • {milestone_str}"` line at a **fixed** font size 9 with no
    width fitting — live-measured: a 66-char note produced **288 px** of text
    drawn from x=11 into a 128 px screen, clipped mid-word and silent, while
    every other dynamic string on that card (row name, hero sub, hero big) goes
    through `_fit_text_font`. `--short` is likewise unbounded (20 chars accepted
    today; its help says "max ~10 chars"). Decide: (a) fit the board meta line
    like the name line and ellipsize once `_fit_text_font` hits its min size so
    overflow is at least legible-and-marked rather than silently amputated,
    (b) bound `--note`/`--short` lengths at the CLI (ConfigError 6 naming the
    limit, the m23/m27 one-home pattern) or (c) both — and record why the hero
    card (which DOES fit) needs no bound. Lock the render table: note lengths
    short/edge/overflowing x hero/board, asserting every drawn string's bbox
    stays inside the row box (no test may depend on today's date). While there,
    check `render_custom_card`'s `--value`/`--sub`/`--footer` for the same
    unbounded-draw class and record findings either way.
  - Verify `python3 -m unittest discover -s tests` passes 100% + lint gate green.

- [x] **`v0.35.0`** — m31 (self-healing trigger fired: 19 gate failures "value escaped above the panel"): the m30 containment rule was **width-only** — `fit_text` measured width across the whole string but never asked how many lines it draws. PIL breaks a line on `\n`, so a centred box (the custom card's `--value`) centred on the TOTAL height and drew its first line ten px ABOVE the panel top (live-measured: a 7-line value hit y=15 vs panel y=25), while top-anchored boxes (board meta, hero subtitle) spilled below their own border — silent, exit 0, the m30 class one axis deeper. DECIDED: fix at the single home of containment, not per-caller — new `screen.flatten_lines` collapses every `str.splitlines()` line-break boundary to a space and is called by BOTH `fit_text` and `ellipsize`, so every caller inherits vertical containment exactly as it already inherits horizontal; newline-free text is returned byte-for-byte identical (parity test against a no-flatten reference), so no prior render moves. CLI deliberately NOT changed (a newline is unrenderable-on-one-line, not inert; loader stays permissive per the m27/m28/m29 trust boundary — hand-edited JSON with `\n` must still render, just contained). Anti-recurrence: source invariants lock that `flatten_lines` lives only in `screen` and `cards.py` never re-implements newline handling; the §10 <300-line budget is regression-guarded (screen.py 294). | `python3 -m unittest` 658/658 + `scripts/lint.sh` GREEN (ruff+mypy) + live render probes (custom value/board meta/hero subtitle newline containment measured pixel-by-pixel, byte-identical newline-free parity)

- [x] **`v0.36.0`** — m31b (found during the v0.35.0 audit): the m30/m31 containment rule still never reached `hud.py`. `render_media_card` bounds title/artist/album by **character count** (`title[:16]`, `artist[:20]`, `album[:22]`) — the exact proxy m30 rejected — so 16 wide glyphs at size 12 measure ~144 px into a ~120 px box (live-measured overflow past x=127), and the two-line wrap loop silently DROPS the first line for a single long word (no word boundary → everything lands on line 2). `render_hud_card` draws `hostname` and `uptime` unbounded (a 1000+-day uptime crosses the right-card border at x=121). Route every dynamic string in both renderers through `screen.fit_text` (measured-pixel, single-line via `flatten_lines`), rewrite the media wrap to a measured greedy wrap that never drops content and ellipsizes the tail, and derive each budget from the actual neighbouring element (clock start, card border). Add a CLI-side single-line guard (`validate_single_line`, ConfigError 6) for `--note`/`--value`/`--sub`/`--footer`/`--short`/`--title` so the creation path rejects what the renderer can only degrade — loader stays permissive (CLI 6 / loader 0 table). Verify with pixel-containment tests across hud/media + the existing byte-identical modularity lock.
  - CLOSED (v0.36.0): all four m31b repros live-proven against the real renderers
    BEFORE fixing (16-char title painted white to x=126 with 182 px amputated
    mid-glyph; single long word left line 1 EMPTY; "12345d 23h" painted amber to
    x=123 across the uptime card's border at x=121; 40-char hostname painted cyan
    to x=125 under the clock starting at x=98). Renderer: every dynamic TEXT
    string in `hud.py` now goes through `screen.fit_text` with each budget derived
    from the measured neighbour — hostname from the clock's start x
    (`clock_x - 4 - HUD_HOST_GAP`), uptime from the card border (`HUD_UPTIME_W =
    121-71 = 50`), media strings from the outer border (`MEDIA_TEXT_W = 127-8 =
    119`); numeric telemetry strings (clock/CPU/RAM/temp) stay the documented
    bounded-by-construction exemption, the same class cards.py locks for the days
    pill. The media wrap is a measured greedy wrap (`_wrap_media_title`): a single
    over-long word gets its own ellipsized line (never an empty line 1), a tail
    that fits on neither line is marked not amputated, and every line is
    pixel-measured ≤ budget. CLI: `configvalues.validate_single_line` (ConfigError
    6) guards `events add --short/--title/--note` (after strip, before the
    length/template rules) and `card --title/--value/--sub/--footer` at
    `renderers.render_card` — the ONE seam live upload and `--dry-run` share, so
    both fail identically (D12 precedent). The CLI's line-break pattern is a
    deliberate PIL-free twin of `screen._LINE_BREAK_RE` (configvalues must never
    import PIL — events add stays PIL-free); the two definitions are locked equal
    by an equivalence table, one rule two homes test-enforced. Loader stays
    permissive (CLI 6 / loader 0 asymmetry table-locked per field; a note a
    re-add PRESERVES from hand-edited JSON is not re-judged — the rule guards
    what the CLI creates). Anti-recurrence: an AST invariant locks every dynamic
    `draw.text` in both hud renderers as fitted/wrapped/exempt, char-count caps
    (`[:16]`/`[:20]`/`[:22]`) are banned by source lock, hud may import
    `flatten_lines` but may never define its own newline regex.
  - Verify `python3 -m unittest` 693/693 (35 new offline tests incl. byte-identical
    parity of both renderers against verbatim legacy copies for every string that
    fitted both the old caps AND the measured box) + `scripts/lint.sh` GREEN
    (ruff+mypy) + live real-hardware probes (`--dry-run card --value $'27\nDAYS'`
    -> 6 naming flag+fix with zero USB, events `--note`/`--title` break forms -> 6
    with config bytes stable, valid forms -> 0, hand-edited `\n` note still loads
    and lists, hud/media `--save-preview` renders measured contained — uptime text
    ends x=101, media title text x=123, both inside their borders; version 0.36.0).
    Found while probing: m32 queued below.

- [ ] **`v0.37.0`** — m32 (found during the v0.36.0 probes): the media card's
    header tags render TOFU on the production font. `render_media_card` draws
    `"▶ PLAYING"` / `"⏸ PAUSED"` / `"♫ MEDIA"` with `FONT_BOLD_PATHS`, but a
    `.notdef`-mask comparison (measured live in v0.36.0): LiberationSans-Bold —
    the FIRST catalog entry, installed on the production machine and in CI —
    renders `▶` and `⏸` as identical-to-PUA tofu boxes (the header reads
    "□ PLAYING" on real hardware, silent, exit 0), NotoSans-Bold renders all
    three as tofu, and only DejaVu carries `▶`/`♫` (never `⏸`). This is exactly
    the silent-glyph class v0.34 closed for `ELLIPSIS` ("every font in the
    catalogs carries the glyph, locked by test") — the tag strings were simply
    never audited. Decide: (a) replace the tags with catalog-universal text
    (plain "PLAYING"/"PAUSED"/"MEDIA", or ASCII-safe markers that exist in
    every catalog font), (b) keep the glyphs but reorder/extend the catalog so
    a carrying font wins, or (c) per-glyph fallback at draw time — and record
    why the chosen fix cannot drift. Extend the v0.34 `.notdef`-comparison
    coverage lock to EVERY literal string the renderers draw (header tags,
    "AK820 Pro Display", "AJAZZ AK820 PRO", "TEMP"/"UPTIME"/"CPU"/"RAM",
    "TODAY!"/"TOMORROW"/"DAYS", state labels), so a literal can never be added
    that some catalog font renders as tofu. While there (minor, record findings
    either way): `card` is the only render command WITHOUT `--save-preview`
    (radar/hud/media all have it — a dry-run digest is not a PNG preview), and
    `events list` renders a hand-edited multi-line note ragged across table
    rows (loader stays permissive by the m27/m28 decision — flatten for
    DISPLAY only at the print seam, not in the data).
