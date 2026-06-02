// SPDX-License-Identifier: GPL-2.0-or-later
#include QMK_KEYBOARD_H

// Custom keycode for the single physical button on the Nano 2.
// SAFE_RANGE ensures it doesn't collide with any built-in QMK keycodes.
enum custom_keycodes {
    BTN_CUSTOM = SAFE_RANGE,
};

const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
    [0] = LAYOUT(BTN_CUSTOM)
};

bool process_record_user(uint16_t keycode, keyrecord_t *record) {
    if (keycode == BTN_CUSTOM && record->event.pressed) {
        uint8_t mods = get_mods();

        if (mods & MOD_MASK_SHIFT) {
            // Shift + Button → cycle DPI: 400 → 800 → 1200 → 1600 → 400…
            // Saved to EEPROM so it persists across reboots.
            cycle_dpi();

        } else if (mods & MOD_MASK_CTRL) {
            // Ctrl + Button → toggle drag scroll
            toggle_drag_scroll();

        } else if (mods & MOD_MASK_ALT) {
            // Alt + Button → type a macro string
            SEND_STRING("Hello!");

        } else {
            // Button alone → toggle drag scroll (natural scroll direction via config.h)
            toggle_drag_scroll();
        }
        return false;
    }
    return true;
}
