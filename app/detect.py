"""Sample a radar frame at the center, classify intensity, build the timeline,
and compute the overall verdict (rain now / approaching / clearing / clear).

Detection keys on the alpha channel exclusively: a pixel counts as precipitation
only if ``alpha > 0``. The transparent-black ``(0,0,0)`` ambiguity (the legend's
"no rain" swatch) is therefore irrelevant — only opaque pixels are classified.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import numpy as np
from PIL import Image

from . import colormap
from .georef import ImageMapping, disk_mask, km_to_pixel_radius


@dataclass
class Sample:
    category: str
    intensity_mmh: float
    dbz: Optional[float]
    coverage: float


def sample_region(rgba: np.ndarray, mask: np.ndarray) -> Sample:
    """Classify precipitation inside ``mask`` of an RGBA frame."""
    region = rgba[mask]
    opaque = region[region[..., 3] > 0]
    total = int(mask.sum())
    if opaque.shape[0] == 0 or total == 0:
        return Sample(category="clear", intensity_mmh=0.0, dbz=None, coverage=0.0)
    coverage = round(opaque.shape[0] / total, 3)
    dbz_vals = colormap.classify_pixels(opaque[..., :3])
    peak_dbz = float(np.percentile(dbz_vals, 95))  # robust peak
    category = colormap.category_for_dbz(peak_dbz)
    mmh = colormap.dbz_to_mmh(peak_dbz)
    return Sample(category=category, intensity_mmh=mmh, dbz=round(peak_dbz, 1), coverage=coverage)


def load_rgba(path_or_bytes) -> np.ndarray:
    if isinstance(path_or_bytes, (bytes, bytearray)):
        img = Image.open(io.BytesIO(path_or_bytes)).convert("RGBA")
    else:
        img = Image.open(path_or_bytes).convert("RGBA")
    return np.asarray(img)


def center_mask(mapping: ImageMapping, lat: float, lon: float, radius_km: float):
    """Return (mask, cx, cy, px_radius) memoized-friendly tuple of pure values."""
    cx, cy = mapping.latlon_to_pixel(lat, lon)
    px_r = km_to_pixel_radius(radius_km, lat, mapping.meters_per_pixel_x())
    mask = disk_mask(mapping.img_w, mapping.img_h, cx, cy, px_r)
    return mask, cx, cy, px_r


# --- Timeline + verdict ------------------------------------------------------

def _frame_summary(sample: Sample, t: datetime, kind: str, lead_min: Optional[int]) -> dict:
    return {
        "time": t.isoformat().replace("+00:00", "Z"),
        "kind": kind,
        "lead_min": lead_min,
        "category": sample.category,
        "intensity_mmh": sample.intensity_mmh,
        "dbz": sample.dbz,
        "coverage": sample.coverage,
    }


def build_timeline(samples: list[tuple[datetime, Sample, str, Optional[int]]]) -> list[dict]:
    return [_frame_summary(s, t, kind, lead) for (t, s, kind, lead) in samples]


def compute_verdict(timeline: list[dict], now: datetime) -> dict:
    observed = [f for f in timeline if f["kind"] == "observed"]
    forecast = [f for f in timeline if f["kind"] == "forecast"]

    latest = observed[-1] if observed else None
    now_cat = latest["category"] if latest else "clear"

    next_event = None
    status: str
    label: str
    detail: str

    if now_cat != "clear":
        status = "rain_now"
        label = f"{colormap.CATEGORY_LABEL[now_cat]} at center now"
        detail = f"~{latest['intensity_mmh']} mm/h, {int(latest['coverage']*100)}% coverage within radius."
        # look for a clearing point in the forecast
        for f in forecast:
            if f["category"] == "clear":
                in_min = max(0, int(round((datetime.fromisoformat(f["time"].replace('Z','+00:00')) - now).total_seconds() / 60)))
                next_event = {"type": "clearing", "time": f["time"], "in_min": in_min}
                break
        else:
            # intensification?
            worse = [f for f in forecast if _rank(f["category"]) > _rank(now_cat)]
            if worse:
                f0 = worse[0]
                in_min = max(0, int(round((datetime.fromisoformat(f0["time"].replace('Z','+00:00')) - now).total_seconds() / 60)))
                next_event = {"type": "intensifying", "time": f0["time"], "in_min": in_min}
    else:
        # no rain now — first non-clear forecast?
        approaching = [f for f in forecast if f["category"] != "clear"]
        if approaching:
            f0 = approaching[0]
            in_min = max(0, int(round((datetime.fromisoformat(f0["time"].replace('Z','+00:00')) - now).total_seconds() / 60)))
            status = "rain_approaching"
            label = f"{colormap.CATEGORY_LABEL[f0['category']]} expected"
            detail = f"Forecast ~{f0['intensity_mmh']} mm/h in ~{in_min} min (at {_local(f0['time'])})."
            next_event = {"type": "onset", "time": f0["time"], "in_min": in_min}
        else:
            status = "clear"
            label = "Clear"
            detail = "No rain detected and none forecast in the next hour."

    return {"status": status, "label": label, "detail": detail, "next_event": next_event}


_RANK = {"clear": 0, "light": 1, "moderate": 2, "heavy": 3, "storm": 4}


def _rank(cat: str) -> int:
    return _RANK.get(cat, 0)


def _local(iso_utc: str) -> str:
    """Best-effort Europe/Prague rendering for backend detail strings."""
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/Prague"))
        return dt.strftime("%H:%M")
    except Exception:
        return iso_utc
