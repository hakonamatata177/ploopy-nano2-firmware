// SPDX-License-Identifier: GPL-2.0-or-later
#include QMK_KEYBOARD_H

// ─── Custom keycodes ─────────────────────────────────────────────────────────
// We define one custom keycode for the single physical button on the Nano 2.
// F13 / F14 are standard QMK keycodes (KC_F13, KC_F14) – no custom enum needed.
enum custom_keycodes {
    BTN_CUSTOM = SAFE_RANGE,
};

// ─── State variables ──────────────────────────────────────────────────────────
static uint16_t btn_timer = 0;   // records when the button went down
static bool     mode_3d   = false; // true while 3D mode is active
static uint8_t  axis_3d   = 0;    // 0 = Rotate XY, 1 = Translate XY, 2 = Zoom/Roll Z

// How many raw trackball counts to accumulate before emitting one scroll tick.
// Higher = slower / less sensitive 3D movement. Tune to taste.
#define SCROLL_DIVISOR_3D 10

// ─── Keymap ───────────────────────────────────────────────────────────────────
// The Nano 2 has a single button; it lives in layer 0.
const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
    [0] = LAYOUT(BTN_CUSTOM)
};

// ─── Button tap / hold logic ──────────────────────────────────────────────────
//
// Outside 3D mode:
//   tap  (<400 ms) → toggle scroll vs pointer mode  (unchanged from original)
//   hold (≥400 ms) → enter 3D mode; send F13 to signal the Python daemon
//
// Inside 3D mode:
//   tap  (<400 ms) → cycle active axis 0→1→2→0; send F14 to signal the daemon
//   hold (≥400 ms) → exit  3D mode; send F13 to signal the daemon
//
// The Python daemon listens for F13 (mode toggle) and F14 (axis cycle).
// F13/F14 are virtually unused by FreeCAD / Blender / Fusion so they don't
// accidentally trigger anything in those apps.
bool process_record_user(uint16_t keycode, keyrecord_t *record) {
    if (keycode == BTN_CUSTOM) {
        if (record->event.pressed) {
            btn_timer = timer_read();
        } else {
            bool short_tap = timer_elapsed(btn_timer) < HOLD_THRESHOLD;

            if (mode_3d) {
                if (short_tap) {
                    // Cycle axis and tell the daemon which axis is now active
                    axis_3d = (axis_3d + 1) % 3;
                    tap_code(KC_F14);
                } else {
                    // Exit 3D mode
                    mode_3d = false;
                    tap_code(KC_F13);
                }
            } else {
                if (short_tap) {
                    // Normal: toggle drag-scroll on/off
                    toggle_drag_scroll();
                } else {
                    // Enter 3D mode, reset to axis 0
                    mode_3d = true;
                    axis_3d = 0;
                    tap_code(KC_F13);
                }
            }
        }
        return false; // tell QMK we handled this key
    }
    return true;
}

// ─── Trackball interception ───────────────────────────────────────────────────
//
// QMK calls this every polling cycle with the latest trackball delta (x, y).
// We return a modified report_mouse_t.
//
// When 3D mode is OFF: return the report unchanged → normal pointer / scroll.
//
// When 3D mode is ON:
//   • Zero out x and y so the cursor does NOT move.
//   • Accumulate raw deltas and emit scroll ticks (h / v) once the accumulator
//     exceeds SCROLL_DIVISOR_3D. This gives sub-pixel precision and avoids
//     jitter at slow movement speeds.
//   • The Python daemon reads REL_HWHEEL (h) and REL_WHEEL (v) events and
//     converts them to 3D navigation actions for the focused 3D app.
//
// Why scroll events instead of custom HID reports?
//   Standard scroll events work on every Linux desktop without extra drivers.
//   The Python daemon can read them with python-evdev and map them to whatever
//   mouse actions the target app expects.
report_mouse_t pointing_device_task_user(report_mouse_t mouse_report) {
    if (!mode_3d) {
        return mouse_report;
    }

    // Persistent accumulators survive between polling cycles
    static int16_t accum_x = 0;
    static int16_t accum_y = 0;

    accum_x += mouse_report.x;
    accum_y += mouse_report.y;

    // Suppress normal pointer movement
    mouse_report.x = 0;
    mouse_report.y = 0;
    mouse_report.h = 0;
    mouse_report.v = 0;

    // Emit one horizontal scroll tick when accumulated X exceeds threshold
    if (accum_x >= SCROLL_DIVISOR_3D) {
        mouse_report.h = 1;
        accum_x -= SCROLL_DIVISOR_3D;
    } else if (accum_x <= -SCROLL_DIVISOR_3D) {
        mouse_report.h = -1;
        accum_x += SCROLL_DIVISOR_3D;
    }

    // Emit one vertical scroll tick when accumulated Y exceeds threshold.
    // Y is inverted (negative v = scroll up) to match natural trackball feel.
    if (accum_y >= SCROLL_DIVISOR_3D) {
        mouse_report.v = -1;
        accum_y -= SCROLL_DIVISOR_3D;
    } else if (accum_y <= -SCROLL_DIVISOR_3D) {
        mouse_report.v = 1;
        accum_y += SCROLL_DIVISOR_3D;
    }

    return mouse_report;
}
