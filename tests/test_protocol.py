#!/usr/bin/env python3
"""
Offline protocol suite (v0.7.0): the `ak820/` package's upload, alignment and
clock-sync state machines exercised end-to-end against the FakeTransport
SN32F290 simulator — zero hardware required (REVIEW.md §5.4).

Regressions covered: D1 (parse_mode), D2 (missing ACK -> UploadIncompleteError,
no SAVE), D3 (>255 frames rejected before any packet), m3 (bad ACK magic ->
ProtocolError), plus ring-counter fidelity of the simulator itself and the
backward-compat re-exports on `ak820_ctrl`.
"""

import datetime
import pathlib
import sys
import unittest

# Ensure project root and tests dir are in path
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import ak820_ctrl
from ak820.device import AK820Pro
from ak820.errors import ProtocolError, UploadIncompleteError
from ak820.protocol import (
    MAX_FRAMES,
    _build_gif_header,
    _cmd_packet,
    build_payload,
    parse_hex_color,
    parse_mode,
    parse_usage_page,
)
from fake_transport import ACK, FakeTransport


def _cmds(fake):
    """First two bytes of each logged feature report (e.g. b'\\x04\\x02' == SAVE)."""
    return [p[:2] for p in fake.feature_log]


class TestFakeTransportFidelity(unittest.TestCase):
    """The simulator is only trustworthy if it models [0x2000003A] correctly."""

    def test_ring_wraps_every_64_packets_and_acks_once(self):
        fake = FakeTransport(start_idx=0)            # provably-clean power-on model
        fake.write_data(b"\x00" * 64 * 5)
        self.assertEqual(fake.sub_packet_idx, 5)
        self.assertEqual(fake.rx_queue, [])          # mid-chunk: no ACK yet
        fake.write_data(b"\x00" * 64 * 59)          # completes the 64-packet ring
        self.assertEqual(fake.sub_packet_idx, 0)
        self.assertEqual(fake.rx_queue, [ACK])
        self.assertEqual(fake.chunks_seen, 1)

    def test_image_cfg_resets_chunk_accounting(self):
        fake = FakeTransport()
        cfg = bytearray(64)
        cfg[0], cfg[1], cfg[2], cfg[8], cfg[9] = 0x04, 0x72, 0x03, 9, 0
        fake.set_feature(cfg)
        self.assertEqual(fake.chunks_expected, 9)
        self.assertEqual(fake.chunks_seen, 0)


class TestUploadProtocol(unittest.TestCase):
    FRAMES = [b"\x00" * 32768]  # 1 frame + 256B header -> 9 chunks

    def test_upload_streams_all_chunks_and_saves(self):
        fake = FakeTransport()
        self.assertTrue(AK820Pro(fake).upload_frames(self.FRAMES, align=False))
        self.assertEqual(fake.chunks_seen, 9)
        self.assertEqual(fake.chunks_expected, 9)
        self.assertIn(b"\x04\x02", _cmds(fake))     # SAVE reached the device

    def test_missing_ack_raises_not_false_success(self):  # regression D2
        fake = FakeTransport(drop_ack_at=3)
        with self.assertRaises(UploadIncompleteError) as ctx:
            AK820Pro(fake).upload_frames(self.FRAMES, align=False)
        self.assertLess(ctx.exception.acks, ctx.exception.total)
        self.assertEqual(ctx.exception.total, 9)
        self.assertNotIn(b"\x04\x02", _cmds(fake))  # aborted *before* SAVE

    def test_bad_ack_magic_is_protocol_error(self):  # regression m3
        fake = FakeTransport(ack_magic=False)
        with self.assertRaises(ProtocolError):
            AK820Pro(fake).upload_frames(self.FRAMES, align=False)
        self.assertNotIn(b"\x04\x02", _cmds(fake))

    def test_over_255_frames_rejected_before_any_packet(self):  # regression D3
        fake = FakeTransport()
        with self.assertRaises(ValueError):
            AK820Pro(fake).upload_frames([b"\x00" * 100] * (MAX_FRAMES + 1))
        self.assertEqual(fake.feature_log, [])      # zero USB traffic

    def test_progress_callback_sees_every_chunk(self):
        fake = FakeTransport()
        seen = []
        AK820Pro(fake).upload_frames(
            self.FRAMES, align=False,
            progress=lambda idx, total, acks: seen.append((idx, total, acks)),
        )
        self.assertEqual(seen[-1], (8, 9, 9))

    def test_align_data_pipe_locks_subpacket_index(self):
        fake = FakeTransport()                      # v0.13.0: starts dirty (idx 37)
        self.assertNotEqual(fake.sub_packet_idx, 0)
        (coarse, fine), acked = AK820Pro(fake).align_data_pipe()
        self.assertTrue(acked)
        self.assertGreater(coarse, 0)
        self.assertGreater(fine, 0)
        self.assertEqual(fake.sub_packet_idx, 0)

    def test_sync_clock_packet_sequence(self):
        fake = FakeTransport()
        AK820Pro(fake).sync_clock(now=datetime.datetime(2026, 10, 23, 7, 30, 15))
        time_data = next(f for f in fake.feature_log if f[2] == 0x5A)
        self.assertEqual(time_data[3:9], bytes([26, 10, 23, 7, 30, 15]))
        self.assertEqual(time_data[62:64], b"\xAA\x55")
        self.assertIn(b"\x04\x02", _cmds(fake))     # RTC SAVE


