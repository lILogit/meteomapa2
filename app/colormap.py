"""CHMI radar reflectivity color table.

The 15 RGB swatches and their dBZ values were derived directly from CHMI's own
legend image ``scl-dbz-mmh.png`` (see ``scripts/build_colormap.py`` to reproduce).
The legend bar is ordered top=highest intensity (white = 60 dBZ) down to
bottom=lowest (dark purple = 4 dBZ), in steps of 4 dBZ.

Two colors that appear in live frames but NOT in the legend bar — pure black
``(0,0,0)`` and gray ``(196,196,196)`` — are cell-edge / clutter artifacts; they
are assigned a trace dBZ (4) so they can never escalate a reading to a storm.

mm/h is estimated from dBZ with the Marshall–Palmer relation ``Z = 200 * R**1.6``
(``Z = 10**(dbz/10)``), which reproduces the reference values printed on the
CHMI legend (8 dBZ≈0.1, 24 dBZ≈1, 40 dBZ≈10, 56 dBZ≈100 mm/h).
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

import numpy as np

RGB = Tuple[int, int, int]

# (r, g, b, dbz)
_TABLE: List[Tuple[int, int, int, int]] = [
    (56, 0, 112, 4),
    (48, 0, 168, 8),
    (0, 0, 252, 12),
    (0, 108, 192, 16),
    (0, 160, 0, 20),
    (0, 188, 0, 24),
    (52, 216, 0, 28),
    (156, 220, 0, 32),
    (224, 220, 0, 36),
    (252, 176, 0, 40),
    (252, 132, 0, 44),
    (252, 88, 0, 48),
    (252, 0, 0, 52),
    (160, 0, 0, 56),
    (252, 252, 252, 60),
    # edge / clutter colors not present in the legend bar
    (0, 0, 0, 4),
    (196, 196, 196, 4),
]

_COLORS = np.array([t[:3] for t in _TABLE], dtype=np.float32)
_DBZ = np.array([t[3] for t in _TABLE], dtype=np.float32)


def dbz_to_mmh(dbz: float) -> float:
    """Marshall–Palmer rainfall rate (mm/h) from reflectivity (dBZ)."""
    z = 10.0 ** (dbz / 10.0)
    r = (z / 200.0) ** (1.0 / 1.6)
    return round(r, 2)


def classify_pixels(rgb: np.ndarray) -> np.ndarray:
    """Map an (N, 3) uint8 RGB array to an (N,) float32 dBZ array.

    Exact RGB match first, then nearest swatch by squared distance.
    """
    if rgb.size == 0:
        return np.empty(0, dtype=np.float32)
    pts = rgb.astype(np.float32)
    # squared distance to every swatch: (N, K)
    d2 = ((pts[:, None, :] - _COLORS[None, :, :]) ** 2).sum(axis=2)
    idx = np.argmin(d2, axis=1)
    return _DBZ[idx]


def rgb_to_dbz(rgb: RGB) -> float:
    """Single-pixel convenience wrapper."""
    arr = np.array([rgb], dtype=np.uint8)
    return float(classify_pixels(arr)[0])


# --- Category classification -------------------------------------------------

# Boundaries on the 95th-percentile (peak) dBZ.
CATEGORIES: List[Tuple[str, float]] = [
    ("clear", 12),
    ("light", 25),
    ("moderate", 38),
    ("heavy", 48),
    ("storm", float("inf")),
]


def category_for_dbz(dbz: Optional[float]) -> str:
    if dbz is None or dbz < 12:
        return "clear"
    for name, upper in CATEGORIES:
        if dbz < upper:
            return name
    return "storm"


# CSS color for each category (used by both backend labels and frontend).
CATEGORY_COLOR = {
    "clear": "#3b82f6",
    "light": "#22c55e",
    "moderate": "#eab308",
    "heavy": "#f97316",
    "storm": "#ef4444",
}

CATEGORY_GLYPH = {
    "clear": "☀️",
    "light": "🌦️",
    "moderate": "🌧️",
    "heavy": "🌧️",
    "storm": "⛈️",
}

CATEGORY_LABEL = {
    "clear": "Clear",
    "light": "Light rain",
    "moderate": "Moderate rain",
    "heavy": "Heavy rain",
    "storm": "Storm / intense",
}
