# Ajazz AK820 Pro Linux Display Controller — Firmware Reverse-Engineering & Findings

Last updated: 2026-10-10

## 1. Overview & Hardware Profile

- **Device**: Ajazz AK820 Pro Mechanical Keyboard (`0.85"` 128×128 TFT color screen, GC9107 controller, 16 MB SPI flash `PY25Q128HA`, Sonix `SN32F290` ARM Cortex-M0 MCU)
- **USB IDs**: `VID 0x0C45`, `PID 0x800A` (also compatible with `0x8009`)
- **Linux HID Interfaces**:
  - **Interface 2 (`/dev/hidraw2`, Usage Page `0xFF68`)**: High-speed data pipe
    - `EP 3 OUT` (`0x03`, Interrupt OUT, 64-byte packets): Receives 4096-byte image chunks (64 packets × 64 bytes per chunk)
    - `EP 4 IN` (`0x84`, Interrupt IN, 64-byte packets): Returns 64-byte per-chunk write acknowledgments (`01 5A 02 00 ...`)
  - **Interface 3 (`/dev/hidraw3`, Usage Page `0xFF13`)**: Control pipe
    - Uses 64-byte Feature Reports (`HIDIOCSFEATURE` / `HIDIOCGFEATURE` with leading `0x00` report ID byte on Linux `hidraw` so the kernel strips the report ID byte and sends the exact 64-byte payload)

---

## 2. Verified Protocol Sequences

### System Clock Synchronization (`sync-clock` / `--sync-clock`)
1. `START`: `04 18 00 00 00 00 00 00 01 ...`
2. `TIME_CFG`: `04 28 00 00 00 00 00 00 01 ...` + `GET_FEATURE` handshake
3. `TIME_DATA`: `00 01 5A <YY> <MM> <DD> <hh> <mm> <ss> 00 04 ... AA 55` (bytes 62–63 = `0xAA 0x55`)
4. `FINISH`: `04 F0 00 00 00 00 00 00 01 ...`
5. `SAVE`: `04 02 00 00 00 00 00 00 01 ...` + `GET_FEATURE` handshake

### Image & Animation Upload (`upload <path>` / `--preset <name>`)
1. **Drain Stale ACKs**: Poll `/dev/hidraw2` non-blocking to clear any unread `01 5A 02` reports in the kernel queue.
2. **3-Chunk Two-Stage Sub-Packet Alignment (`align_data_pipe()`)**:
   - Ensures the firmware's internal 64-packet sub-counter (`[0x2000003A]`) is locked to `0` before writing real image data, without tripping the 2.0-second firmware watchdog (`[0x200000F0]`).
3. **`START`**: `04 18 00 ...` (`enable=False`) + 50 ms delay + `GET_FEATURE` handshake (`04 18 00 01 ...`)
4. **`IMAGE_CFG`**: `04 72 03 00 00 00 00 00 <chunks_lo> <chunks_hi> ...` + `GET_FEATURE` handshake (`04 72 03 01 ...`) + 350 ms `"LOADING..."` screen transition delay (`IMG_CFG_SETUP_DELAY`)
5. **Data Transfer (`EP 3 OUT` / `EP 4 IN`)**:
   - Write `b"\x00" + chunk_4096` (4097 bytes) to `/dev/hidraw2` and wait up to `1000 ms` for the 64-byte hardware ACK (`01 5A 02 ...`) from `/dev/hidraw2`.
   - Non-final chunks take ~90 ms (SPI 4 KB sector erase + 16 × 256-byte page programs).
   - The final chunk (`remaining_chunks == 0`) takes ~300–350 ms because the firmware commits the config flash sector (`0x10D04`), reloads the animation header (`0x146EC`), and renders Frame 0 before emitting the final `01 5A 02` ACK.
6. **`SAVE`**: `04 02 00 ...` + `GET_FEATURE` handshake (`04 02 00 01 ...`).

---

## 3. SPI Flash Animation Layout (`0x540000`)

- **Header (`0x000..0x0FF`, 256 bytes)**:
  - `header[0]`: `num_frames` (`uint8`, total frame count `1..255`)
  - `header[1 .. num_frames]`: Per-frame duration (`uint8`; values `<= 15` are clamped by firmware at `0x146EC` to `25` ticks, values `> 15` use the byte value directly)
  - `header[num_frames + 1 .. 255]`: Zero-padded (`0x00`)
- **Frame Data (`0x100+`, `32,768` bytes per frame)**:
  - `128 × 128` pixels in RGB565 little-endian (`lo` byte first, `hi` byte second), `0x8000` bytes per frame.

---

## 4. Firmware Root Causes Discovered via Disassembly (`SN32F290`)

Disassembly of the stock SN32F290 firmware (`AJAZZ_AK820PRO_PID_8009_V1.13_SN32F290.bin`) revealed three interacting hardware/firmware constraints that caused corrupted/split frames when not handled:

1. **Unbuffered Shared 4096-Byte RAM Buffer (`0x2000030C`) & Leftover Final ACK**:
   - The `EP 3 OUT` interrupt service routine (`0x00FF4`) writes incoming 64-byte USB packets directly into `0x2000030C[sub_packet_idx * 64]` with no double buffering.
   - When the 64th packet arrives, the main loop (`0x05C28` -> `0x08DD6`) erases the 4 KB SPI flash sector and writes `0x2000030C` to flash (~90 ms) before sending the `01 5A 02` ACK (`0x05CBC`).
   - On the final chunk (`remaining_chunks == 0`), the firmware additionally runs `0x10D04`, `0x146EC`, and `0x17C64` (~300–350 ms total) before sending the ACK. If the host uses a short timeout on a prior transfer, that final ACK sits unread in `/dev/hidraw2`, causing Chunk 0 of the next upload to return in `0 ms` and overwrite `0x2000030C` with Chunk 1 while Chunk 0 is still being written to SPI flash (splitting every frame in half).
2. **`IMAGE_CFG (0x72)` Does Not Reset the 64-Byte Sub-Packet Counter (`[0x2000003A]`)**:
   - The `0x72` handler (`0x066D8`) resets `[0x20000038]` (`chunk_ready = 0`) and `[0x2000003C]` (`current_chunk = 0`), but never clears `[0x2000003A]` (`sub_packet_idx`).
3. **2.0-Second Chunk Watchdog (`0x06836`) & `usb_data_mode` State Machine (`0x067E4`)**:
   - While `usb_data_mode == 5`, the firmware increments `[0x200000F0]` every millisecond and resets `usb_data_mode` to `0` if a full 4096-byte chunk does not complete within `2000` ticks (2.0 seconds).
   - Furthermore, while `usb_data_mode == 5`, `0x067E4` ignores all Feature Reports (`0x18`, `0x72`, `0x02`) until `remaining_chunks` (`[0x200000E8]`) reaches `0` (transitioning `usb_data_mode` from `5` to `3`), where `SAVE (0x04 0x02)` cleanly transitions `usb_data_mode` from `3` back to `0`.
   - `align_data_pipe()` solves all three constraints by running a 3-chunk alignment transaction (`num_chunks = 3`): Chunk 1 probes coarse 8-packet bursts (waiting 220 ms for a fast ~90 ms non-final ACK), Chunk 2 sends 56 packets plus single-packet steps (waiting 220 ms for the second fast ACK so `[0x2000003A]` is guaranteed `0`), and Chunk 3 sends an exact 4096-byte closing chunk followed by `SAVE (0x02)` so `usb_data_mode` returns cleanly to `0`.
