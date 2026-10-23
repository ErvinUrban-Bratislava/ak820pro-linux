#!/usr/bin/env python3
"""SN32F290 firmware simulator for offline protocol tests (REVIEW.md §5.3).

Models the three firmware behaviors FINDINGS.md documents:
  (a) the 64-packet sub-packet ring counter [0x2000003A] — an ACK fires only
      when the ring wraps (a full 4096-byte chunk landed),
  (b) the per-chunk ACK with the 01 5A 02 magic,
  (c) injectable faults: `drop_ack_at` (Nth chunk ACK vanishes),
      `ack_magic=False` (ACKs carry garbage — m3 regression),
      `drop_once=True` (the drop is transient — later ACKs flow again, so an
      operation-level retry recovers), and
      `disconnect_at=N` (the device is "unplugged" after N ring-wrap chunks:
      every subsequent call raises DeviceDisconnectedError — ENODEV model).
"""

from ak820.errors import DeviceDisconnectedError

ACK = b"\x01\x5A\x02" + b"\x00" * 61


class FakeTransport:
    """Emulates SN32F290: 64-packet sub-counter, per-chunk ACK, injectable faults."""

    def __init__(self, drop_ack_at=None, ack_magic=True, drop_once=False, disconnect_at=None):
        self.sub_packet_idx = 0          # firmware [0x2000003A]
        self.chunks_expected = 0
        self.chunks_seen = 0
        self.feature_log = []            # every 64-byte feature report received
        self.rx_queue = []               # ACKs awaiting read_data()
        self._buf = bytearray()
        self.drop_ack_at = drop_ack_at   # drop the Nth chunk ACK to simulate loss
        self.ack_magic = ack_magic       # False -> ACKs with garbage payload
        self.drop_once = drop_once       # True -> drop fires only once (transient)
        self._drop_fired = False
        self.disconnect_at = disconnect_at  # "unplug" after N ring-wrap chunks
        self.dead = False

    def _check_alive(self):
        if self.dead:
            raise DeviceDisconnectedError(
                "FakeTransport: simulated mid-upload unplug (ENODEV)."
            )

    # --- control pipe ---------------------------------------------------------
    def set_feature(self, report64: bytes) -> None:
        self._check_alive()
        report64 = bytes(report64)
        self.feature_log.append(report64)
        if report64[0] == 0x04 and report64[1] == 0x72:  # IMAGE_CFG
            self.chunks_expected = report64[8] | (report64[9] << 8)
            self.chunks_seen = 0

    def get_feature(self) -> bytes:
        self._check_alive()
        return b"\x00" * 64                              # canned handshake

    # --- data pipe ------------------------------------------------------------
    def write_data(self, payload: bytes) -> None:
        self._check_alive()
        self._buf += bytes(payload)
        while len(self._buf) >= 64:
            self._buf = self._buf[64:]
            self.sub_packet_idx = (self.sub_packet_idx + 1) % 64
            if self.sub_packet_idx == 0:                 # full 4096B chunk landed
                self.chunks_seen += 1
                if self.disconnect_at is not None and self.chunks_seen >= self.disconnect_at:
                    self.dead = True                     # unplug mid-transfer
                    return
                drop = self.chunks_seen == self.drop_ack_at
                if drop and self.drop_once:
                    drop = not self._drop_fired         # transient: first loss only
                    self._drop_fired = True
                if not drop:
                    self.rx_queue.append(ACK if self.ack_magic else b"\xFF" * 64)

    def read_data(self, timeout: float):
        self._check_alive()
        return self.rx_queue.pop(0) if self.rx_queue else None

    def close(self) -> None:
        pass
