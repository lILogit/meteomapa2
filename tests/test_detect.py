"""Tests for rain detection on synthetic frames."""
import numpy as np
from PIL import Image
import io

from app import colormap
from app.detect import compute_verdict, load_rgba, sample_region
from app.georef import build_mapping, disk_mask, km_to_pixel_radius

M = build_mapping(52.167, 48.047, 11.267, 20.770, 680, 460)
cx, cy = M.latlon_to_pixel(48.9086, 14.5948)
PXR = km_to_pixel_radius(12.0, 48.9086, M.meters_per_pixel_x())
MASK = disk_mask(680, 460, cx, cy, PXR)


def _png(arr_rgba: np.ndarray) -> bytes:
    return np2png(arr_rgba)


def np2png(arr):
    img = Image.fromarray(arr.astype("uint8"), "RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_clear_frame():
    frame = np.zeros((460, 680, 4), dtype="uint8")  # fully transparent
    s = sample_region(load_rgba(_png(frame)), MASK)
    assert s.category == "clear"
    assert s.coverage == 0.0


def test_moderate_rain_at_center():
    frame = np.zeros((460, 460, 4), dtype="uint8")[:460, :680]  # ensure shape
    frame = np.zeros((460, 680, 4), dtype="uint8")
    # paint a disk of "moderate" green (0,160,0 = 20 dBZ) and some (0,188,0)=24
    yy, xx = np.ogrid[:460, :680]
    disk = (xx - cx) ** 2 + (yy - cy) ** 2 <= 6 ** 2
    frame[disk] = (0, 188, 0, 255)  # 24 dBZ -> light/moderate boundary
    s = sample_region(load_rgba(_png(frame)), MASK)
    assert s.category in ("light", "moderate")
    assert s.coverage > 0


def test_heavy_rain_category():
    frame = np.zeros((460, 680, 4), dtype="uint8")
    yy, xx = np.ogrid[:460, :680]
    disk = (xx - cx) ** 2 + (yy - cy) ** 2 <= 8 ** 2
    frame[disk] = (252, 132, 0, 255)  # 44 dBZ -> heavy
    s = sample_region(load_rgba(_png(frame)), MASK)
    assert s.category == "heavy"


def test_verdict_rain_now():
    from datetime import datetime, timezone
    t = datetime.now(timezone.utc)
    tl = [
        {"time": t.isoformat(), "kind": "observed", "lead_min": None,
         "category": "moderate", "intensity_mmh": 3.0, "dbz": 28, "coverage": 0.2},
    ]
    v = compute_verdict(tl, t)
    assert v["status"] == "rain_now"


def test_verdict_approaching():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    tl = [
        {"time": now.isoformat(), "kind": "observed", "lead_min": None,
         "category": "clear", "intensity_mmh": 0.0, "dbz": None, "coverage": 0.0},
        {"time": (now + timedelta(minutes=30)).isoformat(), "kind": "forecast", "lead_min": 30,
         "category": "moderate", "intensity_mmh": 3.0, "dbz": 28, "coverage": 0.15},
    ]
    v = compute_verdict(tl, now)
    assert v["status"] == "rain_approaching"
    assert v["next_event"]["in_min"] == 30
