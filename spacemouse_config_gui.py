#!/usr/bin/env python3
"""
spacemouse_config_gui.py
========================
GTK4 GUI for configuring the SpaceMouse daemon and exporting firmware timing.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib

CONFIG_PATH   = Path.home() / ".config" / "spacemouse" / "config.json"
FIRMWARE_DIR  = Path.home() / "ploopy-nano2-firmware"
CONFIG_H_PATH = FIRMWARE_DIR / "config.h"

DEFAULTS = {
    "actions": {
        "double_tap": "rotate",
        "triple_tap": "pan",
    },
    "navigation": {
        "move_scale": 14,
        "recenter_threshold": 300,
    },
    "firmware": {
        "hold_threshold_ms": 400,
        "tap_timeout_ms": 150,
        "scroll_divisor_3d": 10,
    },
}

ACTION_CHOICES = ["rotate", "pan", "zoom"]
ACTION_LABELS  = ["3D rotate (orbit)", "3D pan", "3D zoom / scroll"]
ACTION_KEYS    = dict(zip(ACTION_LABELS, ACTION_CHOICES))
KEY_LABELS     = dict(zip(ACTION_CHOICES, ACTION_LABELS))


# ─── Config helpers ────────────────────────────────────────────────────────────

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
        except Exception:
            pass
    return _deep_merge(DEFAULTS, {})

def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


# ─── Window ────────────────────────────────────────────────────────────────────

class ConfigWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="SpaceMouse Configuration")
        self.set_default_size(420, -1)
        self.set_resizable(False)

        self.cfg = load_config()
        self._build()
        self._load_values()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_margin_top(16)
        outer.set_margin_bottom(16)
        outer.set_margin_start(20)
        outer.set_margin_end(20)
        self.set_child(outer)

        # ── Button Actions ────────────────────────────────────────────────────
        outer.append(self._heading("Button Actions"))
        grid = self._grid()
        outer.append(grid)

        self._fixed_row(grid, 0, "1 tap", "Toggle scroll ↔ cursor  (fixed)")
        self.double_tap_dd = self._dropdown_row(grid, 1, "2 taps")
        self.triple_tap_dd = self._dropdown_row(grid, 2, "3+ taps")
        self._fixed_row(grid, 3, "Hold", "Exit 3D mode  (fixed)")

        outer.append(Gtk.Separator())

        # ── Navigation ────────────────────────────────────────────────────────
        outer.append(self._heading("Navigation"))
        nav = self._grid()
        outer.append(nav)

        self.move_scale_spin = self._spin_row(nav, 0, "Move scale",
                                              1, 100, "px per scroll tick")
        self.recenter_spin   = self._spin_row(nav, 1, "Recenter at",
                                              50, 2000, "px drift from center")

        outer.append(Gtk.Separator())

        # ── Firmware Timing ───────────────────────────────────────────────────
        fw_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        fw_hdr.set_margin_top(10)
        fw_hdr.set_margin_bottom(4)
        lbl = Gtk.Label(label="Firmware Timing")
        lbl.add_css_class("heading")
        fw_hdr.append(lbl)
        warn = Gtk.Label(label="⚠ requires reflash after export")
        warn.add_css_class("caption")
        warn.set_opacity(0.7)
        fw_hdr.append(warn)
        outer.append(fw_hdr)

        fw = self._grid()
        outer.append(fw)

        self.hold_ms_spin    = self._spin_row(fw, 0, "Hold threshold",
                                              100, 2000, "ms")
        self.tap_timeout_spin = self._spin_row(fw, 1, "Tap timeout",
                                               50, 1000, "ms")
        self.scroll_div_spin  = self._spin_row(fw, 2, "3D scroll divisor",
                                               1, 100, "raw counts per tick")

        outer.append(Gtk.Separator())

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_margin_top(12)
        btn_row.set_halign(Gtk.Align.CENTER)
        outer.append(btn_row)

        apply_btn = Gtk.Button(label="Apply & restart daemon")
        apply_btn.add_css_class("suggested-action")
        apply_btn.connect("clicked", self._on_apply)
        btn_row.append(apply_btn)

        export_btn = Gtk.Button(label="Export firmware config.h")
        export_btn.connect("clicked", self._on_export_firmware)
        btn_row.append(export_btn)

        reset_btn = Gtk.Button(label="Reset to defaults")
        reset_btn.add_css_class("destructive-action")
        reset_btn.connect("clicked", self._on_reset)
        btn_row.append(reset_btn)

    def _heading(self, text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.add_css_class("heading")
        lbl.set_margin_top(10)
        lbl.set_margin_bottom(4)
        return lbl

    def _grid(self) -> Gtk.Grid:
        g = Gtk.Grid()
        g.set_row_spacing(6)
        g.set_column_spacing(12)
        g.set_margin_start(8)
        return g

    def _fixed_row(self, grid: Gtk.Grid, row: int,
                   label: str, value: str) -> None:
        lbl = Gtk.Label(label=label, xalign=0, width_chars=12)
        grid.attach(lbl, 0, row, 1, 1)
        val = Gtk.Label(label=value, xalign=0)
        val.set_opacity(0.55)
        grid.attach(val, 1, row, 1, 1)

    def _dropdown_row(self, grid: Gtk.Grid, row: int,
                      label: str) -> Gtk.DropDown:
        lbl = Gtk.Label(label=label, xalign=0, width_chars=12)
        grid.attach(lbl, 0, row, 1, 1)
        strings = Gtk.StringList.new(ACTION_LABELS)
        dd = Gtk.DropDown(model=strings)
        dd.set_hexpand(True)
        grid.attach(dd, 1, row, 1, 1)
        return dd

    def _spin_row(self, grid: Gtk.Grid, row: int, label: str,
                  lo: int, hi: int, unit: str) -> Gtk.SpinButton:
        lbl = Gtk.Label(label=label, xalign=0, width_chars=20)
        grid.attach(lbl, 0, row, 1, 1)

        adj = Gtk.Adjustment(lower=lo, upper=hi, step_increment=1)
        spin = Gtk.SpinButton(adjustment=adj, numeric=True)
        spin.set_digits(0)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.append(spin)
        u = Gtk.Label(label=unit, xalign=0)
        u.set_opacity(0.55)
        box.append(u)
        grid.attach(box, 1, row, 1, 1)
        return spin

    # ── Data binding ──────────────────────────────────────────────────────────

    def _load_values(self) -> None:
        c = self.cfg

        dt = c["actions"]["double_tap"]
        self.double_tap_dd.set_selected(
            ACTION_CHOICES.index(dt) if dt in ACTION_CHOICES else 0)

        tt = c["actions"]["triple_tap"]
        self.triple_tap_dd.set_selected(
            ACTION_CHOICES.index(tt) if tt in ACTION_CHOICES else 1)

        self.move_scale_spin.set_value(c["navigation"]["move_scale"])
        self.recenter_spin.set_value(c["navigation"]["recenter_threshold"])
        self.hold_ms_spin.set_value(c["firmware"]["hold_threshold_ms"])
        self.tap_timeout_spin.set_value(c["firmware"]["tap_timeout_ms"])
        self.scroll_div_spin.set_value(c["firmware"]["scroll_divisor_3d"])

    def _collect(self) -> dict:
        return {
            "actions": {
                "double_tap": ACTION_CHOICES[self.double_tap_dd.get_selected()],
                "triple_tap": ACTION_CHOICES[self.triple_tap_dd.get_selected()],
            },
            "navigation": {
                "move_scale":         int(self.move_scale_spin.get_value()),
                "recenter_threshold": int(self.recenter_spin.get_value()),
            },
            "firmware": {
                "hold_threshold_ms":  int(self.hold_ms_spin.get_value()),
                "tap_timeout_ms":     int(self.tap_timeout_spin.get_value()),
                "scroll_divisor_3d":  int(self.scroll_div_spin.get_value()),
            },
        }

    # ── Actions ───────────────────────────────────────────────────────────────

    def _on_apply(self, _btn) -> None:
        cfg = self._collect()
        save_config(cfg)
        self.cfg = cfg

        try:
            subprocess.run(
                ["systemctl", "--user", "restart", "spacemouse"],
                check=True, timeout=8,
            )
            self._dialog("Config saved and daemon restarted.", error=False)
        except Exception as ex:
            self._dialog(f"Daemon restart failed:\n{ex}", error=True)

    def _on_export_firmware(self, _btn) -> None:
        cfg = self._collect()
        save_config(cfg)
        self.cfg = cfg

        if not CONFIG_H_PATH.exists():
            self._dialog(
                f"config.h not found at:\n{CONFIG_H_PATH}\n\n"
                "Is the firmware repo at ~/ploopy-nano2-firmware?",
                error=True,
            )
            return

        text = CONFIG_H_PATH.read_text()
        fw   = cfg["firmware"]
        text = re.sub(r"(#define HOLD_THRESHOLD\s+)\d+",
                      rf"\g<1>{fw['hold_threshold_ms']}", text)
        text = re.sub(r"(#define TAP_TIMEOUT\s+)\d+",
                      rf"\g<1>{fw['tap_timeout_ms']}", text)
        text = re.sub(r"(#define SCROLL_DIVISOR_3D\s+)\d+",
                      rf"\g<1>{fw['scroll_divisor_3d']}", text)
        CONFIG_H_PATH.write_text(text)

        self._dialog(
            f"config.h updated.\n\n"
            "To apply:\n"
            "  1. Commit & push the repo\n"
            "  2. Tag a new release to trigger the CI build\n"
            "  3. Download firmware.uf2 from the release\n"
            "  4. Hold button while plugging in Ploopy →\n"
            "     copy firmware.uf2 to the drive that appears",
            error=False,
        )

    def _on_reset(self, _btn) -> None:
        dlg = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Reset to defaults?",
            secondary_text="All unsaved changes will be lost.",
        )
        dlg.connect("response", self._on_reset_response)
        dlg.present()

    def _on_reset_response(self, dlg, response) -> None:
        dlg.destroy()
        if response == Gtk.ResponseType.YES:
            self.cfg = _deep_merge(DEFAULTS, {})
            self._load_values()

    def _dialog(self, message: str, *, error: bool) -> None:
        dlg = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.ERROR if error else Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=message,
        )
        dlg.connect("response", lambda d, _: d.destroy())
        dlg.present()


# ─── Application ───────────────────────────────────────────────────────────────

class SpaceMouseConfigApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="no.ploopy.spacemouse.config")

    def do_activate(self) -> None:
        win = ConfigWindow(self)
        win.present()


def main() -> None:
    app = SpaceMouseConfigApp()
    sys.exit(app.run(sys.argv))


if __name__ == "__main__":
    main()
