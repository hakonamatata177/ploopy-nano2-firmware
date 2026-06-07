// SPDX-License-Identifier: GPL-2.0-or-later
#include QMK_KEYBOARD_H

// The single button just sends F13 while held.
// All tap counting, hold detection, and mode logic runs in the Python daemon.
enum custom_keycodes { BTN_CUSTOM = SAFE_RANGE };

const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
    [0] = LAYOUT(BTN_CUSTOM)
};

bool process_record_user(uint16_t keycode, keyrecord_t *record) {
    if (keycode == BTN_CUSTOM) {
        if (record->event.pressed) register_code(KC_F13);
        else                       unregister_code(KC_F13);
        return false;
    }
    return true;
}
