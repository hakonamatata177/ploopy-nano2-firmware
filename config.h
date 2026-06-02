// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

// Four DPI levels to cycle through (index 1 = 800 CPI is the startup default)
#define PLOOPY_DPI_OPTIONS { 400, 800, 1200, 1600 }
#define PLOOPY_DPI_DEFAULT 1

// Override the board default of 64.0 — higher = less sensitive, lower = more sensitive
#undef PLOOPY_DRAGSCROLL_DIVISOR_H
#define PLOOPY_DRAGSCROLL_DIVISOR_H 32.0
#undef PLOOPY_DRAGSCROLL_DIVISOR_V
#define PLOOPY_DRAGSCROLL_DIVISOR_V 32.0

// Invert scroll direction so it feels like natural/macOS-style scrolling
#define PLOOPY_DRAGSCROLL_INVERT
