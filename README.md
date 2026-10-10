# Ajazz AK820 Pro Linux Screen & System Controller

Native Linux CLI tool to control the TFT screen (images, animations/GIFs, playback index) and sync the internal clock on the **Ajazz AK820 Pro** (USB Vendor `0x0C45`, Product `0x800A` / `0x8009`).

---

## Why OpenClaw Could Not Control the Screen Earlier

The Ajazz AK820 Pro exposes 4 separate USB HID interfaces on Linux:
1. **Interface 0 & 1** (`/dev/hidraw0`, `/dev/hidraw1`): Standard keyboard and mouse/consumer media controls.
2. **Interface 2** (`/dev/hidraw2`): High-throughput vendor data pipe (Usage Page `0xFF68`). Advertises **4,096-byte output reports** specifically for pushing 128×128 RGB565 raw frame streams to the SPI GC9107 TFT display.
3. **Interface 3** (`/dev/hidraw3`): Standard 64-byte control channel (Usage Page `0xFF13`). Used for keyboard settings, lighting modes, and RTC clock synchronization.

Most generic tools fail because:
- The TFT screen payload requires a **bespoke 8-byte frame header** (`0xAA 0x50 chunk_idx total_chunks 0x50 0x06`) and a **256-byte frame delay table** followed by 32,768 bytes of little-endian RGB565 pixels per frame.
- The upload must be written directly to the 4 KB report descriptor on Interface 2 (`/dev/hidraw2`), whereas settings like clock sync are sent via HID feature reports on Interface 3 (`/dev/hidraw3`).

---

## Requirements

- Python 3
- Pillow (`python3-pil`)
- Wired USB connection (the 2.4 GHz dongle and Bluetooth modes do not expose the high-speed TFT flash upload pipe)

---

## Usage

### 1. Probe Keyboard Interfaces
```bash
./ak820_ctrl.py probe
```
Output:
```
TFT Display Interface:   /dev/hidraw2
Control/Clock Interface: /dev/hidraw3
```

### 2. Synchronize Clock
Pushes system time to the keyboard's internal RTC:
```bash
./ak820_ctrl.py sync-clock
```

### 3. Upload Custom Image or GIF
Uploads any static image (PNG, JPG, BMP) or animated GIF (up to 30 frames, automatically clamped to 128×128):
```bash
# Fill/crop to 128x128 (default)
./ak820_ctrl.py upload /path/to/image.png

# Letterbox/contain with black bars
./ak820_ctrl.py upload /path/to/banner.jpg --fit contain

# Upload animated GIF
./ak820_ctrl.py upload /path/to/animation.gif
```

### 4. Switch Screen View / Built-in Index
If the screen doesn't automatically flip to your uploaded image, cycle or set the display index:
```bash
./ak820_ctrl.py select-index 0
./ak820_ctrl.py select-index 1
```
*(You can also press and hold the rotary knob for 3-5 seconds to navigate the keyboard's onboard TFT menu to select the Custom Animation).*
