#!/usr/bin/env python3
"""
spacemouse_daemon.py
====================
Converts a Ploopy Nano 2 trackball into a 6-axis 3D-navigation device for
FreeCAD, Blender, and Fusion 360 (via Bottles) on Wayland/Linux.

Firmware protocol
-----------------
The QMK firmware signals state changes by sending standard key presses:

  F13 keypress  →  toggle 3D rotate mode (axis 0: Rotate XY)
  F15 keypress  →  toggle 3D pan mode    (axis 1: Translate XY)

Tap mapping on the physical button:
  1 tap   → toggle drag-scroll (handled entirely in firmware, no F-key sent)
  2 taps  → toggle rotate mode  → F13
  3 taps  → toggle pan mode     → F15
  hold    → exit 3D mode (if active) → F13

While 3D mode is ON the firmware suppresses normal x/y cursor movement and
sends trackball deltas as scroll events instead:

  REL_HWHEEL   →  horizontal (X-axis) trackball delta
  REL_WHEEL    →  vertical   (Y-axis) trackball delta

Axes
----
  0  Rotate XY     – orbit / tumble the view
  1  Translate XY  – pan the view
  2  Zoom + Roll Z – zoom with V, horizontal-scroll / roll with H
                     (not directly selectable via tap; kept for profile use)

Usage
-----
  python spacemouse_daemon.py          # run in foreground
  systemctl --user start spacemouse   # run as service (see spacemouse.service)
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import evdev
from evdev import InputDevice, UInput, ecodes as e

# ─── Logging ──────────────────────────────────────────────────────────────────

LOG_DIR = Path.home() / ".local" / "share" / "spacemouse-daemon"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "daemon.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

AXIS_NAMES = ["Rotate XY", "Translate XY", "Zoom + Roll Z"]

# How many virtual pixels to move the mouse per one scroll tick from firmware.
# Increase if 3D navigation feels sluggish; decrease if it jumps too fast.
MOVE_SCALE = 14

# evdev key codes used by the firmware tap protocol
KEY_F13 = e.KEY_F13   # 183 – double tap: toggle rotate mode (axis 0)
KEY_F15 = e.KEY_F15   # 185 – triple tap: toggle pan mode    (axis 1)

# ─── Application profiles ─────────────────────────────────────────────────────
#
# Each profile describes how the three axes map to mouse actions in a specific
# 3D application.
#
# Keys per axis:
#   "buttons"  – list of mouse button codes to hold while moving
#   "mods"     – list of keyboard modifier codes to hold while moving
#   "zoom"     – if True, use scroll-wheel events instead of mouse movement
#
# Navigation conventions used:
#   FreeCAD  (Gesture nav, default in FC 1.0)
#     Orbit  = RMB drag      Pan = MMB drag      Zoom = scroll
#   Blender  (default)
#     Orbit  = MMB drag      Pan = Shift+MMB     Zoom = scroll
#   Fusion 360 (via Bottles)
#     Orbit  = Shift+MMB     Pan = MMB drag      Zoom = scroll

PROFILES: dict[str, dict] = {
    "freecad": {
        "name": "FreeCAD",
        0: {"buttons": [e.BTN_RIGHT],  "mods": []},
        1: {"buttons": [e.BTN_MIDDLE], "mods": []},
        2: {"zoom": True},
    },
    "blender": {
        "name": "Blender",
        0: {"buttons": [e.BTN_MIDDLE], "mods": []},
        1: {"buttons": [e.BTN_MIDDLE], "mods": [e.KEY_LEFTSHIFT]},
        2: {"zoom": True},
    },
    "fusion": {
        "name": "Fusion 360",
        0: {"buttons": [e.BTN_MIDDLE], "mods": [e.KEY_LEFTSHIFT]},
        1: {"buttons": [e.BTN_MIDDLE], "mods": []},
        2: {"zoom": True},
    },
    "default": {
        "name": "Default",
        0: {"buttons": [e.BTN_MIDDLE], "mods": []},
        1: {"buttons": [e.BTN_MIDDLE], "mods": [e.KEY_LEFTSHIFT]},
        2: {"zoom": True},
    },
}

# Maps substrings of the Hyprland window class to a profile name
WINDOW_CLASS_TO_PROFILE: dict[str, str] = {
    "freecad":  "freecad",
    "org.freecad": "freecad",
    "blender":  "blender",
    "bottles":  "fusion",   # Fusion 360 runs inside Bottles on Linux
    "fusion360": "fusion",
    "fusion":   "fusion",
}

# ─── Device helpers ───────────────────────────────────────────────────────────

def find_ploopy_devices() -> list[InputDevice]:
    """Return all evdev devices whose name contains 'ploopy' or 'nano'."""
    found = []
    for path in evdev.list_devices():
        try:
            dev = InputDevice(path)
            name_lower = dev.name.lower()
            if "ploopy" in name_lower or "nano 2" in name_lower:
                log.info("Found Ploopy device: %s  (%s)", dev.name, path)
                found.append(dev)
        except PermissionError:
            log.warning(
                "Permission denied on %s — add yourself to the 'input' group:\n"
                "  sudo usermod -aG input $USER  (then log out and back in)",
                path,
            )
        except Exception:
            pass
    return found


def create_virtual_device() -> UInput:
    """Create a uinput virtual mouse + keyboard for injecting input events."""
    caps = {
        e.EV_KEY: [
            e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE,
            e.KEY_LEFTSHIFT, e.KEY_LEFTCTRL,
        ],
        e.EV_REL: [
            e.REL_X, e.REL_Y,
            e.REL_WHEEL, e.REL_HWHEEL,
        ],
    }
    return UInput(caps, name="spacemouse-virtual", version=0x3)


# ─── Window detection ─────────────────────────────────────────────────────────

def get_active_window_class() -> str:
    """
    Return the Wayland window class of the focused window.
    Uses 'hyprctl activewindow -j' (Hyprland-specific).
    Returns an empty string if detection fails.
    """
    try:
        result = subprocess.run(
            ["hyprctl", "activewindow", "-j"],
            capture_output=True, text=True, timeout=0.5,
        )
        data = json.loads(result.stdout)
        return data.get("class", "").lower()
    except Exception:
        return ""


def get_profile(window_class: str) -> dict:
    """Pick the best application profile for the given window class string."""
    for key, profile_name in WINDOW_CLASS_TO_PROFILE.items():
        if key in window_class:
            return PROFILES[profile_name]
    return PROFILES["default"]


# ─── Daemon ───────────────────────────────────────────────────────────────────

class SpaceMouseDaemon:
    """
    Reads events from one or more Ploopy evdev nodes and injects 3D-navigation
    mouse actions via a virtual uinput device.
    """

    def __init__(self, devices: list[InputDevice], virtual: UInput) -> None:
        self.devices = devices
        self.virtual = virtual
        self.mode_3d  = False
        self.axis     = 0
        self._held_buttons: list[int] = []
        self._held_mods:    list[int] = []
        self._mouse_devices = [d for d in devices if e.EV_REL in d.capabilities()]

    # ── Cursor centering ─────────────────────────────────────────────────────

    def _center_cursor(self) -> None:
        # Warp cursor to the centre of the focused window so orbit/pan starts
        # far from any screen edge. Called when entering 3D mode or cycling
        # axes — at these moments no button is held, so FreeCAD ignores the
        # resulting wl_pointer.motion and no counter-movement is injected.
        try:
            result = subprocess.run(
                ["hyprctl", "activewindow", "-j"],
                capture_output=True, text=True, timeout=0.2,
            )
            data = json.loads(result.stdout)
            at   = data.get("at",   [0, 0])
            size = data.get("size", [1920, 1080])
            cx   = at[0] + size[0] // 2
            cy   = at[1] + size[1] // 2
            subprocess.run(
                ["hyprctl", "dispatch", "movecursor", str(cx), str(cy)],
                capture_output=True, timeout=0.1,
            )
            log.debug("Cursor centred at (%d, %d)", cx, cy)
        except Exception:
            pass

    # ── Grab helpers ────────────────────────────────────────────────────────

    def _set_grab(self, grab: bool) -> None:
        """Exclusively grab (or release) the mouse HID nodes.

        Grab prevents raw scroll events from leaking to the focused app
        while 3D mode is active; ungrab restores normal pass-through.
        """
        for dev in self._mouse_devices:
            try:
                if grab:
                    dev.grab()
                    log.info("Grabbed %s", dev.path)
                else:
                    dev.ungrab()
                    log.info("Released %s", dev.path)
            except Exception as ex:
                log.warning("grab(%s) FAILED on %s: %s — raw scroll may leak", grab, dev.path, ex)

    # ── Virtual device helpers ───────────────────────────────────────────────

    def _release_all(self) -> None:
        """Release any buttons / modifier keys that are currently held."""
        for btn in self._held_buttons:
            self.virtual.write(e.EV_KEY, btn, 0)
        for mod in self._held_mods:
            self.virtual.write(e.EV_KEY, mod, 0)
        if self._held_buttons or self._held_mods:
            self.virtual.syn()
        self._held_buttons = []
        self._held_mods    = []

    def _hold(self, buttons: list[int], mods: list[int]) -> None:
        """
        Ensure the given buttons and modifiers are held.
        If the set changed, release the old ones first.
        """
        if set(buttons) == set(self._held_buttons) and \
           set(mods)    == set(self._held_mods):
            return  # already holding the right combo – nothing to do

        self._release_all()
        for mod in mods:
            self.virtual.write(e.EV_KEY, mod, 1)
        for btn in buttons:
            self.virtual.write(e.EV_KEY, btn, 1)
        self.virtual.syn()
        self._held_buttons = list(buttons)
        self._held_mods    = list(mods)

    # ── Movement translation ─────────────────────────────────────────────────

    def _handle_movement(self, dx: int, dy: int) -> None:
        """
        Translate a (dx, dy) scroll-tick pair into a 3D-navigation input event
        for the currently focused application.

        dx  comes from REL_HWHEEL  (horizontal trackball movement)
        dy  comes from REL_WHEEL   (vertical   trackball movement)

        Axis 0 (Rotate XY) and Axis 1 (Translate XY):
          Hold the app-specific mouse button(s) + modifier(s) and move the
          virtual cursor.  The app interprets held-button + mouse-move as
          orbit or pan.

        Axis 2 (Zoom + Roll Z):
          dy → vertical   scroll  (zoom in/out in all 3D apps)
          dx → horizontal scroll  (roll / Z-axis rotation where supported)
        """
        window_class = get_active_window_class()
        profile      = get_profile(window_class)
        axis_cfg     = profile[self.axis]

        if axis_cfg.get("zoom", False):
            # Axis 2: pass movement through as scroll events
            self._release_all()
            if dy != 0:
                self.virtual.write(e.EV_REL, e.REL_WHEEL,  dy)
            if dx != 0:
                self.virtual.write(e.EV_REL, e.REL_HWHEEL, dx)
            self.virtual.syn()
        else:
            # Axes 0 / 1: hold button combo and inject virtual mouse movement.
            self._hold(axis_cfg["buttons"], axis_cfg["mods"])
            if dx != 0:
                self.virtual.write(e.EV_REL, e.REL_X,  dx * MOVE_SCALE)
            if dy != 0:
                self.virtual.write(e.EV_REL, e.REL_Y, -dy * MOVE_SCALE)
            self.virtual.syn()

    # ── Event loop ───────────────────────────────────────────────────────────

    async def _read_device(self, dev: InputDevice) -> None:
        """Read events from a single evdev device indefinitely."""
        async for event in dev.async_read_loop():
            if event.type == e.EV_KEY and event.value == 1:  # key-down only
                if event.code == KEY_F13:
                    # Double tap: toggle 3D rotation mode (axis 0)
                    if self.mode_3d:
                        self.mode_3d = False
                        self._release_all()
                        self._set_grab(False)
                        log.info("3D mode OFF")
                    else:
                        self.mode_3d = True
                        self.axis = 0
                        self._set_grab(True)
                        self._center_cursor()
                        log.info("3D mode ON  (axis 0: %s)", AXIS_NAMES[0])

                elif event.code == KEY_F15:
                    # Triple tap: toggle 3D pan mode (axis 1)
                    if self.mode_3d:
                        self.mode_3d = False
                        self._release_all()
                        self._set_grab(False)
                        log.info("3D mode OFF")
                    else:
                        self.mode_3d = True
                        self.axis = 1
                        self._set_grab(True)
                        self._center_cursor()
                        log.info("3D mode ON  (axis 1: %s)", AXIS_NAMES[1])

            elif event.type == e.EV_REL and self.mode_3d:
                dx, dy = 0, 0
                if event.code == e.REL_HWHEEL:
                    dx = event.value
                elif event.code == e.REL_WHEEL:
                    dy = event.value
                if dx or dy:
                    self._handle_movement(dx, dy)

    async def run(self) -> None:
        log.info(
            "Daemon running. Monitoring %d device(s).", len(self.devices)
        )
        # Run one reader coroutine per Ploopy device concurrently.
        # (QMK composite USB devices expose separate HID nodes for keyboard
        #  and mouse, so there may be two entries for one physical device.)
        tasks = [asyncio.create_task(self._read_device(d)) for d in self.devices]
        try:
            await asyncio.gather(*tasks)
        finally:
            self._release_all()
            for t in tasks:
                t.cancel()


# ─── Entry point ──────────────────────────────────────────────────────────────

async def main() -> None:
    log.info("spacemouse-daemon starting")

    devices = find_ploopy_devices()
    if not devices:
        log.error(
            "No Ploopy device found.\n"
            "  • Make sure the trackball is plugged in.\n"
            "  • Make sure your user is in the 'input' group:\n"
            "      sudo usermod -aG input $USER\n"
            "  • Then log out and back in."
        )
        sys.exit(1)

    virtual = create_virtual_device()
    device_path = virtual.device.path if virtual.device else f"fd={virtual.fd}"
    log.info("Virtual uinput device created: %s", device_path)

    daemon = SpaceMouseDaemon(devices, virtual)
    try:
        await daemon.run()
    except KeyboardInterrupt:
        pass
    finally:
        virtual.close()
        log.info("Daemon stopped.")


if __name__ == "__main__":
    asyncio.run(main())
