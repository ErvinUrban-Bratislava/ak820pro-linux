"""Device-facing actions: upload pipeline, RTC sync, radar frame building (v0.9.0).

Extracted from `ak820_ctrl.py` so `cli.py` stays a thin dispatch layer (§10.5).
Depends only on {protocol, device, screen, state, errors} + milestone_radar
(rendering domain, function-level import — never on protocol/device paths).
"""

import os
import sys

from . import state
from .device import stream_payload
from .errors import ProtocolError, UploadIncompleteError
from .protocol import MAX_FRAMES, build_payload
from .screen import image_to_rgb565, load_frames_from_file


def upload_frames_to_screen(
    dev,
    frames: list,
    delays_ms: list = None,
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
        print(f"Screen already displays '{label}' (sha256={digest[:10]}...). Skipping USB upload (use --force to override).")
        return False

    print("Aligning USB data pipe sub-packet counter...")
    aligned_pkts, acked = dev.align_data_pipe()
    if not acked:
        # m4: streaming into an un-aligned pipe is the desync state alignment
        # exists to prevent — retry once, then abort before any chunk is sent.
        print(f"  WARN: alignment ACK not seen ({aligned_pkts} packets) — retrying alignment once...")
        aligned_pkts, acked = dev.align_data_pipe()
        if not acked:
            raise ProtocolError(
                "Data pipe will not align after 2 attempts — "
                "check the cable / re-seat the device. Upload aborted."
            )
    print(f"  Aligned: flash ACK after {aligned_pkts} packets -> sub-packet counter locked to 0.")

    print(f"Uploading '{label}' ({num_frames} frame(s), {total_chunks} chunks)...")

    def _progress(idx, total, acks):
        pct = int((idx + 1) / total * 100)
        print(f"\r  Progress: {pct:3d}% ({idx + 1}/{total} chunks, ACKs: {acks})", end="", flush=True)

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
            dev.align_data_pipe()
            stream_payload(dev, payload_bytes, total_chunks, progress=progress)
    except UploadIncompleteError:
        if progress is not None:
            print()
        raise
    if progress is not None:
        print()

    state.save_payload_hash(digest, label)
    print("Upload and flash save complete!")
    return True


def upload_to_screen(dev, file_path: str, fit_mode: str = "fill", force: bool = True):
    """Load an image or GIF from disk and upload it to the TFT display."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    frames, delays = load_frames_from_file(file_path, fit_mode=fit_mode)
    return upload_frames_to_screen(dev, frames, delays, label=os.path.basename(file_path), force=force)


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
    import milestone_radar as mr

    hero_rgb = image_to_rgb565(mr.render_hero_card(upcoming, today=today))
    board_rgb = image_to_rgb565(mr.render_board_card(upcoming, today=today))
    frames_per_slide = max(1, min(60, int(round(slide_seconds / 0.30))))

    if view == "hero":
        return [hero_rgb], None
    elif view == "board":
        return [board_rgb], None
    elif view == "hybrid":
        bongo_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "presets", "bongo_cat.gif")
        frames, delays = [], []
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
