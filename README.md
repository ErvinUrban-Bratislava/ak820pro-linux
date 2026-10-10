# Ajazz AK820 Pro Linux Screen, Clock & Milestone Radar Controller

Native Linux CLI tool to control the `0.85"` 128×128 TFT screen (Family Milestone Radar, AI status cards, custom static images, animated GIFs) and synchronize the internal RTC clock on the **Ajazz AK820 Pro** (`VID 0x0C45`, `PID 0x800A` / `0x8009`).

See [`FINDINGS.md`](FINDINGS.md) for the complete USB HID protocol and Sonix `SN32F290` firmware disassembly notes.

---

## Requirements

- Python 3.10+
- Pillow (`python3-pil`)
- Wired USB connection (2.4 GHz wireless and Bluetooth modes do not expose the vendor HID display interfaces)
- Access to `/dev/hidraw*` (via `udev` rule or user permissions for `0c45:800a`)

---

## Usage

### 1. Family Milestone Radar (`radar` & `events`)
Reads recurring family events from [`/home/ervin/personal-data/ak820_events.json`](/home/ervin/personal-data/ak820_events.json), computes exact days remaining and upcoming ages/anniversary years, and pushes a high-contrast 128×128 countdown screen (with SHA-256 deduplication so daily timers never re-upload unchanged screens):

```bash
# List upcoming family milestones (add --json for AI tools)
python3 ak820_ctrl.py events list

# Add or update a milestone
python3 ak820_ctrl.py events add --title "Daughter's Birthday" --short "DAUGHTER" --date "2025-11-06" --category birthday

# Push the 2-slide alternating Radar (Hero Countdown + 3-Row Family Board, ~5s per slide)
python3 ak820_ctrl.py radar --view cycle

# Push Hybrid mode (2 loops of Bongo Cat + Hero Countdown + 3-Row Family Board)
python3 ak820_ctrl.py radar --view hybrid

# Push static single-frame Hero Card or 3-Row Board (fast ~2.2s upload)
python3 ak820_ctrl.py radar --view hero
python3 ak820_ctrl.py radar --view board
```

### 2. AI-Ready Custom Status / Sticky-Note Card (`card`)
Any AI agent (OpenClaw, Antigravity) or script can push an instant 1-frame high-contrast card (`soon`, `warn`, `urgent`, `today`, `calm`, `green`):
```bash
python3 ak820_ctrl.py card --title "REMINDER" --value "BUY GIFT" --sub "Daughter 1st B-Day" --footer "Nov 06 (27d)" --theme warn
```

### 3. Upload Any Image or Animated GIF (`upload`)
```bash
# Upload bundled Bongo Cat animation
python3 ak820_ctrl.py upload presets/bongo_cat.gif

# Upload custom GIF, PNG, or JPG (auto-resized to 128x128 RGB565 LE)
python3 ak820_ctrl.py upload /path/to/image.png --fit fill
python3 ak820_ctrl.py upload /path/to/banner.jpg --fit contain
```

### 4. Backlight & Ambient Lighting Showcase (`backlight`)
Test and configure your preferred ambient lighting profile:
```bash
# Showcase curated presets live on your desk (6 seconds each):
python3 ak820_ctrl.py backlight demo

# List all supported modes and presets
python3 ak820_ctrl.py backlight list

# Apply and save your favorite preset as default idle ambient:
python3 ak820_ctrl.py backlight set --preset warm-white --save
python3 ak820_ctrl.py backlight set --preset cyber-cyan --save
python3 ak820_ctrl.py backlight set --preset single-on --save

# Manual customization:
python3 ak820_ctrl.py backlight set --mode breath --color "#FF8C00" --speed 3 --brightness 4
python3 ak820_ctrl.py backlight off
```

### 5. Workflow Status & Notification Indicators (`status` & `run`)
Ambient status indicators with **auto-revert on your next keypress**:
```bash
# Set status directly from any script or OpenClaw automation:
python3 ak820_ctrl.py status in-progress --label "Running DuckDB Sync" # Breathing Amber
python3 ak820_ctrl.py status success     --label "Garmin Pull Complete" # Emerald Green (waits for keypress)
python3 ak820_ctrl.py status failed      --label "Backup Failed"        # Bright Red (waits for keypress)
python3 ak820_ctrl.py status idle                                       # Immediate restore to idle profile

# Or wrap any long-running shell command:
python3 ak820_ctrl.py run --label "Compile" -- make -j16
# (Pulses amber while compiling -> pulses emerald green or red on finish -> reverts when you touch the keyboard!)
```

### 6. Synchronize Internal Clock & Probe Interfaces
```bash
python3 ak820_ctrl.py sync-clock
python3 ak820_ctrl.py probe
```

---

## Automated Daily Sync (`systemd` User Timer)

`ak820-radar.timer` runs `ak820_ctrl.py radar --view cycle --sync-clock` daily at `07:00` (and on wake/boot via `Persistent=true`):
```bash
systemctl --user status ak820-radar.timer
```
