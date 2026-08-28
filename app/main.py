"""FastAPI application: serves the radar API + static frontend."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import colormap
from .chmi import ChmiClient, FCT_PNG_RE, OBS_RE, _parse_ts
from .config import get_settings
from .detect import Sample, build_timeline, center_mask, compute_verdict, load_rgba, sample_region
from .georef import build_mapping
from .models import (Center, ForecastRef, FrameRef, FramesResponse, HealthResponse,
                     NextEvent, StatusResponse, TimelineEntry, Verdict)
from .monitor import Monitor, Store
from .radar import status_at

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("meteomapa")

cfg = get_settings()
client = ChmiClient(cfg)
mapping = build_mapping(cfg.bounds_north, cfg.bounds_south, cfg.bounds_west,
                        cfg.bounds_east, cfg.img_w, cfg.img_h)
_degraded = {"value": False}

# Telegram monitor (no-op until a bot token is configured)
store = Store(cfg)
monitor = Monitor(cfg, store, client, mapping, send=lambda *_a, **_k: None)

app = FastAPI(title="Meteomapa", version="0.1.0",
              description="CHMI weather radar with rain-at-center detection & forecast timeline")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


# --- Background refresher ----------------------------------------------------

async def _refresher():
    while True:
        try:
            client.listing_observed()
            latest = client.latest_observed_name()
            if latest:
                client.get_observed_png(latest)  # warm cache
            if cfg.include_forecast:
                client.get_forecast_frames()
            _degraded["value"] = False
        except Exception as e:  # noqa: BLE001
            log.warning("refresher error: %s", e)
            _degraded["value"] = True
        await asyncio.sleep(60)


@app.on_event("startup")
async def _startup():
    asyncio.create_task(_refresher())
    if cfg.telegram_bot_token:
        from .telegram_bot import TelegramBot
        bot = TelegramBot(cfg.telegram_bot_token, cfg, store, monitor)
        monitor.send = bot.send_message  # wire the notifier to the bot
        asyncio.create_task(bot.run())
        asyncio.create_task(_monitor_loop())
        log.info("Telegram bot + monitor enabled")
    else:
        log.info("Telegram bot disabled (set TELEGRAM_BOT_TOKEN to enable proactive alerts)")


async def _monitor_loop():
    """Periodically evaluate every active subscription and push alerts."""
    while True:
        try:
            await monitor.check_all()
        except Exception as e:  # noqa: BLE001
            log.warning("monitor loop error: %s", e)
        await asyncio.sleep(cfg.telegram_monitor_interval)


# --- Endpoints ---------------------------------------------------------------

@app.get("/api/config")
def config():
    """Expose the .env-derived configuration so the frontend has one source of truth."""
    return {
        "center_lat": cfg.center_lat,
        "center_lon": cfg.center_lon,
        "radius_km": cfg.radius_km,
        "status_refresh_seconds": cfg.status_refresh_seconds,
        "bounds": {"south": cfg.bounds_south, "west": cfg.bounds_west,
                   "north": cfg.bounds_north, "east": cfg.bounds_east},
    }


@app.get("/api/health", response_model=HealthResponse)
def health():
    latest = None
    try:
        latest = client.latest_observed_name()
        ok = True
    except Exception:  # noqa: BLE001
        ok = False
    return HealthResponse(status="ok", latest_observed=latest, cache_ok=ok)


@app.get("/api/frames", response_model=FramesResponse)
def frames(history: int = Query(default=12, ge=1, le=288), forecast: bool = True):
    try:
        obs_names = client.observed_names_last(history)
    except Exception as e:  # noqa: BLE001
        log.warning("frames listing failed: %s", e)
        obs_names = []

    observed: list[FrameRef] = []
    latest_time = None
    for i, name in enumerate(obs_names):
        m = OBS_RE.match(name)
        t = _parse_ts(m.group(1), m.group(2))
        if i == len(obs_names) - 1:
            latest_time = t
        observed.append(FrameRef(time=_iso(t), url=f"/api/img/obs/{name}",
                                 is_latest=(i == len(obs_names) - 1)))

    forecast_refs: list[ForecastRef] = []
    issue_time = None
    if forecast and cfg.include_forecast:
        try:
            frs = client.get_forecast_frames()
            for fr in frs:
                if issue_time is None:
                    issue_time = fr.issue
                forecast_refs.append(ForecastRef(
                    time=_iso(fr.valid), lead_min=fr.lead_min,
                    issue=_iso(fr.issue), url=f"/api/img/fct/{fr.member}"))
        except Exception as e:  # noqa: BLE001
            log.warning("forecast fetch failed: %s", e)

    return FramesResponse(
        center=Center(lat=cfg.center_lat, lon=cfg.center_lon),
        generated_at=_iso(_now()),
        observed=observed,
        forecast=forecast_refs,
        latest_observed_time=_iso(latest_time) if latest_time else None,
        forecast_issue_time=_iso(issue_time) if issue_time else None,
        refresh_seconds=cfg.refresh_seconds,
        degraded=_degraded["value"] or not observed,
    )


@app.get("/api/status", response_model=StatusResponse)
def status(lat: float | None = None, lon: float | None = None, radius_km: float | None = None):
    lat = cfg.center_lat if lat is None else lat
    lon = cfg.center_lon if lon is None else lon
    radius_km = cfg.radius_km if radius_km is None else radius_km

    data = status_at(client, mapping, cfg, lat, lon, radius_km)
    v = data["verdict"]
    ne = v.get("next_event")
    return StatusResponse(
        center=Center(lat=lat, lon=lon),
        radius_km=radius_km,
        sampled_at=data["sampled_at"],
        latest_observed_time=data["latest_observed_time"],
        timeline=[TimelineEntry(**e) for e in data["timeline"]],
        current=TimelineEntry(**data["current"]),
        verdict=Verdict(status=v["status"], label=v["label"], detail=v["detail"],
                        next_event=NextEvent(**ne) if ne else None),
        category_meta=data["category_meta"],
        degraded=_degraded["value"] or not data["timeline"],
    )


@app.get("/api/img/{kind}/{filename}")
def img(kind: str, filename: str):
    if kind == "obs":
        if not OBS_RE.match(filename):
            raise HTTPException(status_code=400, detail="bad filename")
        try:
            data = client.get_observed_png(filename)
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=404, detail="not available")
    elif kind == "fct":
        if not FCT_PNG_RE.match(filename):
            raise HTTPException(status_code=400, detail="bad filename")
        try:
            data = client.get_forecast_png(filename)
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=404, detail="not available")
    else:
        raise HTTPException(status_code=400, detail="bad kind")
    return Response(content=data, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=300, immutable"})


# --- Static frontend ---------------------------------------------------------

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))
