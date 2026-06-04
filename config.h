// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

// Four DPI levels to cycle through (index 1 = 800 CPI is startup default)
#define PLOOPY_DPI_OPTIONS { 400, 800, 1200, 1600 }
#define PLOOPY_DPI_DEFAULT 0

// Scroll sensitivity in drag-scroll mode
#undef PLOOPY_DRAGSCROLL_DIVISOR_H
#define PLOOPY_DRAGSCROLL_DIVISOR_H 32.0
#undef PLOOPY_DRAGSCROLL_DIVISOR_V
#define PLOOPY_DRAGSCROLL_DIVISOR_V 32.0

// Natural/macOS-style scroll direction
#define PLOOPY_DRAGSCROLL_INVERT

// Hold time (ms) to trigger hold action instead of tap
#define HOLD_THRESHOLD 400
