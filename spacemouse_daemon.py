#!/usr/bin/env python3

import asyncio
import json
import logging
import os
import struct
import subprocess
import sys
from pathlib import Path

import evdev
from evdev import InputDevice, UInput, ecodes as e

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
        "spnav_scale":        150,
        "recenter_threshold": 300,
        "scroll_divisor":     10,
        "invert_rx":          True,
        "invert_ry":          True,
        "invert_pan_x":       False,
        "invert_pan_y":       False,
        "roll_scale":         0.5,
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

KEY_F13     = e.KEY_F13
AXIS_NAMES  = {"rotate": "Rotate XY", "pan": "Translate XY"}
ACTION_AXIS = {"rotate": 0, "pan": 1, "zoom": 2}

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

def find_keyboard_devices() -> list[InputDevice]:
    found = []
    for path in evdev.list_devices():
        try:
            dev = InputDevice(path)
            if e.EV_KEY in dev.capabilities() and e.KEY_LEFTCTRL in dev.capabilities()[e.EV_KEY]:
                log.info("Found keyboard device: %s  (%s)", dev.name, path)
                found.append(dev)
        except PermissionError:
            pass
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

SPNAV_SOCKET = os.environ.get(
    "SPNAV_SOCKET",
    os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "spnav.sock"),
)
_MOTION_FMT  = "=iiiiiiii"
_BUTTON_FMT  = "=iii"

