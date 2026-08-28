"""Reusable "status at a point" computation, shared by the /api/status endpoint
and the background Telegram monitor.

``status_at`` returns the same plain-dict shape the API serializes, so callers
that don't need pydantic models (the monitor) can read fields directly.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from . import colormap
from .chmi import OBS_RE, _parse_ts
from .detect import Sample, build_timeline, center_mask, compute_verdict, load_rgba, sample_region
from .georef import ImageMapping

log = logging.getLogger("meteomapa.radar")


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def status_at(client, mapping: ImageMapping, cfg, lat: float, lon: float,
              radius_km: float) -> dict[str, Any]:
    """Compute the rain status + verdict at (lat, lon) within radius_km."""
    mask, _, _, _ = center_mask(mapping, lat, lon, radius_km)

    samples: list[tuple[datetime, Sample, str, int | None]] = []
    latest_time = None

    try:
        for name in client.observed_names_last(cfg.history_frames):
            m = OBS_RE.match(name)
            t = _parse_ts(m.group(1), m.group(2))
            latest_time = t
            rgba = load_rgba(client.get_observed_png(name))
            samples.append((t, sample_region(rgba, mask), "observed", None))
    except Exception as e:  # noqa: BLE001
        log.warning("status observed sampling failed: %s", e)

    try:
        for fr in client.get_forecast_frames():
            rgba = load_rgba(client.get_forecast_png(fr.member))
            samples.append((fr.valid, sample_region(rgba, mask), "forecast", fr.lead_min))
    except Exception as e:  # noqa: BLE001
        log.warning("status forecast sampling failed: %s", e)

    timeline = build_timeline(samples)
    now = datetime.now(timezone.utc)

    if samples:
        obs_samples = [s for s in samples if s[2] == "observed"]
        cur_t, cur_s, _, _ = obs_samples[-1] if obs_samples else samples[-1]
        current = {
            "time": _iso(cur_t), "kind": "observed", "lead_min": None,
            "category": cur_s.category, "intensity_mmh": cur_s.intensity_mmh,
            "dbz": cur_s.dbz, "coverage": cur_s.coverage,
        }
    else:
        current = {"time": _iso(now), "kind": "observed", "lead_min": None,
                   "category": "clear", "intensity_mmh": 0.0, "dbz": None, "coverage": 0.0}

    verdict = compute_verdict(timeline, now)

    return {
        "center": {"lat": lat, "lon": lon},
        "radius_km": radius_km,
        "sampled_at": _iso(now),
        "latest_observed_time": _iso(latest_time) if latest_time else None,
        "timeline": timeline,
        "current": current,
        "verdict": verdict,
        "category_meta": {
            "glyph": colormap.CATEGORY_GLYPH,
            "color": colormap.CATEGORY_COLOR,
            "label": colormap.CATEGORY_LABEL,
        },
    }


# --- severity + human summary (used by the monitor) -------------------------

_CAT_RANK = {"clear": 0, "light": 1, "moderate": 2, "heavy": 3, "storm": 4}


def cat_rank(category: str) -> int:
    return _CAT_RANK.get(category, 0)


def severity(status: dict) -> int:
    """0 clear, 1 approaching, 2..4 = light/moderate/heavy/storm at the point."""
    cur = status["current"]["category"]
    st = status["verdict"]["status"]
    if st == "rain_approaching":
        return max(1, cat_rank(cur))
    if st == "clear" and cur == "clear":
        return 0
    return cat_rank(cur)


def forecast_summary(status: dict, tz: str = "Europe/Prague") -> str:
    """One-line human forecast: onset ETA, peak, clearing — Prague time."""
    from zoneinfo import ZoneInfo
    zone = ZoneInfo(tz)
    tl = status["timeline"]
    fct = [f for f in tl if f["kind"] == "forecast"]
    cur = status["current"]

    def t(iso):
        try:
            return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(zone).strftime("%H:%M")
        except Exception:  # noqa: BLE001
            return iso

    if cur["category"] == "clear":
        onset = next((f for f in fct if f["category"] != "clear"), None)
        if not onset:
            return "Forecast: no rain expected in the next hour."
        window = [cur] + fct
        peak = max(window, key=lambda f: cat_rank(f["category"]))
        parts = [f"{colormap.CATEGORY_LABEL[onset['category']]} expected ~{t(onset['time'])} "
                 f"(+{onset['lead_min']} min)"]
        if cat_rank(peak["category"]) > cat_rank(onset["category"]):
            parts.append(f"peak {colormap.CATEGORY_LABEL[peak['category']]} ~{t(peak['time'])}")
        clearing = next((f for f in fct if f["time"] > onset["time"] and f["category"] == "clear"), None)
        if clearing:
            parts.append(f"clearing ~{t(clearing['time'])}")
        return "Forecast: " + ", ".join(parts) + "."

    # currently raining
    peak = max([cur] + fct, key=lambda f: cat_rank(f["category"]))
    clearing = next((f for f in fct if f["category"] == "clear"), None)
    parts = [f"now {colormap.CATEGORY_LABEL[cur['category']]} ({cur['intensity_mmh']:.1f} mm/h)"]
    if cat_rank(peak["category"]) > cat_rank(cur["category"]):
        parts.append(f"peaking {colormap.CATEGORY_LABEL[peak['category']]} ~{t(peak['time'])}")
    if clearing:
        parts.append(f"clearing ~{t(clearing['time'])}")
    else:
        parts.append("no clearing within the hour")
    return "Forecast: " + ", ".join(parts) + "."
