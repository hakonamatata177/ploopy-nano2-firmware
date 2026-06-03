# Ploopy Nano 2 – 3D SpaceMouse Mode

Turns the Ploopy Nano 2 into a 6-axis 3D-navigation device for
**FreeCAD**, **Blender**, and **Fusion 360** (via Bottles) on
Arch Linux / Wayland (Hyprland).

---

## How it works

### Firmware side (QMK)

The button now has three behaviours depending on context:

| Situation | Action | Result |
|---|---|---|
| Outside 3D mode | Short tap (<400 ms) | Toggle scroll ↔ pointer (unchanged) |
| Outside 3D mode | Hold ≥400 ms | **Enter 3D mode** (sends F13) |
| Inside 3D mode | Short tap | Cycle active axis (sends F14) |
| Inside 3D mode | Hold ≥400 ms | **Exit 3D mode** (sends F13) |

When **3D mode is ON** the trackball no longer moves the cursor.
Instead, movement is re-routed as scroll-wheel events:

- Horizontal trackball → `REL_HWHEEL`
- Vertical trackball → `REL_WHEEL`

The Python daemon reads these scroll events and converts them into
mouse actions for the focused 3D application.

### Daemon side (Python)

`spacemouse_daemon.py` runs in the background and:

1. Listens for **F13** (mode toggle) and **F14** (axis cycle) from the trackball.
2. While 3D mode is ON, reads scroll ticks and injects virtual mouse events
   via a `/dev/uinput` virtual device.
3. Detects the focused window with `hyprctl activewindow` and picks the
   matching application profile automatically.

### Axes

| # | Name | Trackball V | Trackball H |
|---|---|---|---|
| 0 | Rotate XY | Orbit up/down | Orbit left/right |
| 1 | Translate XY | Pan up/down | Pan left/right |
| 2 | Zoom + Roll Z | Zoom in/out | Horizontal scroll (roll) |

### Application profiles

| App | Orbit | Pan | Zoom |
|---|---|---|---|
| FreeCAD (Gesture nav) | RMB drag | MMB drag | Scroll |
| Blender | MMB drag | Shift+MMB drag | Scroll |
| Fusion 360 (Bottles) | Shift+MMB drag | MMB drag | Scroll |

---

## Installation

### 1. Flash the firmware

After pushing to GitHub, the Action builds `firmware.uf2` automatically.

1. Download the artifact from the **Actions** tab on GitHub.
2. Put the Ploopy Nano 2 into bootloader mode:
   - While holding the button, plug in the USB cable.
   - The trackball appears as a USB mass-storage device (`RPI-RP2`).
3. Drag and drop `firmware.uf2` onto the `RPI-RP2` drive.
   The drive unmounts automatically when flashing is complete.

### 2. Install Python dependencies

```bash
sudo pacman -S python-evdev
# hyprctl is bundled with Hyprland and already available
```

### 3. Allow your user to create uinput devices

The daemon needs write access to `/dev/uinput` to create a virtual device.
Create a udev rule that grants access to the `input` group:

```bash
echo 'KERNEL=="uinput", GROUP="input", MODE="0660"' | \
    sudo tee /etc/udev/rules.d/99-uinput.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Make sure your user is in the `input` group (log out/in after):

```bash
sudo usermod -aG input $USER
```

### 4. Install the daemon

```bash
# Copy the script to a location on PATH and make it executable
cp ~/ploopy-nano2-firmware/spacemouse_daemon.py ~/.local/bin/spacemouse_daemon.py
chmod +x ~/.local/bin/spacemouse_daemon.py

# Install the systemd user service
mkdir -p ~/.config/systemd/user
cp ~/ploopy-nano2-firmware/spacemouse.service ~/.config/systemd/user/spacemouse.service

# Enable and start the service
systemctl --user daemon-reload
systemctl --user enable --now spacemouse
```

### 5. Verify it works

```bash
# Check service status
systemctl --user status spacemouse

# Tail the log
tail -f ~/.local/share/spacemouse-daemon/daemon.log
```

Hold the trackball button for ≥400 ms. You should see `3D mode ON` in the log.

---

## Tuning

| Location | Variable | What it does |
|---|---|---|
| `keymap.c` | `HOLD_THRESHOLD` (config.h) | Hold time (ms) to enter/exit 3D mode |
| `keymap.c` | `SCROLL_DIVISOR_3D` | Trackball sensitivity in 3D mode (higher = slower) |
| `spacemouse_daemon.py` | `MOVE_SCALE` | Virtual mouse pixels per scroll tick (higher = faster orbit/pan) |

To change scroll divisor or hold threshold, edit `config.h` / `keymap.c`,
push to GitHub, download the new `firmware.uf2`, and flash.

To change daemon sensitivity or profiles, edit `spacemouse_daemon.py` and
restart the service: `systemctl --user restart spacemouse`.

---

## Stopping / disabling

```bash
systemctl --user stop spacemouse
systemctl --user disable spacemouse
```
