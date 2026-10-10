# Ajazz AK820 Pro Linux Screen & System Controller

Native Linux CLI tool to control the `0.85"` 128×128 TFT screen (custom static images, animated GIFs, built-in presets) and synchronize the internal RTC clock on the **Ajazz AK820 Pro** (`VID 0x0C45`, `PID 0x800A` / `0x8009`).

See [`FINDINGS.md`](FINDINGS.md) for the complete USB HID protocol and Sonix `SN32F290` firmware disassembly notes.

---

## Requirements

- Python 3.10+
- Pillow (`python3-pil`)
- Wired USB connection (2.4 GHz wireless and Bluetooth modes do not expose the vendor HID display interfaces)
- Access to `/dev/hidraw*` (via `udev` rule or user permissions for `0c45:800a`)

---

## Usage

### 1. Probe Keyboard Interfaces
```bash
python3 ak820_ctrl.py probe
```

### 2. Upload Built-In Preset or Custom GIF / Image
```bash
# Upload bundled Bongo Cat animation
python3 ak820_ctrl.py upload presets/bongo_cat.gif

# Or use the --preset flag (bongo, cyberpunk, cat, matrix, fire, stats)
python3 ak820_ctrl.py --preset bongo

# Upload any custom GIF, PNG, or JPG (auto-resized to 128x128 RGB565 LE)
python3 ak820_ctrl.py upload /path/to/image.png --fit cover
python3 ak820_ctrl.py upload /path/to/banner.jpg --fit contain
```

### 3. Synchronize Internal Clock
```bash
python3 ak820_ctrl.py sync-clock
```
*(In `--preset` / `--image` mode, the RTC clock is also synced automatically before the image upload unless `--no-clock` is passed).*