class TestPureProtocolHelpers(unittest.TestCase):
    def test_parse_mode(self):                      # regression D1
        self.assertEqual(parse_mode("breath"), 7)
        self.assertEqual(parse_mode("BREATH"), 7)
        self.assertEqual(parse_mode("13"), 13)
        with self.assertRaises(ValueError):
            parse_mode("banana")
        with self.assertRaises(ValueError):
            parse_mode("20")

    def test_cmd_packet_shape(self):
        pkt = _cmd_packet(0x18, enable=False)
        self.assertEqual(len(pkt), 64)
        self.assertEqual((pkt[0], pkt[1]), (0x04, 0x18))
        self.assertEqual(pkt[8], 0x00)

    def test_gif_header_delays_scaled_and_clamped(self):
        h = _build_gif_header(3, [100, 200, 5])
        self.assertEqual(h[0], 3)
        self.assertEqual(h[1], 20)                  # 100ms // 5
        self.assertEqual(h[2], 40)                  # 200ms // 5
        self.assertEqual(h[3], 16)                  # clamped to min 16

    def test_build_payload_pads_to_chunk_boundary(self):
        payload, n, chunks = build_payload([b"\xAB" * 100])
        self.assertEqual(n, 1)
        self.assertEqual(chunks, 1)
        self.assertEqual(len(payload), 4096)
        self.assertEqual(payload[:1], b"\x01")      # frame count byte

    def test_parse_usage_page_and_hex_color(self):
        self.assertEqual(parse_usage_page(b"\x06\x68\xff"), 0xFF68)
        self.assertEqual(parse_usage_page(b"\x05\x01"), 0x01)
        self.assertEqual(parse_hex_color("#f0a"), (255, 0, 170))


class TestBackwardCompatShim(unittest.TestCase):
    """`ak820_ctrl` must keep every protocol name importable (shim semantics)."""

    NAMES = [
        "VID", "PIDS", "CHUNK_SIZE", "MAX_FRAMES", "ACK_MAGIC", "LIGHT_MODES",
        "MAX_LIGHT_MODE", "UploadIncompleteError", "ProtocolError",
        "parse_mode", "parse_hex_color", "_cmd_packet", "_build_gif_header",
        "find_hidraw_devices", "find_keyboard_hidraw", "AK820ProDevice",
    ]

    def test_ctrl_reexports_all_protocol_names(self):
        for name in self.NAMES:
            self.assertTrue(hasattr(ak820_ctrl, name), f"ak820_ctrl.{name} missing")

    def test_reexports_are_the_same_objects(self):
        import ak820
        self.assertIs(ak820_ctrl.UploadIncompleteError, UploadIncompleteError)
        self.assertIs(ak820_ctrl.parse_mode, parse_mode)
        self.assertIs(ak820_ctrl.find_hidraw_devices, ak820.find_hidraw_devices)
        self.assertIs(ak820_ctrl.find_keyboard_hidraw, ak820.find_keyboard_hidraw)

    def test_dependency_rule_no_os_specifics_outside_transport(self):
        """CI grep (§10.2): no fd/ioctl/sysfs *code* outside transport.py (re-export
        symbol names like find_hidraw_devices in __init__ are fine); no
        milestone_radar import in protocol/device paths."""
        pkg = ROOT / "ak820"
        # fd/io primitives: transport.py ONLY (§10.2, unchanged by v0.19.0).
        fd_io_tokens = ("fcntl", "select.select", "os.open")
        # Sysfs/devfs path *strings*: transport.py or discovery.py (§18 —
        # discovery answers *which* nodes with plain reads, no fd/io).
        sysfs_path_tokens = ("/dev/hidraw", "/sys/class/hidraw")
        for py in sorted(pkg.glob("*.py")):
            text = py.read_text()
            if py.name != "transport.py":
                for token in fd_io_tokens:
                    self.assertNotIn(token, text, f"{py.name} uses {token!r}")
                if py.name != "discovery.py":
                    for token in sysfs_path_tokens:
                        self.assertNotIn(token, text, f"{py.name} uses {token!r}")
            if py.name in ("protocol.py", "device.py"):
                self.assertNotIn("import milestone_radar", text)


if __name__ == "__main__":
    unittest.main()
