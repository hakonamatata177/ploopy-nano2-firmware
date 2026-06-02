// SPDX-License-Identifier: GPL-2.0-or-later
#include QMK_KEYBOARD_H

enum custom_keycodes {
    BTN_CUSTOM = SAFE_RANGE,
};

// How long (ms) the button must be held to trigger the hold action instead of tap
#define HOLD_THRESHOLD 300

static uint16_t btn_timer = 0;

const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
    [0] = LAYOUT(BTN_CUSTOM)
};

bool process_record_user(uint16_t keycode, keyrecord_t *record) {
    if (keycode == BTN_CUSTOM) {
        if (record->event.pressed) {
            // Record when the button went down
            btn_timer = timer_read();
        } else {
            // Decide on release based on how long it was held
            if (timer_elapsed(btn_timer) < HOLD_THRESHOLD) {
                // Short tap → toggle drag scroll on/off
                toggle_drag_scroll();
            } else {
                // Long hold → cycle DPI: 400 → 800 → 1200 → 1600 → 400…
                cycle_dpi();
            }
        }
        return false;
    }
    return true;
}
