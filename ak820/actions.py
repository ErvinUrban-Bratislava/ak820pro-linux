"""Device-facing actions: upload pipeline, RTC sync, radar frame building (v0.9.0).

Extracted from `ak820_ctrl.py` so `cli.py` stays a thin dispatch layer (§10.5).
Depends only on {protocol, device, screen, state, errors} + cards (rendering
domain, function-level import — never on protocol/device paths).
"""

import hashlib
import os
import struct
import sys

from . import state
from .device import stream_payload
from .errors import FileMissingError, InvalidFileError, ProtocolError, UploadIncompleteError
from .protocol import MAX_FRAMES, build_payload
from .screen import image_to_rgb565, load_frames_from_file


def _ensure_aligned(dev):
    """m4 invariant, centralized in v0.21.0 (D19, §20): never stream into an
    un-aligned pipe — retry once, then abort with ProtocolError (exit 7)
    before any chunk leaves the host.

    D19: the §3.4 exit-5 full-retry path used to call `align_data_pipe()` and
    *discard* its result, streaming into a provably un-aligned pipe — the exact
    desync state alignment exists to prevent. Both call sites now share this
    one implementation, so the retry can no longer bypass the invariant.
    """
    aligned_pkts, acked = dev.align_data_pipe()
    if not acked:
        # m4: streaming into an un-aligned pipe is the desync state alignment
        # exists to prevent — retry once, then abort before any chunk is sent.
        print(
            f"  WARN: alignment ACK not seen ({aligned_pkts} packets) — retrying alignment once..."
        )
        aligned_pkts, acked = dev.align_data_pipe()
        if not acked:
            raise ProtocolError(
                "Data pipe will not align after 2 attempts — "
                "check the cable / re-seat the device. Upload aborted."
            )
    print(f"  Aligned: flash ACK after {aligned_pkts} packets -> sub-packet counter locked to 0.")


def upload_frames_to_screen(
    dev,
    frames: list,
    delays_ms: list | None = None,
    label: str = "screen",
    force: bool = True,
):
    """Execute START -> IMAGE_CFG -> DATA+ACK -> SAVE for a list of RGB565 frames."""
    # Defect D3: build_payload rejects >MAX_FRAMES outright (single-byte frame
    # counter), *before* any USB packet (alignment, START, IMAGE_CFG) is produced.
    payload_bytes, num_frames, total_chunks = build_payload(frames, delays_ms)

    # v0.8.0: dedup is a persistence concern (ak820.state), not a milestone
    # rendering one — the old `import milestone_radar` here was the §2.1
    # layering inversion.
    should_upload, digest = state.check_and_record_payload_hash(payload_bytes, label, force=force)
    if not should_upload:
        print(
            f"Screen already displays '{label}' (sha256={digest[:10]}...). Skipping USB upload (use --force to override)."
        )
        return False

    # §3.5/§4.1: the previous upload ended fully-ACKed + SAVE'd, so the firmware
    # sub-packet counter [0x2000003A] is provably 0 (FINDINGS.md §4) — skip the
    # ~1.6-2 s align_data_pipe entirely. Any failure below (or between runs)
    # left the flag dirty, so this path is only taken on provably-clean state.
    if state.pipe_known_clean():
        print("Pipe known clean from last fully-ACKed SAVE — skipping alignment (~2 s saved).")
    else:
        print("Aligning USB data pipe sub-packet counter...")
        _ensure_aligned(dev)

    # §3.5: flip to dirty pessimistically *before* any chunk leaves the host —
    # a timeout/crash between here and the fully-ACKed SAVE re-aligns next run.
    state.mark_pipe(False)

    print(f"Uploading '{label}' ({num_frames} frame(s), {total_chunks} chunks)...")

    def _progress(idx, total, acks):
        pct = int((idx + 1) / total * 100)
        print(
            f"\r  Progress: {pct:3d}% ({idx + 1}/{total} chunks, ACKs: {acks})", end="", flush=True
        )

    # m12 (§10.5): `\r` progress spam lands verbatim in journald when stdout
    # is not a tty — stream it only to interactive terminals.
    progress = _progress if sys.stdout.isatty() else None

    # D2/m3 strict ACK accounting lives in ak820.device.stream_payload: SAVE is
    # only reached when every chunk was ACKed, so on failure the digest below is
    # never recorded and the next run retries instead of skipping the upload.
    try:
        try:
            stream_payload(dev, payload_bytes, total_chunks, progress=progress)
        except UploadIncompleteError:
            # §3.4 exit-5 consumer behavior: a transient ACK loss gets exactly
            # one full retry (re-align + re-stream from chunk 0). Safe because
            # the digest is only recorded after a fully-ACKed SAVE, so a retry
            # can never double-flash or skip a payload that never landed.
            print("\n  Upload incomplete — re-aligning and retrying the full transfer once...")
            # D19: the retry align is now guarded too — if the pipe will not
            # align, abort with ProtocolError (7) instead of streaming into it.
            _ensure_aligned(dev)
            stream_payload(dev, payload_bytes, total_chunks, progress=progress)
    except UploadIncompleteError:
        if progress is not None:
            print()
        raise
    if progress is not None:
        print()

    state.save_payload_hash(digest, label)
    # §3.5: fully-ACKed + SAVE'd -> sub_packet_idx is provably 0; the next
    # upload may skip alignment. Reached only on success — every failure path
    # above propagates with the flag left dirty.
    state.mark_pipe(True)
    print("Upload and flash save complete!")
    return True


