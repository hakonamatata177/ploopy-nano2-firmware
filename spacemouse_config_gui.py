#!/usr/bin/env python3
"""
spacemouse_config_gui.py
========================
Config GUI for the Ploopy SpaceMouse daemon.
Edit tap actions, navigation tuning, and firmware timing values.
"""

import json
import os
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

ACTION_KEYS   = ["scroll_toggle", "rotate", "pan", "exit_3d", "nothing"]
ACTION_LABELS = ["Toggle scroll ↔ cursor", "3D rotate (orbit)",
                 "3D pan", "Exit 3D mode", "Nothing"]
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

        def combo() -> QComboBox:
            cb = QComboBox()
            for lbl in ACTION_LABELS:
                cb.addItem(lbl)
            cb.setMinimumWidth(200)
            return cb

        # ── Button Actions ────────────────────────────────────────────────────
        layout.addWidget(_section_label("Button Actions"))
        act_form = QFormLayout()
        act_form.setSpacing(8)

        self.tap1_cb = combo()
        self.tap2_cb = combo()
        self.tap3_cb = combo()
        self.hold_cb = combo()

        act_form.addRow("1 tap",   self.tap1_cb)
        act_form.addRow("2 taps",  self.tap2_cb)
        act_form.addRow("3+ taps", self.tap3_cb)
        act_form.addRow("Hold",    self.hold_cb)

        layout.addLayout(act_form)
        layout.addWidget(_separator())

        # ── Timing ───────────────────────────────────────────────────────────
        layout.addWidget(_section_label("Timing"))
        timing_form = QFormLayout()
        timing_form.setSpacing(8)

        self.hold_ms_sb     = _spinbox(100, 2000, "ms")
        self.tap_timeout_sb = _spinbox(50,  1000, "ms")

        timing_form.addRow("Hold threshold", self.hold_ms_sb)
        timing_form.addRow("Tap timeout",    self.tap_timeout_sb)

        layout.addLayout(timing_form)
        layout.addWidget(_separator())

        # ── Navigation ────────────────────────────────────────────────────────
        layout.addWidget(_section_label("Navigation"))
        nav_form = QFormLayout()
        nav_form.setSpacing(8)

        self.move_scale_sb  = _spinbox(1,  100,  "px per scroll tick")
        self.recenter_sb    = _spinbox(50, 2000, "px drift from center")
        self.scroll_div_sb  = _spinbox(1,  100,  "raw counts per tick")

        nav_form.addRow("Move scale",      self.move_scale_sb)
        nav_form.addRow("Recenter at",     self.recenter_sb)
        nav_form.addRow("Scroll divisor",  self.scroll_div_sb)

        layout.addLayout(nav_form)
        layout.addWidget(_separator())

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        apply_btn = QPushButton("Apply && restart daemon")
        apply_btn.setDefault(True)
        apply_btn.clicked.connect(self._on_apply)

        firmware_btn = QPushButton("Build firmware release")
        firmware_btn.setToolTip(
            "Tags the current firmware and pushes to GitHub.\n"
            "CI builds firmware.uf2 — check Releases in ~2 min."
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
        self.tap1_cb.setCurrentIndex(KEY_TO_IDX.get(c["actions"]["tap_1"], 0))
        self.tap2_cb.setCurrentIndex(KEY_TO_IDX.get(c["actions"]["tap_2"], 1))
        self.tap3_cb.setCurrentIndex(KEY_TO_IDX.get(c["actions"]["tap_3"], 2))
        self.hold_cb.setCurrentIndex(KEY_TO_IDX.get(c["actions"]["hold"],  3))
        self.hold_ms_sb.setValue(c["timing"]["hold_threshold_ms"])
        self.tap_timeout_sb.setValue(c["timing"]["tap_timeout_ms"])
        self.move_scale_sb.setValue(c["navigation"]["move_scale"])
        self.recenter_sb.setValue(c["navigation"]["recenter_threshold"])
        self.scroll_div_sb.setValue(c["navigation"]["scroll_divisor"])

    def _collect(self) -> dict:
        return {
            "actions": {
                "tap_1": ACTION_KEYS[self.tap1_cb.currentIndex()],
                "tap_2": ACTION_KEYS[self.tap2_cb.currentIndex()],
                "tap_3": ACTION_KEYS[self.tap3_cb.currentIndex()],
                "hold":  ACTION_KEYS[self.hold_cb.currentIndex()],
            },
            "timing": {
                "hold_threshold_ms": self.hold_ms_sb.value(),
                "tap_timeout_ms":    self.tap_timeout_sb.value(),
            },
            "navigation": {
                "move_scale":         self.move_scale_sb.value(),
                "recenter_threshold": self.recenter_sb.value(),
            },
            "firmware": {
                "hold_threshold_ms":  self.hold_ms_sb.value(),
                "recenter_threshold": self.recenter_sb.value(),
                "scroll_divisor":     self.scroll_div_sb.value(),
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
        FIRMWARE_DIR = Path.home() / "ploopy-nano2-firmware"
        if not FIRMWARE_DIR.exists():
            QMessageBox.critical(self, "Not found",
                                 f"Firmware repo not found at:\n{FIRMWARE_DIR}")
            return

        # Auto-increment version tag
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

        reply = QMessageBox.question(
            self, "Build firmware release",
            f"Tag and push {new_tag} to trigger a firmware build on GitHub?\n\n"
            "The new firmware.uf2 will appear in Releases in ~2 minutes.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            # Commit any uncommitted changes first (e.g. keymap tweaks)
            dirty = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=FIRMWARE_DIR, capture_output=True, text=True,
            ).stdout.strip()
            if dirty:
                subprocess.run(["git", "add", "-A"], cwd=FIRMWARE_DIR, check=True)
                subprocess.run(
                    ["git", "commit", "-m", f"Firmware update for {new_tag}"],
                    cwd=FIRMWARE_DIR, check=True,
                )
                subprocess.run(["git", "push", "origin", "master"],
                               cwd=FIRMWARE_DIR, check=True)

            subprocess.run(["git", "tag", new_tag], cwd=FIRMWARE_DIR, check=True)
            subprocess.run(["git", "push", "origin", new_tag],
                           cwd=FIRMWARE_DIR, check=True)
        except subprocess.CalledProcessError as ex:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Git error", f"Failed:\n{ex}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        webbrowser.open(
            "https://github.com/hakonamatata177/ploopy-nano2-firmware/releases"
        )
        QMessageBox.information(
            self, "Building",
            f"Tagged {new_tag} — firmware is building now.\n\n"
            "Download firmware.uf2 from the Releases page in ~2 minutes,\n"
            "then flash by holding the button while plugging in your Ploopy.",
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
