#!/usr/bin/env python3
"""
spacemouse_config_gui.py
========================
Config GUI for the Ploopy SpaceMouse daemon.
Edit tap actions, navigation tuning, and firmware timing values.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

if sys.platform == "win32":
    CONFIG_PATH = Path.home() / "AppData" / "Roaming" / "spacemouse" / "config.json"
else:
    CONFIG_PATH = Path.home() / ".config" / "spacemouse" / "config.json"

FIRMWARE_DIR  = Path.home() / "ploopy-nano2-firmware"
CONFIG_H_PATH = FIRMWARE_DIR / "config.h"

DEFAULTS: dict = {
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

ACTION_KEYS   = ["rotate", "pan", "zoom"]
ACTION_LABELS = ["3D rotate (orbit)", "3D pan", "3D zoom / scroll"]
KEY_TO_IDX    = {k: i for i, k in enumerate(ACTION_KEYS)}


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


# ─── Widgets ───────────────────────────────────────────────────────────────────

def _separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line

def _section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    font = lbl.font()
    font.setBold(True)
    font.setPointSize(font.pointSize() + 1)
    lbl.setFont(font)
    return lbl

def _fixed_row(label: str, value: str) -> tuple[QLabel, QLabel]:
    lbl = QLabel(label)
    val = QLabel(value)
    val.setStyleSheet("color: gray;")
    return lbl, val

def _action_combo() -> QComboBox:
    cb = QComboBox()
    for lbl in ACTION_LABELS:
        cb.addItem(lbl)
    cb.setMinimumWidth(200)
    return cb

def _spinbox(lo: int, hi: int, suffix: str) -> QSpinBox:
    sb = QSpinBox()
    sb.setRange(lo, hi)
    sb.setSuffix(f"  {suffix}")
    sb.setMinimumWidth(160)
    return sb


# ─── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SpaceMouse Configuration")
        self.setFixedWidth(480)

        self.cfg = load_config()
        self._build()
        self._load_values()

    def _build(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # ── Button Actions ────────────────────────────────────────────────────
        layout.addWidget(_section_label("Button Actions"))
        act_form = QFormLayout()
        act_form.setSpacing(8)

        lbl, val = _fixed_row("1 tap", "Toggle scroll ↔ cursor  (fixed)")
        act_form.addRow(lbl, val)

        self.double_tap_cb = _action_combo()
        act_form.addRow("2 taps", self.double_tap_cb)

        self.triple_tap_cb = _action_combo()
        act_form.addRow("3+ taps", self.triple_tap_cb)

        lbl, val = _fixed_row("Hold", "Exit 3D mode  (fixed)")
        act_form.addRow(lbl, val)

        layout.addLayout(act_form)
        layout.addWidget(_separator())

        # ── Navigation ────────────────────────────────────────────────────────
        layout.addWidget(_section_label("Navigation"))
        nav_form = QFormLayout()
        nav_form.setSpacing(8)

        self.move_scale_sb = _spinbox(1, 100, "px per scroll tick")
        nav_form.addRow("Move scale", self.move_scale_sb)

        self.recenter_sb = _spinbox(50, 2000, "px drift from center")
        nav_form.addRow("Recenter at", self.recenter_sb)

        layout.addLayout(nav_form)
        layout.addWidget(_separator())

        # ── Firmware Timing ───────────────────────────────────────────────────
        fw_header = QHBoxLayout()
        fw_header.addWidget(_section_label("Firmware Timing"))
        warn = QLabel("  ⚠ requires reflash after export")
        warn.setStyleSheet("color: #c07000;")
        fw_header.addWidget(warn)
        fw_header.addStretch()
        layout.addLayout(fw_header)

        fw_form = QFormLayout()
        fw_form.setSpacing(8)

        self.hold_ms_sb     = _spinbox(100, 2000, "ms")
        self.tap_timeout_sb = _spinbox(50,  1000, "ms")
        self.scroll_div_sb  = _spinbox(1,   100,  "raw counts per tick")

        fw_form.addRow("Hold threshold",    self.hold_ms_sb)
        fw_form.addRow("Tap timeout",       self.tap_timeout_sb)
        fw_form.addRow("3D scroll divisor", self.scroll_div_sb)

        layout.addLayout(fw_form)
        layout.addWidget(_separator())

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        apply_btn = QPushButton("Apply && restart daemon")
        apply_btn.setDefault(True)
        apply_btn.clicked.connect(self._on_apply)

        firmware_btn = QPushButton("Build firmware release")
        firmware_btn.setToolTip(
            "Saves timing values to config.h, commits and pushes a new git tag.\n"
            "GitHub Actions will build the firmware — check the Releases page in a few minutes."
        )
        firmware_btn.clicked.connect(self._on_build_firmware)

        reset_btn = QPushButton("Reset to defaults")
        reset_btn.clicked.connect(self._on_reset)

        btn_row.addWidget(apply_btn)
        btn_row.addWidget(firmware_btn)
        btn_row.addStretch()
        btn_row.addWidget(reset_btn)
        layout.addLayout(btn_row)

    # ── Data binding ──────────────────────────────────────────────────────────

    def _load_values(self) -> None:
        c = self.cfg
        self.double_tap_cb.setCurrentIndex(
            KEY_TO_IDX.get(c["actions"]["double_tap"], 0))
        self.triple_tap_cb.setCurrentIndex(
            KEY_TO_IDX.get(c["actions"]["triple_tap"], 1))
        self.move_scale_sb.setValue(c["navigation"]["move_scale"])
        self.recenter_sb.setValue(c["navigation"]["recenter_threshold"])
        self.hold_ms_sb.setValue(c["firmware"]["hold_threshold_ms"])
        self.tap_timeout_sb.setValue(c["firmware"]["tap_timeout_ms"])
        self.scroll_div_sb.setValue(c["firmware"]["scroll_divisor_3d"])

    def _collect(self) -> dict:
        return {
            "actions": {
                "double_tap": ACTION_KEYS[self.double_tap_cb.currentIndex()],
                "triple_tap": ACTION_KEYS[self.triple_tap_cb.currentIndex()],
            },
            "navigation": {
                "move_scale":         self.move_scale_sb.value(),
                "recenter_threshold": self.recenter_sb.value(),
            },
            "firmware": {
                "hold_threshold_ms":  self.hold_ms_sb.value(),
                "tap_timeout_ms":     self.tap_timeout_sb.value(),
                "scroll_divisor_3d":  self.scroll_div_sb.value(),
            },
        }

    # ── Actions ───────────────────────────────────────────────────────────────

    def _on_apply(self) -> None:
        cfg = self._collect()
        save_config(cfg)
        self.cfg = cfg
        if sys.platform == "win32":
            QMessageBox.information(self, "Saved",
                                    "Config saved.\n\n"
                                    "The daemon runs on Linux only — restart it there to apply.")
            return
        try:
            subprocess.run(
                ["systemctl", "--user", "restart", "spacemouse"],
                check=True, timeout=8,
            )
            QMessageBox.information(self, "Applied",
                                    "Config saved and daemon restarted.")
        except Exception as ex:
            QMessageBox.critical(self, "Error",
                                 f"Daemon restart failed:\n{ex}")

    def _on_build_firmware(self) -> None:
        cfg = self._collect()
        save_config(cfg)
        self.cfg = cfg

        if not CONFIG_H_PATH.exists():
            QMessageBox.critical(
                self, "Not found",
                f"config.h not found at:\n{CONFIG_H_PATH}\n\n"
                "Is the firmware repo at ~/ploopy-nano2-firmware?",
            )
            return

        # Patch config.h with new timing values
        text = CONFIG_H_PATH.read_text()
        fw   = cfg["firmware"]
        text = re.sub(r"(#define HOLD_THRESHOLD\s+)\d+",
                      rf"\g<1>{fw['hold_threshold_ms']}", text)
        text = re.sub(r"(#define TAP_TIMEOUT\s+)\d+",
                      rf"\g<1>{fw['tap_timeout_ms']}", text)
        text = re.sub(r"(#define SCROLL_DIVISOR_3D\s+)\d+",
                      rf"\g<1>{fw['scroll_divisor_3d']}", text)
        CONFIG_H_PATH.write_text(text)

        # Check if config.h actually changed
        diff = subprocess.run(
            ["git", "diff", "--quiet", "config.h"],
            cwd=FIRMWARE_DIR,
        )
        if diff.returncode == 0:
            QMessageBox.information(self, "No changes",
                                    "Firmware timing values are unchanged — no build needed.")
            return

        # Auto-increment the version tag
        res = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=FIRMWARE_DIR, capture_output=True, text=True,
        )
        latest = res.stdout.strip()
        if latest.startswith("v"):
            parts = latest[1:].split(".")
            parts[-1] = str(int(parts[-1]) + 1)
            new_tag = "v" + ".".join(parts)
        else:
            new_tag = "v1.0.0"

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            subprocess.run(["git", "add", "config.h"],
                           cwd=FIRMWARE_DIR, check=True)
            subprocess.run(
                ["git", "commit", "-m", f"Update firmware timing ({new_tag})"],
                cwd=FIRMWARE_DIR, check=True,
            )
            subprocess.run(["git", "push", "origin", "master"],
                           cwd=FIRMWARE_DIR, check=True)
            subprocess.run(["git", "tag", new_tag],
                           cwd=FIRMWARE_DIR, check=True)
            subprocess.run(["git", "push", "origin", new_tag],
                           cwd=FIRMWARE_DIR, check=True)
        except subprocess.CalledProcessError as ex:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Git error",
                                 f"Failed to push release:\n{ex}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        webbrowser.open(
            "https://github.com/hakonamatata177/ploopy-nano2-firmware/releases"
        )
        QMessageBox.information(
            self, "Building firmware",
            f"Tagged {new_tag} and pushed to GitHub.\n\n"
            "The firmware is building now (takes ~2 min).\n"
            "Download firmware.uf2 from the Releases page,\n"
            "then flash it by holding the Ploopy button while plugging in.",
        )

    def _on_reset(self) -> None:
        reply = QMessageBox.question(
            self, "Reset to defaults",
            "Discard all changes and reset to defaults?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.cfg = _deep_merge(DEFAULTS, {})
            self._load_values()


# ─── Self-install ──────────────────────────────────────────────────────────────

INSTALL_BIN     = Path.home() / ".local" / "bin" / "spacemouse-config"
INSTALL_DESKTOP = Path.home() / ".local" / "share" / "applications" / "spacemouse-config.desktop"

def _offer_install(app: QApplication) -> None:
    """If running as an AppImage and not yet installed, ask the user to install."""
    appimage_src = os.environ.get("APPIMAGE")
    if not appimage_src or INSTALL_DESKTOP.exists():
        return  # not an AppImage, or already installed

    reply = QMessageBox.question(
        None,
        "Install SpaceMouse Config",
        "Install SpaceMouse Config to your system?\n\n"
        "It will appear in your app launcher so you can open it anytime.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return

    try:
        INSTALL_BIN.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(appimage_src, INSTALL_BIN)
        INSTALL_BIN.chmod(0o755)

        INSTALL_DESKTOP.parent.mkdir(parents=True, exist_ok=True)
        INSTALL_DESKTOP.write_text(
            "[Desktop Entry]\n"
            "Name=SpaceMouse Config\n"
            "Comment=Configure the Ploopy SpaceMouse daemon\n"
            f"Exec={INSTALL_BIN}\n"
            "Icon=preferences-system\n"
            "Type=Application\n"
            "Categories=Settings;Utility;\n"
            "Keywords=ploopy;trackball;spacemouse;\n"
        )
        QMessageBox.information(
            None,
            "Installed",
            "SpaceMouse Config installed!\n\n"
            "You'll find it in your app launcher from now on.",
        )
    except Exception as ex:
        QMessageBox.critical(None, "Install failed", str(ex))


# ─── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("SpaceMouse Configuration")
    app.setApplicationDisplayName("SpaceMouse Configuration")
    _offer_install(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
