"""`AK820Pro`: the fd-free protocol state machine over a HidTransport (v0.7.0).

All upload/alignment/clock-sync logic lives here and touches nothing
OS-specific — the OS boundary is `transport.py` alone. `stream_payload` is a
module-level function so any duck-typed device (including the legacy
`AK820ProDevice` in `ak820_ctrl.py`) shares one upload implementation.
"""

import datetime
import time

from .errors import AK820Error, ProtocolError, UploadIncompleteError
from .protocol import (
    ACK_MAGIC,
    CHUNK_ACK_TIMEOUT_MS,
    CHUNK_SIZE,
    CMD_DELAY,
    IMG_CFG_SETUP_DELAY,
    _cmd_packet,
    build_payload,
)


class AK820Pro:
    """Pure state machine over a transport — no fds, no ioctls, fully testable."""

    def __init__(self, transport):
        self.transport = transport
        # §3.6: handshakes are observable — a rising count is the early signal
        # of a flaky cable/hub long before an upload actually fails.
        self.handshake_failures = 0

    def close(self):
        close = getattr(self.transport, "close", None)
        if close is not None:
            close()

    # --- control pipe --------------------------------------------------------
    def send_feature(self, data64):
        """Send a 64-byte feature report with Report ID 0x00."""
        self.transport.set_feature(bytes(data64))

    def get_feature(self):
        """Read back a 64-byte feature report (handshake ACK)."""
        return self.transport.get_feature()

    def handshake(self, post_delay=CMD_DELAY):
        """Wait for MCU main loop to process SET_FEATURE, then read GET_FEATURE ACK."""
        time.sleep(CMD_DELAY)
        try:
            resp = self.get_feature()
        except (OSError, AK820Error):
            resp = None
        if resp is None:
            self.handshake_failures += 1
        time.sleep(post_delay)
        return resp

    # --- data pipe -----------------------------------------------------------
    def drain_data_acks(self):
        """Drain any pending 64-byte ACKs from the data pipe."""
        while self.transport.read_data(0.01) is not None:
            pass

    def send_data_chunk(self, chunk4096, timeout_ms=CHUNK_ACK_TIMEOUT_MS):
        """Write a 4096-byte chunk to Interface 2 and wait for the 64-byte ACK.

        Validates the ACK magic (minor defect m3): a reply that does not start
        with 01 5A 02 is *not* a chunk ACK, so the transfer state is unknown
        and we abort instead of counting it as success.
        """
        self.transport.write_data(bytes(chunk4096))
        ack = self.transport.read_data(timeout_ms / 1000.0)
        if ack is not None and not ack.startswith(ACK_MAGIC):
            raise ProtocolError(
                f"Chunk ACK magic mismatch: expected {ACK_MAGIC.hex()} prefix, "
                f"got {ack[:4].hex()}. Aborting upload with unknown device state."
            )
        return ack

    def align_data_pipe(self):
        """
        Synchronize the keyboard's 64-packet (4096B) ring index (0x2000003A) to 0
        using a two-stage (8-packet coarse + 1-packet fine) probe that stays well
        under the firmware's 2.0s watchdog while waiting for full SPI sector erases.
        """
        self.drain_data_acks()
        self.send_feature(_cmd_packet(0x18, enable=False))
        self.handshake()

        img_cfg = bytearray(64)
        img_cfg[0] = 0x04
        img_cfg[1] = 0x72
        img_cfg[2] = 0x03
        img_cfg[8] = 0x03  # 3 chunks so 2 alignment ACKs never trigger final-chunk save
        img_cfg[9] = 0x00
        self.send_feature(img_cfg)
        self.handshake(post_delay=IMG_CFG_SETUP_DELAY)
        self.drain_data_acks()

        pkt64 = b"\x00" * 64

        # Stage 1: Coarse 8-packet bursts (max 8 bursts * 200ms = 1.6s < 2.0s watchdog)
        coarse_burst = 0
        for b in range(1, 9):
            for _ in range(8):
                self.transport.write_data(pkt64)
            if self.transport.read_data(0.22) is not None:
                coarse_burst = b
                break

        # Stage 2: Advance 56 packets (putting index in 56..63), then step 1 packet at a time
        for _ in range(56):
            self.transport.write_data(pkt64)
        fine_pkt = 0
        for p in range(1, 9):
            self.transport.write_data(pkt64)
            if self.transport.read_data(0.22) is not None:
                fine_pkt = p
                break

        # Stage 3: Send the 3rd chunk (exact 4096B) + SAVE (0x02) so remaining_chunks hits 0
        # and usb_data_mode transitions cleanly 5 -> 3 -> 0 while keeping sub_packet_idx at 0.
        ack3 = self.send_data_chunk(b"\x00" * CHUNK_SIZE, timeout_ms=CHUNK_ACK_TIMEOUT_MS)
        time.sleep(0.10)
        self.send_feature(_cmd_packet(0x02, enable=False))
        self.handshake(post_delay=0.10)
        self.drain_data_acks()
        acked = (coarse_burst > 0 and fine_pkt > 0 and ack3 is not None)
        return (coarse_burst, fine_pkt), acked

    # --- high-level operations -------------------------------------------------
    def upload_frames(self, frames, delays_ms=None, align=True, progress=None):
        """Build the payload (D3 guard), align, stream with strict ACK
        accounting (D2/m3), then SAVE. Returns True only on a complete transfer."""
        payload_bytes, num_frames, total_chunks = build_payload(frames, delays_ms)
        if align:
            # m4: an un-aligned pipe is the exact desync state alignment exists
            # to prevent — retry once, then abort instead of streaming into it.
            if not self.align_data_pipe()[1]:
                if not self.align_data_pipe()[1]:
                    raise ProtocolError(
                        "Data pipe will not align after 2 attempts — "
                        "check the cable / re-seat the device."
                    )
        stream_payload(self, payload_bytes, total_chunks, progress=progress)
        return True

    def sync_clock(self, now=None):
        """Synchronize the given (or system) clock to the keyboard's internal RTC."""
        now = now or datetime.datetime.now()
        print(f"Synchronizing clock to {now.strftime('%Y-%m-%d %H:%M:%S')}...")

        # 1. START
        self.send_feature(_cmd_packet(0x18, enable=True))
        time.sleep(CMD_DELAY)

        # 2. TIME_CFG
        self.send_feature(_cmd_packet(0x28, enable=True))
        self.handshake()

        # 3. TIME_DATA
        data = bytearray(64)
        data[0] = 0x00
        data[1] = 0x01
        data[2] = 0x5A
        data[3] = now.year - 2000
        data[4] = now.month
        data[5] = now.day
        data[6] = now.hour
        data[7] = now.minute
        data[8] = now.second
        data[9] = 0x00
        data[10] = 0x04
        data[62] = 0xAA
        data[63] = 0x55
        self.send_feature(data)
        time.sleep(CMD_DELAY)

        # 4. SAVE
        self.send_feature(_cmd_packet(0x02, enable=False))
        self.handshake()
        print("Clock synced successfully!")


