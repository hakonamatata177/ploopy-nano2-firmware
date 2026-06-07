// SPDX-License-Identifier: GPL-2.0-or-later
#include QMK_KEYBOARD_H

// ─── Custom keycodes ─────────────────────────────────────────────────────────
// We define one custom keycode for the single physical button on the Nano 2.
// F13 / F14 are standard QMK keycodes (KC_F13, KC_F14) – no custom enum needed.
enum custom_keycodes {
    BTN_CUSTOM = SAFE_RANGE,
};

// ─── State variables ──────────────────────────────────────────────────────────
static uint16_t btn_press_timer = 0;  // when the button went down
static uint16_t tap_timer       = 0;  // time of last tap release
static uint8_t  tap_count       = 0;  // taps accumulated in current multi-tap sequence
static bool     btn_held        = false;
static bool     mode_3d         = false; // true while 3D mode is active (suppresses cursor)
static uint8_t  axis_3d         = 0;     // 0 = rotate, 1 = pan

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
// Multi-tap detection: taps are counted on release; once TAP_TIMEOUT ms passes
// without another tap the accumulated count is acted on.
//
//   1 tap        → toggle scroll / pointer mode (only outside 3D mode)
//   2 taps       → if not in 3D or in pan: enter/switch to rotate (F13)
//                  if already in rotate: exit 3D (F13)
//   3+ taps      → if not in 3D or in rotate: enter/switch to pan (F15)
//                  if already in pan: exit 3D (F15)
//   hold         → exit 3D mode (sends F13 or F15 matching current axis)
//
// Daemon protocol:
//   F13 = "rotate intent" — enter rotate if off/pan, exit if already rotating
//   F15 = "pan intent"    — enter pan if off/rotate, exit if already panning
bool process_record_user(uint16_t keycode, keyrecord_t *record) {
    if (keycode == BTN_CUSTOM) {
        if (record->event.pressed) {
            btn_press_timer = timer_read();
            btn_held = true;
        } else {
            btn_held = false;
            if (timer_elapsed(btn_press_timer) < HOLD_THRESHOLD) {
                tap_count++;
                tap_timer = timer_read();
            } else {
                // Hold: exit 3D mode
                if (mode_3d) {
                    mode_3d = false;
                    tap_code(axis_3d == 0 ? KC_F13 : KC_F15);
                }
                tap_count = 0;
            }
        }
        return false;
    }
    return true;
}

// ─── Multi-tap dispatch ───────────────────────────────────────────────────────
void matrix_scan_user(void) {
    if (tap_count > 0 && !btn_held && timer_elapsed(tap_timer) > TAP_TIMEOUT) {
        switch (tap_count) {
            case 1:
                if (mode_3d) {
                    // In 3D mode: single tap exits to cursor mode
                    mode_3d = false;
                    tap_code(axis_3d == 0 ? KC_F13 : KC_F15);
                } else {
                    toggle_drag_scroll();
                }
                break;
            case 2:
                if (!mode_3d) {
                    // Off → enter rotate
                    mode_3d = true; axis_3d = 0;
                } else if (axis_3d == 0) {
                    // Already rotating → exit
                    mode_3d = false;
                } else {
                    // Panning → switch to rotate (stay in 3D)
                    axis_3d = 0;
                }
                tap_code(KC_F13);
                break;
            default: // 3+ taps
                if (!mode_3d) {
                    // Off → enter pan
                    mode_3d = true; axis_3d = 1;
                } else if (axis_3d == 1) {
                    // Already panning → exit
                    mode_3d = false;
                } else {
                    // Rotating → switch to pan (stay in 3D)
                    axis_3d = 1;
                }
                tap_code(KC_F15);
                break;
        }
        tap_count = 0;
    }
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