class SpnavServer:

    def __init__(self) -> None:
        self._clients: list[asyncio.StreamWriter] = []
        self._server: asyncio.Server | None = None

    async def start(self) -> bool:
        try:
            try:
                os.unlink(SPNAV_SOCKET)
            except FileNotFoundError:
                pass
            Path(SPNAV_SOCKET).parent.mkdir(parents=True, exist_ok=True)
            self._server = await asyncio.start_unix_server(
                self._on_client, SPNAV_SOCKET)
            os.chmod(SPNAV_SOCKET, 0o666)
            log.info("SpaceMouse socket ready at %s", SPNAV_SOCKET)
            return True
        except Exception as ex:
            log.warning("Could not create spnav socket at %s: %s — "
                        "falling back to cursor-drag mode", SPNAV_SOCKET, ex)
            return False

    async def _on_client(self, reader: asyncio.StreamReader,
                         writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername") or "client"
        log.info("spnav: %s connected", peer)
        self._clients.append(writer)
        try:
            while True:
                data = await reader.read(64)
                if not data:
                    break
        finally:
            self._clients.remove(writer)
            writer.close()
            log.info("spnav: %s disconnected", peer)

    def send_motion(self, x: int = 0, y: int = 0, z: int = 0,
                    rx: int = 0, ry: int = 0, rz: int = 0) -> None:
        if not self._clients:
            return
        data = struct.pack(_MOTION_FMT, 0, x, y, z, rx, ry, rz, 1)
        self._write_all(data)

    def send_button(self, bnum: int, press: bool) -> None:
        data = struct.pack(_BUTTON_FMT, 1, int(press), bnum)
        self._write_all(data)

    def _write_all(self, data: bytes) -> None:
        dead = []
        for w in self._clients:
            try:
                w.write(data)
            except Exception:
                dead.append(w)
        for w in dead:
            try:
                self._clients.remove(w)
            except ValueError:
                pass

    async def stop(self) -> None:
        if self._server:
            self._server.close()
        try:
            os.unlink(SPNAV_SOCKET)
        except Exception:
            pass

class SpaceMouseDaemon:
    def __init__(self, devices: list[InputDevice], virtual: UInput,
                 keyboard_devices: list[InputDevice], config: dict) -> None:
        self.devices = devices
        self.keyboard_devices = keyboard_devices
        self.virtual = virtual
        self.config  = config
        self.spnav   = SpnavServer()
        self._spnav_active = False

        self.mode = "cursor"

        self._held_buttons: list[int] = []
        self._held_mods:    list[int] = []
        self._mouse_devices = [d for d in devices if e.EV_REL in d.capabilities()]

        self._accum_x = 0
        self._accum_y = 0
        self._drift_x = 0
        self._drift_y = 0

        self._tap_count       = 0
        self._press_time: float = 0.0
        self._tap_task: asyncio.Task | None = None
        self._pre_tap_mode: str = "cursor"

        self._prev_rot: tuple[int, int] = (0, 0)

        self._ctrl_held = False

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
    def _spnav_scale(self) -> int:
        return self.config["navigation"]["spnav_scale"]

    @property
    def _recenter_threshold(self) -> int:
        return self.config["navigation"]["recenter_threshold"]

    @property
    def _scroll_divisor(self) -> int:
        return self.config["navigation"]["scroll_divisor"]

    @property
    def _axis(self) -> int:
        return ACTION_AXIS.get(self.mode, 0)

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
        if new_mode == "rotate" or old_mode == "rotate":
            self._prev_rot = (0, 0)

        log.info("Mode: %s → %s", old_mode, new_mode)

    def _apply_action(self, action: str) -> None:
        if action == "scroll_toggle":
            if self.mode == "scroll":
                self._set_mode("cursor")
            elif self.mode == "cursor":
                self._set_mode("scroll")
            else:
                self._set_mode("cursor")
        elif action == "rotate":
            self._set_mode("cursor" if self.mode == "rotate" else "rotate")
        elif action == "pan":
            self._set_mode("cursor" if self.mode == "pan" else "pan")
        elif action == "exit_3d":
            if self.mode not in ("cursor", "scroll"):
                self._set_mode("cursor")

    async def _on_button_press(self, ts: float) -> None:
        if self._tap_task:
            self._tap_task.cancel()
            self._tap_task = None
        self._press_time = ts

    async def _on_button_release(self, ts: float) -> None:
        duration = ts - self._press_time

        if duration >= self._hold_threshold:
            self._tap_count = 0
            action = self.config["actions"].get("hold", "exit_3d")
            self._apply_action(action)
            log.info("Hold → %s", action)
            return

        self._tap_count += 1

        if self._tap_count == 1:
            self._pre_tap_mode = self.mode
            action = self.config["actions"].get("tap_1", "scroll_toggle")
            self._apply_action(action)
            log.info("Tap ×1 (optimistic) → %s", action)
            self._tap_task = asyncio.create_task(
                self._tap_confirmed(self._tap_timeout))
        else:
            if self._tap_task:
                self._tap_task.cancel()
                self._tap_task = None
            self._set_mode(self._pre_tap_mode)
            self._tap_task = asyncio.create_task(
                self._tap_timer(self._tap_timeout))

    async def _tap_confirmed(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        self._tap_count = 0
        self._tap_task  = None

    async def _tap_timer(self, delay: float) -> None:
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
        scale = self._move_scale

        if self.spnav._clients:
            self._release_all()
            s   = self._spnav_scale
            nav = self.config["navigation"]
            if self.mode == "rotate":
                if self._ctrl_held:
                    rx = (dy  if nav["invert_rx"] else -dy) * s
                    ry = 0
                    rz = (-dx if nav["invert_ry"] else  dx) * s
                else:
                    rx = (dy  if nav["invert_rx"] else -dy) * s
                    ry = (-dx if nav["invert_ry"] else  dx) * s

                    prev_dx, prev_dy = self._prev_rot
                    cross = prev_dx * dy - prev_dy * dx
                    rz = int(cross * nav["roll_scale"] * s)
                    self._prev_rot = (dx, dy)

                self.spnav.send_motion(rx=rx, ry=ry, rz=rz)
            elif self.mode == "pan":
                px = (-dx if nav["invert_pan_x"] else  dx) * s
                pz = ( dy if nav["invert_pan_y"] else -dy) * s
                self.spnav.send_motion(x=px, z=pz)
            return

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
            self._drift_x += dx * scale
            self._drift_y += -dy * scale
            if (abs(self._drift_x) > self._recenter_threshold or
                    abs(self._drift_y) > self._recenter_threshold):
                self._release_all()
                self.virtual.syn()
                await asyncio.sleep(0.05)
                self._center_cursor()
            self._hold(axis_cfg["buttons"], axis_cfg["mods"])
            if dx:
                self.virtual.write(e.EV_REL, e.REL_X,  dx * scale)
            if dy:
                self.virtual.write(e.EV_REL, e.REL_Y, -dy * scale)
            self.virtual.syn()

    async def _monitor_ctrl(self, dev: InputDevice) -> None:
        async for event in dev.async_read_loop():
            if event.type == e.EV_KEY and event.code == e.KEY_LEFTCTRL:
                self._ctrl_held = bool(event.value)
                log.debug("Ctrl %s", "pressed" if event.value else "released")

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
        await self.spnav.start()
        tasks = [asyncio.create_task(self._read_device(d)) for d in self.devices]
        tasks += [asyncio.create_task(self._monitor_ctrl(d)) for d in self.keyboard_devices]
        try:
            await asyncio.gather(*tasks)
        finally:
            self._release_all()
            self._ungrab_all()
            await self.spnav.stop()
            for t in tasks:
                t.cancel()

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

    keyboard_devices = find_keyboard_devices()
    if keyboard_devices:
        log.info("Found %d keyboard device(s) for Ctrl monitoring", len(keyboard_devices))
    else:
        log.warning("No keyboard devices found — Ctrl+X roll will not work")

    virtual = create_virtual_device()
    log.info("Virtual uinput device: %s",
             virtual.device.path if virtual.device else f"fd={virtual.fd}")

    config = load_config()
    log.info("Config: %s", CONFIG_PATH if CONFIG_PATH.exists() else "defaults")

    daemon = SpaceMouseDaemon(devices, virtual, keyboard_devices, config)
    try:
        await daemon.run()
    except KeyboardInterrupt:
        pass
    finally:
        virtual.close()
        log.info("Daemon stopped.")

if __name__ == "__main__":
    asyncio.run(main())
