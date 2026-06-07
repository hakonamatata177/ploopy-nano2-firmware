#!/usr/bin/env python3
"""
spacemouse_daemon.py
====================
Converts a Ploopy Nano 2 trackball into a configurable 3D-navigation device.

Firmware protocol (minimal firmware, never needs reflashing)
------------------------------------------------------------
The QMK firmware sends a single signal:
  F13 key-down   →  button pressed
  F13 key-up     →  button released

All tap counting, hold detection, mode switching, and movement routing
is handled here in the daemon, driven by ~/.config/spacemouse/config.json.

Modes
-----
  cursor   – normal pointer movement (default)
  scroll   – trackball moves scroll wheel
  rotate   – 3D orbit (RMB/MMB drag depending on app)
  pan      – 3D pan  (MMB drag depending on app)

Config actions
--------------
  scroll_toggle  – switch between cursor and scroll
  rotate         – enter/exit rotate mode
  pan            – enter/exit pan mode
  exit_3d        – exit any 3D mode back to cursor
  nothing        – ignore

Usage
-----
  python spacemouse_daemon.py          # foreground
  systemctl --user start spacemouse   # as service
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

# ─── Config ───────────────────────────────────────────────────────────────────

if sys.platform == "win32":
    CONFIG_PATH = Path.home() / "AppData" / "Roaming" / "spacemouse" / "config.json"
else:
    CONFIG_PATH = Path.home() / ".config" / "spacemouse" / "config.json"

DEFAULTS: dict = {
    "actions": {
        "tap_1": "scroll_toggle",
        "tap_2": "rotate",
        "tap_3": "pan",
        "hold":  "exit_3d",
    },
    "timing": {
        "hold_threshold_ms": 400,
        "tap_timeout_ms":    150,
    },
    "navigation": {
        "move_scale":         14,
        "recenter_threshold": 300,
        "scroll_divisor":     10,
    },
}

def _deep_merge(base: dict, override: dict) -> dict:
    out = base.copy()
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            out[k] = _deep_merge(base[k], v)
        else:
            out[k] = v
    return out

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                return _deep_merge(DEFAULTS, json.load(f))
        except Exception as ex:
            logging.getLogger(__name__).warning(
                "Could not read config: %s — using defaults", ex)
    return _deep_merge(DEFAULTS, {})

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

KEY_F13     = e.KEY_F13   # button press/release signal from firmware
AXIS_NAMES  = {"rotate": "Rotate XY", "pan": "Translate XY"}
ACTION_AXIS = {"rotate": 0, "pan": 1, "zoom": 2}

# ─── Application profiles ─────────────────────────────────────────────────────

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

WINDOW_CLASS_TO_PROFILE: dict[str, str] = {
    "freecad":    "freecad",
    "org.freecad": "freecad",
    "blender":    "blender",
    "bottles":    "fusion",
    "fusion360":  "fusion",
    "fusion":     "fusion",
}

# ─── Device helpers ───────────────────────────────────────────────────────────

def find_ploopy_devices() -> list[InputDevice]:
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
                "  sudo usermod -aG input $USER  (then log out and back in)", path)
        except Exception:
            pass
    return found

def create_virtual_device() -> UInput:
    caps = {
        e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE,
                   e.KEY_LEFTSHIFT, e.KEY_LEFTCTRL],
        e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_HWHEEL],
    }
    return UInput(caps, name="spacemouse-virtual", version=0x3)

# ─── Window detection ─────────────────────────────────────────────────────────

def get_active_window_class() -> str:
    try:
        result = subprocess.run(
            ["hyprctl", "activewindow", "-j"],
            capture_output=True, text=True, timeout=0.5,
        )
        return json.loads(result.stdout).get("class", "").lower()
    except Exception:
        return ""

def get_profile(window_class: str) -> dict:
    for key, name in WINDOW_CLASS_TO_PROFILE.items():
        if key in window_class:
            return PROFILES[name]
    return PROFILES["default"]

# ─── Daemon ───────────────────────────────────────────────────────────────────

class SpaceMouseDaemon:
    def __init__(self, devices: list[InputDevice], virtual: UInput,
                 config: dict) -> None:
        self.devices = devices
        self.virtual = virtual
        self.config  = config

        # Mode: "cursor" | "scroll" | "rotate" | "pan"
        self.mode = "cursor"

        self._held_buttons: list[int] = []
        self._held_mods:    list[int] = []
        self._mouse_devices = [d for d in devices if e.EV_REL in d.capabilities()]

        # Movement accumulators
        self._accum_x = 0
        self._accum_y = 0
        self._drift_x = 0
        self._drift_y = 0

        # Tap counting state
        self._tap_count       = 0
        self._press_time: float = 0.0
        self._tap_task: asyncio.Task | None = None
        self._pre_tap_mode: str = "cursor"   # mode before optimistic single tap

    # ── Config helpers ───────────────────────────────────────────────────────

    @property
    def _hold_threshold(self) -> float:
        return self.config["timing"]["hold_threshold_ms"] / 1000.0

    @property
    def _tap_timeout(self) -> float:
        return self.config["timing"]["tap_timeout_ms"] / 1000.0

    @property
    def _move_scale(self) -> int:
        return self.config["navigation"]["move_scale"]

    @property
    def _recenter_threshold(self) -> int:
        return self.config["navigation"]["recenter_threshold"]

    @property
    def _scroll_divisor(self) -> int:
        return self.config["navigation"]["scroll_divisor"]

    @property
    def _axis(self) -> int:
        return ACTION_AXIS.get(self.mode, 0)

    # ── Cursor ───────────────────────────────────────────────────────────────

    def _center_cursor(self) -> None:
        try:
            result = subprocess.run(
                ["hyprctl", "activewindow", "-j"],
                capture_output=True, text=True, timeout=0.2,
            )
            data = json.loads(result.stdout)
            at, size = data.get("at", [0, 0]), data.get("size", [1920, 1080])
            cx, cy = at[0] + size[0] // 2, at[1] + size[1] // 2
            subprocess.run(
                ["hyprctl", "dispatch", "movecursor", str(cx), str(cy)],
                capture_output=True, timeout=0.1,
            )
            self._drift_x = 0
            self._drift_y = 0
            log.debug("Cursor centred at (%d, %d)", cx, cy)
        except Exception:
            pass

    # ── Virtual device helpers ───────────────────────────────────────────────

    def _release_all(self) -> None:
        for btn in self._held_buttons:
            self.virtual.write(e.EV_KEY, btn, 0)
        for mod in self._held_mods:
            self.virtual.write(e.EV_KEY, mod, 0)
        if self._held_buttons or self._held_mods:
            self.virtual.syn()
        self._held_buttons = []
        self._held_mods    = []

    def _hold(self, buttons: list[int], mods: list[int]) -> None:
        if (set(buttons) == set(self._held_buttons) and
                set(mods) == set(self._held_mods)):
            return
        self._release_all()
        for mod in mods:
            self.virtual.write(e.EV_KEY, mod, 1)
        for btn in buttons:
            self.virtual.write(e.EV_KEY, btn, 1)
        self.virtual.syn()
        self._held_buttons = list(buttons)
        self._held_mods    = list(mods)

    # ── Grab ────────────────────────────────────────────────────────────────

    def _grab_all(self) -> None:
        for dev in self._mouse_devices:
            try:
                dev.grab()
                log.info("Grabbed %s", dev.path)
            except Exception as ex:
                log.warning("grab FAILED on %s: %s", dev.path, ex)

    def _ungrab_all(self) -> None:
        for dev in self._mouse_devices:
            try:
                dev.ungrab()
                log.info("Released %s", dev.path)
            except Exception:
                pass

    # ── Mode switching ───────────────────────────────────────────────────────

    def _set_mode(self, new_mode: str) -> None:
        old_mode = self.mode
        if old_mode == new_mode:
            return
        self.mode = new_mode
        self._release_all()
        self._accum_x = 0
        self._accum_y = 0

        if new_mode in ("rotate", "pan"):
            self._center_cursor()

        log.info("Mode: %s → %s", old_mode, new_mode)

    def _apply_action(self, action: str) -> None:
        if action == "scroll_toggle":
            if self.mode == "scroll":
                self._set_mode("cursor")
            elif self.mode == "cursor":
                self._set_mode("scroll")
            else:
                self._set_mode("cursor")   # exit 3D → cursor
        elif action == "rotate":
            self._set_mode("cursor" if self.mode == "rotate" else "rotate")
        elif action == "pan":
            self._set_mode("cursor" if self.mode == "pan" else "pan")
        elif action == "exit_3d":
            if self.mode not in ("cursor", "scroll"):
                self._set_mode("cursor")
        # "nothing" → no-op

    # ── Tap counting ─────────────────────────────────────────────────────────

    async def _on_button_press(self, ts: float) -> None:
        # Cancel any pending tap confirmation timer — a new press has arrived
        if self._tap_task:
            self._tap_task.cancel()
            self._tap_task = None
        self._press_time = ts

    async def _on_button_release(self, ts: float) -> None:
        duration = ts - self._press_time

        if duration >= self._hold_threshold:
            # Long press — fire hold action immediately, reset tap state
            self._tap_count = 0
            action = self.config["actions"].get("hold", "exit_3d")
            self._apply_action(action)
            log.info("Hold → %s", action)
            return

        self._tap_count += 1

        if self._tap_count == 1:
            # Optimistic: fire single tap immediately so there is zero delay.
            # If a second tap arrives within the timeout window we undo this
            # and let the multi-tap timer handle the final count instead.
            self._pre_tap_mode = self.mode
            action = self.config["actions"].get("tap_1", "scroll_toggle")
            self._apply_action(action)
            log.info("Tap ×1 (optimistic) → %s", action)
            self._tap_task = asyncio.create_task(
                self._tap_confirmed(self._tap_timeout))
        else:
            # Second (or third) tap arrived — undo the optimistic single tap
            # and start the normal timer to wait for the final count.
            if self._tap_task:
                self._tap_task.cancel()
                self._tap_task = None
            self._set_mode(self._pre_tap_mode)   # undo single tap
            self._tap_task = asyncio.create_task(
                self._tap_timer(self._tap_timeout))

    async def _tap_confirmed(self, delay: float) -> None:
        """Single tap was not followed by another press — it stands."""
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        self._tap_count = 0
        self._tap_task  = None

    async def _tap_timer(self, delay: float) -> None:
        """Multi-tap: fire action once no more taps arrive within delay."""
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        count = self._tap_count
        self._tap_count = 0
        self._tap_task  = None
        key = f"tap_{min(count, 3)}"
        action = self.config["actions"].get(key, "nothing")
        self._apply_action(action)
        log.info("Tap ×%d → %s", count, action)

    # ── Movement routing ─────────────────────────────────────────────────────

    async def _handle_raw_movement(self, dx: int, dy: int) -> None:
        if self.mode == "cursor":
            if dx:
                self.virtual.write(e.EV_REL, e.REL_X, dx)
            if dy:
                self.virtual.write(e.EV_REL, e.REL_Y, dy)
            if dx or dy:
                self.virtual.syn()

        elif self.mode == "scroll":
            div = self._scroll_divisor
            self._accum_x += dx
            self._accum_y += dy
            h = int(self._accum_x / div) if abs(self._accum_x) >= div else 0
            v = int(-self._accum_y / div) if abs(self._accum_y) >= div else 0
            self._accum_x -= h * div
            self._accum_y -= -v * div
            if h:
                self.virtual.write(e.EV_REL, e.REL_HWHEEL, h)
            if v:
                self.virtual.write(e.EV_REL, e.REL_WHEEL, v)
            if h or v:
                self.virtual.syn()

        elif self.mode in ("rotate", "pan"):
            div = self._scroll_divisor
            self._accum_x += dx
            self._accum_y += dy
            tick_x = int(self._accum_x / div) if abs(self._accum_x) >= div else 0
            tick_y = int(self._accum_y / div) if abs(self._accum_y) >= div else 0
            self._accum_x -= tick_x * div
            self._accum_y -= tick_y * div
            if tick_x or tick_y:
                await self._handle_3d_movement(tick_x, tick_y)

    async def _handle_3d_movement(self, dx: int, dy: int) -> None:
        profile  = get_profile(get_active_window_class())
        axis_cfg = profile[self._axis]

        if axis_cfg.get("zoom", False):
            self._release_all()
            if dy:
                self.virtual.write(e.EV_REL, e.REL_WHEEL, dy)
            if dx:
                self.virtual.write(e.EV_REL, e.REL_HWHEEL, dx)
            self.virtual.syn()
        else:
            ms = self._move_scale
            self._drift_x += dx * ms
            self._drift_y += -dy * ms
            if (abs(self._drift_x) > self._recenter_threshold or
                    abs(self._drift_y) > self._recenter_threshold):
                self._release_all()
                self.virtual.syn()
                # Wait two Wayland frames so the button-release reaches
                # FreeCAD before the cursor warp does.  Without this sleep
                # the warp (a separate IPC path) can arrive while the app
                # still thinks the button is held, causing a phantom pan.
                await asyncio.sleep(0.04)
                self._center_cursor()
            self._hold(axis_cfg["buttons"], axis_cfg["mods"])
            if dx:
                self.virtual.write(e.EV_REL, e.REL_X,  dx * ms)
            if dy:
                self.virtual.write(e.EV_REL, e.REL_Y, -dy * ms)
            self.virtual.syn()

    # ── Event loop ───────────────────────────────────────────────────────────

    async def _read_device(self, dev: InputDevice) -> None:
        async for event in dev.async_read_loop():
            if event.type == e.EV_KEY and event.code == KEY_F13:
                ts = event.timestamp()
                if event.value == 1:
                    await self._on_button_press(ts)
                elif event.value == 0:
                    await self._on_button_release(ts)

            elif event.type == e.EV_REL:
                dx, dy = 0, 0
                if event.code == e.REL_X:
                    dx = event.value
                elif event.code == e.REL_Y:
                    dy = event.value
                if dx or dy:
                    await self._handle_raw_movement(dx, dy)

    async def run(self) -> None:
        log.info("Daemon running — grabbing %d mouse device(s).", len(self._mouse_devices))
        self._grab_all()
        tasks = [asyncio.create_task(self._read_device(d)) for d in self.devices]
        try:
            await asyncio.gather(*tasks)
        finally:
            self._release_all()
            self._ungrab_all()
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
    log.info("Virtual uinput device: %s",
             virtual.device.path if virtual.device else f"fd={virtual.fd}")

    config = load_config()
    log.info("Config: %s", CONFIG_PATH if CONFIG_PATH.exists() else "defaults")

    daemon = SpaceMouseDaemon(devices, virtual, config)
    try:
        await daemon.run()
    except KeyboardInterrupt:
        pass
    finally:
        virtual.close()
        log.info("Daemon stopped.")

if __name__ == "__main__":
    asyncio.run(main())