def stream_payload(dev, payload_bytes, total_chunks, progress=None):
    """START -> IMAGE_CFG -> per-chunk stream with strict ACK accounting -> SAVE.

    Works on any duck-typed device exposing send_feature/handshake/
    drain_data_acks/send_data_chunk (AK820Pro and the legacy shim alike).
    Defect D2: never reaches SAVE unless every chunk was ACKed; the caller
    owns digest recording and must skip it when this raises.
    `progress(idx, total, acks)` is an optional UI callback.
    """
    # Step 1: START (enable=False)
    dev.send_feature(_cmd_packet(0x18, enable=False))
    dev.handshake()

    # Step 2: IMAGE_CFG (04 72 03 ... chunk_count_le16)
    img_cfg = bytearray(64)
    img_cfg[0] = 0x04
    img_cfg[1] = 0x72
    img_cfg[2] = 0x03
    img_cfg[8] = total_chunks & 0xFF
    img_cfg[9] = (total_chunks >> 8) & 0xFF
    dev.send_feature(img_cfg)
    dev.handshake(post_delay=IMG_CFG_SETUP_DELAY)
    dev.drain_data_acks()

    # Step 3: Stream 4096-byte chunks and strictly await per-chunk ACK
    acks = 0
    for idx in range(total_chunks):
        chunk = payload_bytes[idx * CHUNK_SIZE:(idx + 1) * CHUNK_SIZE]
        ack = dev.send_data_chunk(chunk, timeout_ms=CHUNK_ACK_TIMEOUT_MS)
        if ack is not None:
            acks += 1
        if progress is not None:
            progress(idx, total_chunks, acks)

    # Defect D2: never report success on dropped ACKs. Abort *before* SAVE (04 02)
    # so a partial payload is never flashed, and never record the digest so the
    # next run retries instead of skipping the upload as "already on screen".
    if acks != total_chunks:
        raise UploadIncompleteError(
            f"Upload incomplete: device ACKed {acks}/{total_chunks} chunks. "
            "Flash save aborted; payload digest not recorded. Retry the upload.",
            acks=acks,
            total=total_chunks,
        )

    # Step 4: SAVE (04 02, no FINISH 0xF0) — only reached on a fully ACKed transfer
    time.sleep(0.10)
    dev.send_feature(_cmd_packet(0x02, enable=False))
    dev.handshake(post_delay=0.10)
    dev.drain_data_acks()