def load_frames_guarded(path: str, fit_mode: str = "fill"):
    """D12 (v0.18.0): `upload notes.txt` / `upload <dir>` / a corrupt image used
    to leak a raw PIL `UnidentifiedImageError`, `IsADirectoryError`, or
    `OSError: image file is truncated` traceback (exit 1). Loader-domain
    failures become the typed `InvalidFileError` (exit 9) — same user-error
    class as D11's exit 8: fix the path, retrying never helps.

    Lives here (not in screen.py, at the §10 line budget) and is shared by
    `upload_to_screen` and `renderers.render_upload`, so live and `--dry-run`
    paths fail identically.
    """
    try:
        return load_frames_from_file(path, fit_mode=fit_mode)
    except InvalidFileError:
        raise
    except (OSError, ValueError, SyntaxError, struct.error) as exc:
        raise InvalidFileError(f"Cannot load {path!r} as an image: {exc}") from exc


def upload_to_screen(dev, file_path: str, fit_mode: str = "fill", force: bool = True):
    """Load an image or GIF from disk and upload it to the TFT display."""
    if not os.path.exists(file_path):
        # D11 (v0.15.0): typed error -> exit 8, never a raw traceback.
        raise FileMissingError(f"File not found: {file_path}")
    frames, delays = load_frames_guarded(file_path, fit_mode=fit_mode)
    return upload_frames_to_screen(
        dev, frames, delays, label=os.path.basename(file_path), force=force
    )


def _payload_digest(frames: list, delays_ms: list | None = None) -> str:
    """sha256 of the exact wire payload — identical to what dedup records."""
    payload_bytes, _, _ = build_payload(frames, delays_ms)
    return hashlib.sha256(memoryview(payload_bytes)).hexdigest()


def describe_payload(frames: list, delays_ms: list | None = None, label: str = "screen") -> int:
    """`--dry-run` (B7): build the exact wire payload, report digest + chunk
    count, and touch neither the dedup state file nor USB. The digest is the
    same sha256 a real upload would record, so previews are byte-trustworthy."""
    payload_bytes, num_frames, total_chunks = build_payload(frames, delays_ms)
    digest = hashlib.sha256(memoryview(payload_bytes)).hexdigest()
    print(f"[dry-run] '{label}': {num_frames} frame(s), {total_chunks} chunk(s), sha256={digest}")
    return 0


def sync_clock(dev, now=None):
    """Synchronize system clock to the keyboard's internal RTC.

    The byte-level sequence lives in `ak820.device.AK820Pro.sync_clock`;
    `now` is injectable for offline tests (REVIEW.md §5.4).
    """
    dev.sync_clock(now=now)


def build_radar_frames(view: str, upcoming: list, today, slide_seconds: float = 5.0):
    """
    Build RGB565 frames and frame delays (ms) for the requested radar view.
    In SN32F290 firmware (0x16304), each frame's delay byte (max 255) is counted
    in 1ms SysTick ticks (~300ms total per frame including SPI->LCD blit).
    Repeating a static slide N times holds it steady on screen for N * ~0.30s.
    """
    from . import cards  # rendering domain: function-level per §10.2

    hero_rgb = image_to_rgb565(cards.render_hero_card(upcoming, today=today))
    board_rgb = image_to_rgb565(cards.render_board_card(upcoming, today=today))
    frames_per_slide = max(1, min(60, round(slide_seconds / 0.30)))

    if view == "hero":
        return [hero_rgb], None
    elif view == "board":
        return [board_rgb], None
    elif view == "hybrid":
        bongo_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "presets", "bongo_cat.gif"
        )
        frames: list = []
        delays: list = []
        if os.path.exists(bongo_path):
            b_frames, b_delays = load_frames_from_file(bongo_path, fit_mode="fill")
            # 4 loops of Bongo Cat (~4 seconds)
            for _ in range(4):
                frames.extend(b_frames)
                delays.extend(b_delays or [125] * len(b_frames))
        frames.extend([hero_rgb] * frames_per_slide + [board_rgb] * frames_per_slide)
        delays.extend([1275] * (frames_per_slide * 2))
        # Defect D3: clamp the hybrid total (Bongo loops + event slides) so the
        # frame count can never silently wrap & 0xFF in the GIF header.
        if len(frames) > MAX_FRAMES:
            frames = frames[:MAX_FRAMES]
            delays = delays[:MAX_FRAMES]
        return frames, delays
    else:  # "cycle": Hero slide (~5s) + Board slide (~5s)
        frames = [hero_rgb] * frames_per_slide + [board_rgb] * frames_per_slide
        delays = [1275] * len(frames)
        return frames, delays
